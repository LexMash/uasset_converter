# -*- coding: utf-8 -*-
"""
Локализация питоновской стороны конвертера.

Файлы локалей общие с Unity-пакетом и лежат в одном месте — переводчику не
нужно знать, что сторон две. Формат специально плоский (массив пар вместо
словаря), потому что JsonUtility в Unity словари не умеет, а тащить туда
сторонний JSON-парсер ради переводов не хочется.

Использование:

    from i18n import t, set_language
    set_language("en")
    print(t("cli.done", step="export"))

Неизвестный ключ не роняет конвертацию: возвращается запасной язык, а если и
там пусто — сам ключ. Сломанный перевод от сообщества не должен стоить
пользователю часа экспорта.
"""
import json
import locale
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LOCALES_DIR = os.path.join(HERE, "unity", "com.uasset.converter", "Editor", "Locales")

DEFAULT_LANGUAGE = "en"

_cache = {}          # код языка -> {ключ: строка}
_meta = {}           # код языка -> {"name": ..., "fallback": ..., "authors": [...]}
_current = DEFAULT_LANGUAGE
_missing = set()     # ключи, которых не нашлось — чтобы не спамить один и тот же


def locales_dir():
    return LOCALES_DIR


def _read(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def available():
    """
    Список локалей: [(код, человеческое имя), ...].
    Читается сканом папки — чтобы добавить язык, достаточно положить файл.
    """
    result = []
    if not os.path.isdir(LOCALES_DIR):
        return result
    for name in sorted(os.listdir(LOCALES_DIR)):
        if not name.endswith(".json"):
            continue
        code = name[:-5]
        try:
            data = _read(os.path.join(LOCALES_DIR, name))
        except (OSError, ValueError):
            continue
        result.append((code, data.get("name") or code))
    return result


def load(code):
    """Загружает локаль в кэш и возвращает словарь ключ -> строка."""
    if code in _cache:
        return _cache[code]

    path = os.path.join(LOCALES_DIR, "%s.json" % code)
    if not os.path.isfile(path):
        _cache[code] = {}
        _meta[code] = {}
        return _cache[code]

    try:
        data = _read(path)
    except (OSError, ValueError) as error:
        # Битый файл локали — не повод падать: скажем об этом и поедем дальше.
        print("locale %s could not be read (%s), falling back to %s"
              % (code, error, DEFAULT_LANGUAGE))
        _cache[code] = {}
        _meta[code] = {}
        return _cache[code]

    _cache[code] = {entry["k"]: entry["v"] for entry in data.get("entries", [])
                    if entry.get("k")}
    _meta[code] = {"name": data.get("name") or code,
                   "fallback": data.get("fallback") or DEFAULT_LANGUAGE,
                   "authors": data.get("authors") or []}
    return _cache[code]


def detect_system_language():
    """Код языка ОС, если такая локаль у нас есть; иначе запасной."""
    codes = {code for code, _ in available()}
    for tag in _system_language_tags():
        # 'ru_RU' -> пробуем 'ru_RU', потом 'ru'
        tag = tag.replace("-", "_")
        for candidate in (tag, tag.split("_")[0]):
            if candidate in codes:
                return candidate
    return DEFAULT_LANGUAGE


# Windows отдаёт локаль человеческим именем ('Russian_Russia'), а не тегом,
# поэтому одного источника мало: перебираем все, что есть.
_WINDOWS_NAMES = {
    "russian": "ru", "english": "en", "german": "de", "french": "fr",
    "spanish": "es", "portuguese": "pt", "italian": "it", "polish": "pl",
    "turkish": "tr", "japanese": "ja", "korean": "ko", "chinese": "zh-Hans",
    "ukrainian": "uk", "belarusian": "be",
}


def _system_language_tags():
    tags = []
    for source in (lambda: locale.getlocale()[0],
                   lambda: getattr(locale, "getdefaultlocale", lambda: (None,))()[0],
                   lambda: os.environ.get("LANG")):
        try:
            value = source()
        except (ValueError, AttributeError, TypeError, IndexError):
            continue
        if not value:
            continue
        value = str(value).split(".")[0]
        tags.append(value)
        mapped = _WINDOWS_NAMES.get(value.split("_")[0].lower())
        if mapped:
            tags.append(mapped)
    return tags


def set_language(code):
    """'auto' — определить по системе. Возвращает фактически выбранный код."""
    global _current
    if not code or code == "auto":
        code = detect_system_language()
    load(code)
    _current = code
    return _current


def current_language():
    return _current


def language_name(code):
    load(code)
    return (_meta.get(code) or {}).get("name") or code


def t(key, **kwargs):
    """
    Строка по ключу с подстановкой именованных параметров.

    Параметры именованные, а не позиционные, осознанно: в разных языках порядок
    слов разный, и переводчик обязан иметь право переставить {count} и {total}
    местами, не ломая вывод.
    """
    text = _lookup(key)
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        # Переводчик ошибся в плейсхолдере. Показываем хоть что-то осмысленное,
        # а не роняем шаг конвертации на форматировании строки.
        _warn_once("format|" + key, "broken placeholder in the translation of %r" % key)
        return _lookup(key, force_default=True).format(**kwargs)


def _lookup(key, force_default=False):
    if not force_default:
        text = load(_current).get(key)
        if text:
            return text

        fallback = (_meta.get(_current) or {}).get("fallback") or DEFAULT_LANGUAGE
        if fallback != _current:
            text = load(fallback).get(key)
            if text:
                return text

    text = load(DEFAULT_LANGUAGE).get(key)
    if text:
        return text

    # По-английски и мимо локализации: сообщение о ненайденном ключе не
    # должно само зависеть от того, найдётся ли ключ.
    _warn_once(key, "no translation for key %r" % key)
    return key


def _warn_once(token, message):
    if token in _missing:
        return
    _missing.add(token)
    print(message)
