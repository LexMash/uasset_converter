#!/usr/bin/env python3
"""
Конвертер ассетов Unreal -> Unity.

Без аргументов запускает GUI. С --cli работает из консоли:

    python convert.py --cli --step export
    python convert.py --cli --step all --scope test --lang en

Служебные команды нужны Unity-окну, чтобы не разбирать config.json на C#:

    python convert.py --config-dump          плоский key=value
    python convert.py --config-set a.b=value
    python convert.py --scan-folders         папки /Game для выбора объёма
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

from i18n import t, set_language

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
DEFAULT_CONFIG_PATH = os.path.join(HERE, "config.default.json")

# Windows-консоль по умолчанию не в utf-8 и превращает лог в кашу.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

STEPS = ("export", "shaders", "postprocess", "all")

# Папки Content, которые в дереве выбора показывать бессмысленно.
SKIPPED_CONTENT_DIRS = {"__ExternalActors__", "__ExternalObjects__", "Collections",
                        "Developers", "Localization"}


# ----------------------------------------------------------------------------
# Конфиг
# ----------------------------------------------------------------------------

def load_config(path=CONFIG_PATH):
    # Живого конфига может не быть: он не хранится в репозитории, потому что
    # содержит абсолютные пути конкретного пользователя.
    if not os.path.isfile(path) and os.path.isfile(DEFAULT_CONFIG_PATH):
        shutil.copyfile(DEFAULT_CONFIG_PATH, path)

    with open(path, "r", encoding="utf-8") as fh:
        config = json.load(fh)

    # Конфиг мог остаться от старой версии — добираем ключи, появившиеся позже,
    # чтобы обращение к ним не роняло шаг на KeyError.
    if os.path.isfile(DEFAULT_CONFIG_PATH):
        with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as fh:
            _fill_missing(config, json.load(fh))
    return config


def _fill_missing(target, defaults):
    """Рекурсивно добавляет отсутствующие ключи, ничего не перезаписывая."""
    for key, value in defaults.items():
        if key not in target:
            target[key] = value
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            _fill_missing(target[key], value)


def save_config(config, path=CONFIG_PATH):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2, ensure_ascii=False)


def apply_language(config):
    """Ставит язык из конфига и возвращает фактически выбранный код."""
    return set_language(config.get("language") or "auto")


def find_engine_dir(config):
    """Папка движка: из конфига, иначе из реестра Windows."""
    configured = config["paths"].get("ue_engine_dir")
    if configured and os.path.isdir(configured):
        return configured

    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"SOFTWARE\Epic Games\Unreal Engine\Builds")
        index = 0
        while True:
            try:
                _, value, _ = winreg.EnumValue(key, index)
            except OSError:
                break
            path = value.replace("/", os.sep)
            if os.path.isdir(path):
                return path
            index += 1
    except Exception:
        pass
    return None


def editor_cmd_path(engine_dir):
    return os.path.join(engine_dir, "Engine", "Binaries", "Win64", "UnrealEditor-Cmd.exe")


def list_content_folders(config, depth=2):
    """
    Два уровня папок Content в виде путей /Game/...

    Обходим файлы на диске, а не Asset Registry: выбрать объём конвертации надо
    ДО долгого шага экспорта, то есть без запуска Unreal. Используется и
    tkinter-GUI, и окном в Unity — правила отбора должны быть одни.
    """
    project = config["paths"].get("ue_project") or ""
    content = os.path.join(os.path.dirname(project), "Content")
    if not os.path.isdir(content):
        return None

    result = []
    try:
        tops = sorted(os.listdir(content))
    except OSError:
        return None

    for top in tops:
        top_path = os.path.join(content, top)
        if not os.path.isdir(top_path) or top in SKIPPED_CONTENT_DIRS:
            continue
        result.append("/Game/" + top)
        if depth < 2:
            continue
        try:
            children = sorted(os.listdir(top_path))
        except OSError:
            continue
        for child in children:
            if os.path.isdir(os.path.join(top_path, child)):
                result.append("/Game/%s/%s" % (top, child))
    return result


def list_umap_files(config):
    """
    Все .umap проекта в виде путей /Game/..., собранные рекурсивным обходом
    диска (без запуска Unreal — выбрать уровни надо ДО долгого экспорта).
    Общая функция для tkinter-GUI и окна в Unity: список должен быть один.
    """
    project = config["paths"].get("ue_project") or ""
    content = os.path.join(os.path.dirname(project), "Content")
    if not os.path.isdir(content):
        return None

    result = []
    for root_dir, dirs, files in os.walk(content):
        dirs[:] = [d for d in dirs if d not in SKIPPED_CONTENT_DIRS]
        for name in files:
            if not name.lower().endswith(".umap"):
                continue
            rel = os.path.relpath(os.path.join(root_dir, name), content)
            rel = os.path.splitext(rel)[0].replace(os.sep, "/")
            result.append("/Game/" + rel)
    return sorted(result)


# Типы ассетов, без которых уровень в Unity бессмысленен: меши не на что
# ссылаться, материалы и текстуры не из чего собирать сцену.
REQUIRED_FOR_LEVELS = ("static_meshes", "materials", "textures")


def missing_required_assets(config):
    """Список выключенных обязательных для режима levels типов ассетов."""
    export = config.get("export", {})
    return [key for key in REQUIRED_FOR_LEVELS if not export.get(key, True)]


# ----------------------------------------------------------------------------
# Запуск шагов
# ----------------------------------------------------------------------------

_PROGRESS_RE = re.compile(r"UAC\|PROGRESS\|(\d+)/(\d+)\|(.*)")
_LOG_RE = re.compile(r"UAC\|(?!PROGRESS)(.*)")


def run_unreal_export(config, on_line=None, on_progress=None, should_stop=None):
    """
    Гоняет ue_export.py внутри UnrealEditor-Cmd.
    Возвращает код возврата. on_line получает только осмысленные строки —
    лог движка на несколько тысяч строк в интерфейс не тащим.
    """
    engine_dir = find_engine_dir(config)
    if not engine_dir:
        raise RuntimeError(t("cli.error.no_engine"))

    editor = editor_cmd_path(engine_dir)
    if not os.path.isfile(editor):
        raise RuntimeError(t("cli.error.no_editor_cmd", path=editor))

    project = config["paths"]["ue_project"]
    if not os.path.isfile(project):
        raise RuntimeError(t("cli.error.no_uproject", path=project))

    script = os.path.join(HERE, "ue_export.py")
    env = dict(os.environ, UASSET_CONV_CONFIG=CONFIG_PATH, PYTHONIOENCODING="utf-8")

    # -ExecutePythonScript, а НЕ -run=pythonscript: коммандлет поднимает движок
    # без рендера, и экспортёр скелетных мешей падает ассертом MeshObject.
    # Полный редактор с -RenderOffScreen даёт живой RHI без окна на экране.
    command = [
        editor, project,
        "-ExecutePythonScript=%s" % script,
        "-EnablePlugins=PythonScriptPlugin",
        "-RenderOffScreen",          # RHI жив, окна нет
        "-ddc=NoZenLocalFallback",   # иначе движок минуту долбится в незапущенный ZenServer
        "-unattended", "-nosplash", "-nopause",
        "-stdout", "-FullStdOutLogOutput", "-NoLogTimes",
    ]

    emit = on_line or (lambda text: print(text))
    emit(t("cli.launching_unreal", editor=os.path.basename(editor)))

    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    raw_log = open(os.path.join(HERE, "logs", "ue_export.log"), "w", encoding="utf-8", errors="replace")

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               env=env, text=True, encoding="utf-8", errors="replace",
                               bufsize=1)
    try:
        for line in process.stdout:
            raw_log.write(line)
            if should_stop is not None and should_stop():
                process.terminate()
                emit(t("cli.stopped_by_user"))
                break

            match = _PROGRESS_RE.search(line)
            if match:
                done, total, label = int(match.group(1)), int(match.group(2)), match.group(3)
                if on_progress:
                    on_progress(done, total, label)
                continue
            match = _LOG_RE.search(line)
            if match:
                emit(match.group(1).strip())
            elif "Error:" in line and "LogPython" in line:
                emit(line.strip())
    finally:
        raw_log.close()

    process.wait()
    emit(t("cli.unreal_exit", code=process.returncode))
    return process.returncode


def run_python_step(module, config, on_line=None):
    """Запускает shader_gen.py / postprocess.py тем же интерпретатором."""
    emit = on_line or (lambda text: print(text))
    script = os.path.join(HERE, module)
    if not os.path.isfile(script):
        emit(t("cli.step_missing", module=module))
        return 0

    # Дочерний Python по умолчанию пишет в кодировке системной локали, а мы
    # читаем поток как utf-8 — без этой переменной лог превращается в кашу.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    process = subprocess.Popen([sys.executable, script, "--config", CONFIG_PATH],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               env=env, text=True, encoding="utf-8",
                               errors="replace", bufsize=1)
    for line in process.stdout:
        emit(line.rstrip())
    process.wait()
    return process.returncode


def run_step(step, config, on_line=None, on_progress=None, should_stop=None):
    code = 0
    if step in ("export", "all"):
        code = run_unreal_export(config, on_line, on_progress, should_stop)
        if code != 0:
            return code
    if step in ("shaders", "all"):
        code = run_python_step("shader_gen.py", config, on_line)
        if code != 0:
            return code
    if step in ("postprocess", "all"):
        code = run_python_step("postprocess.py", config, on_line)
    return code


# ----------------------------------------------------------------------------
# Машинный вывод для Unity
# ----------------------------------------------------------------------------
# Окно в Unity читает stdout построчно. Чтобы на C# не появилось второго
# формата, весь машинный вывод идёт тем же протоколом UAC|, что уже использует
# ue_export.py внутри движка.

def _porcelain_emit(text):
    for line in str(text).splitlines() or [""]:
        print("UAC|LOG|%s" % line, flush=True)


def _porcelain_progress(done, total, label):
    print("UAC|PROGRESS|%d/%d|%s" % (done, total, label), flush=True)


# ----------------------------------------------------------------------------
# Служебные команды над конфигом
# ----------------------------------------------------------------------------

def flatten_config(config, prefix=""):
    """{'paths': {'python': 'x'}} -> [('paths.python', 'x')]. Списки — через ';'."""
    rows = []
    for key, value in config.items():
        if key.startswith("_"):
            continue
        path = "%s.%s" % (prefix, key) if prefix else key
        if isinstance(value, dict):
            rows.extend(flatten_config(value, path))
        elif isinstance(value, list):
            if all(isinstance(item, (str, int, float, bool)) for item in value):
                rows.append((path, ";".join(str(item) for item in value)))
        elif isinstance(value, bool):
            rows.append((path, "true" if value else "false"))
        else:
            rows.append((path, "" if value is None else str(value)))
    return rows


def set_config_value(config, dotted, raw):
    """
    Ставит значение по точечному пути, сохраняя тип того, что там лежало.
    Тип берём из текущего значения, а не угадываем из текста: иначе путь
    "2024" превратился бы в число, а флаг из "false" — в непустую строку.
    """
    parts = dotted.split(".")
    node = config
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]

    key = parts[-1]
    current = node.get(key)
    if isinstance(current, bool):
        node[key] = raw.strip().lower() in ("1", "true", "yes", "on")
    elif isinstance(current, int) and not isinstance(current, bool):
        node[key] = int(float(raw))
    elif isinstance(current, float):
        node[key] = float(raw)
    elif isinstance(current, list):
        node[key] = [item for item in raw.split(";") if item]
    else:
        node[key] = raw
    return node[key]


# ----------------------------------------------------------------------------
# Точка входа
# ----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Unreal -> Unity asset converter")
    parser.add_argument("--cli", action="store_true", help="run in console, no GUI")
    parser.add_argument("--step", choices=STEPS, default="all", help="which step to run")
    parser.add_argument("--scope", choices=("test", "selected", "all"),
                        help="override scope.mode from the config")
    parser.add_argument("--lang", help="interface language code, or 'auto'")
    parser.add_argument("--porcelain", action="store_true",
                        help="machine-readable output (UAC| protocol) for the Unity window")
    parser.add_argument("--config-dump", action="store_true",
                        help="print the config as flat key=value lines")
    parser.add_argument("--config-set", action="append", metavar="KEY=VALUE",
                        help="set a config value by dotted path; repeatable")
    parser.add_argument("--scan-folders", action="store_true",
                        help="print /Game folders available for the 'selected' scope")
    parser.add_argument("--scan-levels", action="store_true",
                        help="print /Game .umap levels available for the 'levels' scope")
    parser.add_argument("--allow-required-assets", action="store_true",
                        help="in 'levels' scope, run even if required asset types are disabled")
    parser.add_argument("--list-languages", action="store_true",
                        help="print available interface languages as code=name")
    args = parser.parse_args()

    config = load_config()
    if args.lang:
        config["language"] = args.lang
    apply_language(config)

    if args.list_languages:
        from i18n import available
        for code, name in available():
            print("%s=%s" % (code, name))
        return 0

    if args.config_set:
        for pair in args.config_set:
            if "=" not in pair:
                print("UAC|LOG|--config-set ждёт KEY=VALUE, получил %r" % pair)
                return 2
            dotted, raw = pair.split("=", 1)
            set_config_value(config, dotted.strip(), raw)
        save_config(config)
        if not (args.config_dump or args.scan_folders or args.cli):
            return 0

    if args.config_dump:
        for key, value in flatten_config(config):
            print("%s=%s" % (key, value))
        return 0

    if args.scan_folders:
        folders = list_content_folders(config)
        if folders is None:
            print("UAC|LOG|%s" % t("gui.folders.no_content"))
            return 1
        for folder in folders:
            print(folder)
        return 0

    if args.scan_levels:
        levels = list_umap_files(config)
        if levels is None:
            print("UAC|LOG|%s" % t("gui.folders.no_content"))
            return 1
        for level in levels:
            print(level)
        return 0

    if args.scope:
        config["scope"]["mode"] = args.scope
        save_config(config)

    # В режиме levels меши/материалы/текстуры обязательны: без них сцену не
    # собрать. Останавливаемся с подсказкой, если их выключили без явного
    # разрешения --allow-required-assets.
    if (config["scope"].get("mode") == "levels"
            and args.step in ("export", "all") and not args.allow_required_assets):
        missing = missing_required_assets(config)
        if missing:
            message = t("cli.error.required_assets", types=", ".join(missing))
            if args.porcelain:
                _porcelain_emit(message)
                print("UAC|DONE|2", flush=True)
            else:
                print(message)
            return 2

    if args.porcelain:
        code = run_step(args.step, config,
                        on_line=_porcelain_emit, on_progress=_porcelain_progress)
        print("UAC|DONE|%d" % code, flush=True)
        return code

    if args.cli:
        return run_step(args.step, config)

    import gui
    return gui.launch(config)


if __name__ == "__main__":
    sys.exit(main() or 0)
