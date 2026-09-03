#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборка эталонных локалей en/ru из одной таблицы.

Нужен только разработчику и только для двух локалей, которые едут в комплекте:
держать русский и английский в разных файлах, но править парой — так надёжнее,
чем сверять два JSON глазами. Локали сообщества этим скриптом НЕ трогаются,
они живут своей жизнью и проверяются через tools/lint_locales.py.

    python tools/build_locales.py
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOCALES = os.path.join(ROOT, "unity", "com.uasset.converter", "Editor", "Locales")

# ключ: (english, русский)
STRINGS = {
    # -- CLI / оркестратор ---------------------------------------------------
    "cli.error.no_engine": (
        "Unreal Engine installation not found — set paths.ue_engine_dir in the config",
        "не нашёл установленный Unreal Engine — укажи путь в конфиге"),
    "cli.error.no_editor_cmd": (
        "UnrealEditor-Cmd.exe is missing at {path}",
        "нет UnrealEditor-Cmd.exe по пути {path}"),
    "cli.error.no_uproject": (
        "no .uproject at {path}",
        "нет .uproject по пути {path}"),
    "cli.launching_unreal": (
        "Starting Unreal: {editor}",
        "Запускаю Unreal: {editor}"),
    "cli.stopped_by_user": (
        "Cancelled by user.",
        "Прервано пользователем."),
    "cli.unreal_exit": (
        "Unreal exited with code {code}. Full log: logs/ue_export.log",
        "Unreal завершился с кодом {code}. Полный лог: logs/ue_export.log"),
    "cli.step_missing": (
        "skipping {module} — the file does not exist yet",
        "пропускаю {module} — файла ещё нет"),
    "cli.error.required_assets": (
        "the 'levels' scope needs these asset types, but they are disabled: {types}. "
        "Enable them, or pass --allow-required-assets to run anyway.",
        "режиму «уровни» нужны эти типы ассетов, но они выключены: {types}. "
        "Включи их или добавь --allow-required-assets, чтобы запустить всё равно."),

    # -- GUI: каркас ---------------------------------------------------------
    "gui.title": (
        "Unreal to Unity asset converter",
        "Конвертер ассетов Unreal → Unity"),
    "gui.browse": ("Browse…", "Обзор…"),
    "gui.box.paths": ("Paths", "Пути"),
    "gui.box.export": ("What to export", "Что экспортировать"),
    "gui.box.options": ("Settings", "Настройки"),
    "gui.box.folders": ("Folders for the “Selected folders” scope",
                        "Папки для режима «Выбранные папки»"),
    "gui.box.log": ("Progress", "Ход работы"),

    "gui.path.project": ("Unreal project (.uproject)", "Проект Unreal (.uproject)"),
    "gui.path.engine": ("Engine folder", "Папка движка"),
    "gui.path.output": ("Output folder", "Папка вывода"),

    # -- GUI: что экспортировать --------------------------------------------
    "gui.export.textures": ("Textures", "Текстуры"),
    "gui.export.static_meshes": ("Static meshes", "Статик-меши"),
    "gui.export.skeletal_meshes": ("Skeletal meshes", "Скелетные меши"),
    "gui.export.animations": ("Animations", "Анимации"),
    "gui.export.materials": ("Materials", "Материалы"),
    "gui.export.material_graphs": ("Master material graphs (for shaders)",
                                   "Графы мастеров (для шейдеров)"),

    # -- GUI: настройки ------------------------------------------------------
    "gui.option.language": ("Language:", "Язык:"),
    "gui.option.scope": ("Conversion scope:", "Объём конвертации:"),
    "gui.option.flip_normal": ("Flip the green channel of normal maps",
                               "Переворачивать зелёный канал нормалей"),
    "gui.option.shadergraph": ("Materials as Shader Graph (URP)",
                               "Материалы как Shader Graph (URP)"),
    "gui.option.avatar": ("Avatar for skeletal meshes:", "Аватар для скелеток:"),

    "gui.scope.test": ("Test subset", "Тестовая выборка"),
    "gui.scope.selected": ("Selected folders", "Выбранные папки"),
    "gui.scope.all": ("Entire Content", "Весь Content"),
    "gui.scope.levels": ("Levels → Scenes", "Уровни → Сцены"),

    "gui.option.light_multiplier": ("Light intensity ×", "Множитель яркости света ×"),

    # -- GUI: папки ----------------------------------------------------------
    "gui.folders.reload": ("Refresh list", "Обновить список"),
    "gui.folders.clear": ("Clear all", "Снять все"),
    "gui.folders.no_content": ("no Content folder found", "папка Content не найдена"),
    "gui.folders.selected": ("selected: {count}", "выбрано: {count}"),
    "gui.folders.none_selected": (
        "nothing selected — include_paths from the config will be used",
        "ничего не выбрано — будет использован include_paths из конфига"),

    # -- GUI: уровни ---------------------------------------------------------
    "gui.box.levels": ("Levels for the “Levels → Scenes” scope",
                       "Уровни для режима «Уровни → Сцены»"),
    "gui.levels.reload": ("Refresh levels", "Обновить уровни"),
    "gui.levels.selected": ("levels selected: {count}", "выбрано уровней: {count}"),
    "gui.levels.none_selected": ("no levels selected", "уровни не выбраны"),
    "gui.levels.no_content": ("no Content folder found", "папка Content не найдена"),

    # -- GUI: действия -------------------------------------------------------
    "gui.action.scan": ("Scan", "Сканировать"),
    "gui.action.export": ("Export from Unreal", "Экспорт из Unreal"),
    "gui.action.shaders": ("Generate shaders", "Сгенерировать шейдеры"),
    "gui.action.shadergraph": ("Shader Graph", "Shader Graph"),
    "gui.action.postprocess": ("Post-process", "Постобработка"),
    "gui.action.all": ("Everything", "Всё целиком"),
    "gui.action.stop": ("Cancel", "Отмена"),
    "gui.action.open_output": ("Open output", "Открыть output"),
    "gui.action.install_package": ("Add package to Unity…", "Пакет в Unity…"),

    # -- GUI: статусы --------------------------------------------------------
    "gui.status.ready": ("Ready", "Готов к работе"),
    "gui.status.working": ("Working…", "Работаю…"),
    "gui.status.stopping": ("Stopping…", "Останавливаю…"),
    "gui.status.stopped": ("Stopped by user", "Остановлено пользователем"),
    "gui.status.step_done": ("Step “{step}” finished", "Шаг «{step}» завершён"),
    "gui.status.step_failed": ("Step “{step}” failed with code {code}",
                               "Шаг «{step}» завершился с кодом {code}"),
    "gui.status.aborted": ("Aborted because of an error", "Прервано из-за ошибки"),
    "gui.error.prefix": ("ERROR: {error}", "ОШИБКА: {error}"),
    "gui.progress.of": ("{done} of {total} — {label}", "{done} из {total} — {label}"),

    # -- GUI: диалоги выбора -------------------------------------------------
    "gui.pick.project": ("Pick a .uproject", "Выбери .uproject"),
    "gui.pick.project_filter": ("Unreal project", "Проект Unreal"),
    "gui.pick.engine": ("Installed Unreal Engine folder",
                        "Папка установленного Unreal Engine"),
    "gui.pick.output": ("Where to put the result", "Куда складывать результат"),
    "gui.pick.unity_project": ("Unity project folder (the one containing Assets)",
                               "Папка Unity-проекта (та, где лежит Assets)"),

    # -- GUI: разведка -------------------------------------------------------
    "gui.scan.header": ("— File survey —", "— Разведка по файлам —"),
    "gui.scan.content": ("Content: {path}", "Content: {path}"),
    "gui.scan.no_extension": ("(no extension)", "(без расширения)"),
    "gui.scan.total_gb": ("  {size} GB total", "  всего {size} ГБ"),
    "gui.scan.hint": ("Exact asset counts come from the “Export from Unreal” step.",
                      "Точные типы ассетов посчитает шаг «Экспорт из Unreal»."),
    "gui.scan.not_found_title": ("Not found", "Не найдено"),
    "gui.scan.not_found_body": ("There is no Content folder next to the .uproject",
                                "Нет папки Content рядом с .uproject"),

    # -- GUI: установка пакета ----------------------------------------------
    "gui.install.not_unity_title": ("Not a Unity project", "Это не проект Unity"),
    "gui.install.not_unity_body": (
        "The selected folder has no Packages/manifest.json",
        "В выбранной папке нет Packages/manifest.json"),
    "gui.install.no_package_title": ("Package not found", "Пакет не найден"),
    "gui.install.no_package_body": ("No package.json at {path}",
                                    "Нет package.json по пути {path}"),
    "gui.install.failed_title": ("Could not register the package",
                                 "Не удалось прописать пакет"),
    "gui.install.failed_body": ("{error}", "{error}"),
    "gui.install.registered": ("Package registered in {path}",
                               "Пакет прописан в {path}"),
    "gui.install.replaced": ("Previous reference replaced: {previous}",
                             "Прежняя ссылка заменена: {previous}"),
    "gui.install.done_title": ("Done", "Готово"),
    "gui.install.done_body": (
        "The package is registered. Unity will import it on the next focus.\n\n"
        "In Unity: Tools → Uasset Converter.",
        "Пакет прописан. Unity подхватит его при следующем переключении в редактор.\n\n"
        "В Unity: меню Tools → Uasset Converter."),

    # -- Экспорт из Unreal ---------------------------------------------------
    "export.scanning_registry": ("scanning the Asset Registry…",
                                 "сканирую Asset Registry..."),
    "export.test_subset": ("test subset with dependencies: {count} assets",
                           "тестовая выборка с зависимостями: {count} ассетов"),
    "export.queued": ("queued: {count} assets (scope={mode})",
                      "к обработке: {count} ассетов (режим scope={mode})"),
    "export.done": ("DONE. Manifest: {path}", "ГОТОВО. Манифест: {path}"),
    "export.error_on": ("ERROR on {path}: {error}", "ОШИБКА на {path}: {error}"),

    # -- Экспорт уровней -----------------------------------------------------
    "export.level_subset": (
        "levels: {levels}, assets to export with dependencies: {count}",
        "уровней: {levels}, ассетов к экспорту с зависимостями: {count}"),
    "export.level_collected": (
        "level {name}: {objects} objects, {lights} lights",
        "уровень {name}: {objects} объектов, {lights} источников света"),
    "export.level.skip.load_failed": (
        "the level could not be loaded",
        "уровень не удалось загрузить"),
    "export.level.skip.niagara": (
        "Niagara component — particle systems are not converted",
        "Компонент Niagara — системы частиц не конвертируются"),
    "export.level.skip.particles": (
        "cascade particle component — particle systems are not converted",
        "каскадный компонент частиц — системы частиц не конвертируются"),
    "export.level.skip.camera": (
        "camera component — cameras are not transferred to the scene",
        "компонент камеры — камеры в сцену не переносятся"),
    "export.level.skip.skylight": (
        "sky light — no direct counterpart, set up ambient lighting in Unity",
        "SkyLight — прямого аналога нет, окружающий свет настраивается в Unity"),
    "export.level.skip.audio": (
        "audio component — sound is out of scope",
        "аудиокомпонент — звук не входит в задачу"),
    "export.level.skip.landscape": (
        "landscape component — terrain is not converted",
        "компонент Landscape — ландшафт не конвертируется"),
    "export.level.skip.decal": (
        "decal component — decals are a separate mechanic in Unity",
        "компонент декали — декали в Unity отдельная механика"),

    # -- Экспорт: причины пропуска ------------------------------------------
    "export.skip.texture_exporter": (
        "the texture exporter produced no file (format {format})",
        "экспортёр текстуры не выдал файл (формат {format})"),
    "export.skip.no_source_art": (
        "source art was stripped from the asset — exported from platform data, "
        "quality is below the original",
        "source art вырезан — экспорт из платформенных данных, качество ниже оригинала"),
    "export.skip.static_mesh_exporter": ("StaticMeshExporterFBX produced no file",
                                         "StaticMeshExporterFBX не выдал файл"),
    "export.skip.skeletal_mesh_exporter": ("SkeletalMeshExporterFBX produced no file",
                                           "SkeletalMeshExporterFBX не выдал файл"),
    "export.skip.anim_exporter": ("AnimSequenceExporterFBX produced no file",
                                  "AnimSequenceExporterFBX не выдал файл"),
    "export.skip.unknown_class": ("class {cls} is not supported by the converter",
                                  "класс {cls} не поддерживается конвертером"),
    "export.skip.configured": ("excluded from transpilation in the config",
                               "исключён из транспиляции в конфиге"),
    "export.skip.configured_reason": ("{reason}", "{reason}"),
    "export.skip.function_unreadable": (
        "the expressions of this material function could not be read — "
        "calls to it stay approximate",
        "не удалось прочитать выражения этой функции материала — "
        "вызовы останутся приблизительными"),
    "export.skip.blueprint": (
        "Blueprints are logic, not art; they are rewritten by hand in Unity",
        "Блюпринты — это логика, а не арт; в Unity переписываются вручную"),
    "export.skip.world": (
        "levels are not converted; only the art they are built from is transferred",
        "Уровни не конвертируются; переносится только арт, из которого они собраны"),
    "export.skip.niagara_system": (
        "Niagara particle systems have no automatic counterpart in Unity",
        "Системы частиц Niagara не имеют автоматического аналога в Unity"),
    "export.skip.niagara_emitter": (
        "a Niagara emitter is part of a particle system and is useless on its own",
        "Эмиттер Niagara — часть системы частиц, отдельно бесполезен"),
    "export.skip.sound": ("audio is out of scope for art conversion",
                          "Звук не входит в задачу конвертации арта"),
    "export.skip.anim_blueprint": (
        "an Anim Blueprint is animation logic and is rewritten by hand",
        "Anim Blueprint — логика анимаций, переписывается вручную"),
    "export.skip.skeleton": (
        "the skeleton travels inside the skeletal mesh FBX, a separate file is not needed",
        "Скелет переносится внутри FBX скелетного меша, отдельным файлом не нужен"),
    "export.skip.physics_asset": (
        "UE physics bodies have no direct counterpart; colliders are set up in Unity",
        "Физические тела UE не имеют прямого аналога; коллайдеры настраиваются в Unity"),

    # -- Сводка экспорта -----------------------------------------------------
    "summary.textures": ("textures", "текстуры"),
    "summary.static_meshes": ("static meshes", "статик-меши"),
    "summary.skeletal_meshes": ("skeletal meshes", "скелетные меши"),
    "summary.animations": ("animations", "анимации"),
    "summary.materials": ("materials", "материалы"),
    "summary.material_graphs": ("material graphs", "графы материалов"),
    "summary.material_functions": ("material functions", "функции материалов"),
    "summary.levels": ("levels", "уровни"),
    "summary.skipped": ("skipped", "пропущено"),
    "summary.errors": ("errors", "ошибки"),

    # -- Генерация шейдеров --------------------------------------------------
    "shader.no_graphs": (
        "the manifest has no master material graphs — nothing to transpile",
        "в манифесте нет графов мастер-материалов — транспилировать нечего"),
    "shader.reused": ("{shader} (same graph, shader reused)",
                      "{shader} (тот же граф, шейдер переиспользован)"),
    "shader.node_and_property_count": ("{nodes} nodes, {properties} properties",
                                       "{nodes} нод, {properties} свойств"),
    "shader.todo_count": ("{count} TODO", "{count} TODO"),
    "shader.no_unity_equivalent": ("(no Unity equivalent: {names})",
                                   "(без аналога в Unity: {names})"),
    "shader.done": ("Done: {written} shaders written, {reused} reused, TODO: {todos}",
                    "Готово: {written} шейдеров записано, {reused} переиспользовано, TODO: {todos}"),
    "shader.index": ("Index: {path}", "Индекс: {path}"),
    "shader.unnamed": ("<unnamed>", "<без имени>"),
    "shader.helpers_header": ("Material functions carried over from Unreal",
                              "Перенесённые функции материалов Unreal"),
    "shader.comment.generated": (
        "Generated by the Unreal -> Unity converter. Edits are overwritten on rebuild.",
        "Сгенерировано конвертером Unreal -> Unity. Правки будут затёрты при пересборке."),
    "shader.comment.source": ("Source: {path}", "Источник: {path}"),
    "shader.comment.nodes": ("Unreal master material, {count} nodes in the graph",
                             "Мастер-материал Unreal, нод в графе: {count}"),
    "shader.comment.smoothness": (
        "Unreal stores roughness, Unity wants smoothness — this is the inversion.",
        "Unreal хранит roughness, Unity ждёт smoothness — это инверсия."),
    "shader.header_todo": (
        "WARNING: {count} node(s) were not transferred exactly — see unsupported.md",
        "ВНИМАНИЕ: {count} нод(ы) не перенесены точно — подробности в unsupported.md"),
    "shader.todo.unsupported_passthrough": (
        "node is not supported, input {input} passed through as is",
        "нода не поддерживается, вход {input} проброшен как есть"),
    "shader.todo.unsupported_no_inputs": (
        "node is not supported and has no inputs — 0 substituted",
        "нода не поддерживается и не имеет входов — подставлен 0"),
    "shader.todo.uv_set_clamped": (
        "UV set {requested} is not passed to the shader, UV{used} used instead",
        "UV-набор {requested} не передаётся в шейдер, взят UV{used}"),
    "shader.todo.function_not_ported": ("material function {path} was not ported",
                                        "функция материала {path} не перенесена"),

    # -- Постобработка -------------------------------------------------------
    "post.no_manifest": ("no {path} — run the export step first",
                         "нет {path} — сначала выполни шаг export"),
    "post.done": ("Done.", "Готово."),
    "post.summary.textures": ("textures:           {count} (+{generated} generated)",
                              "текстур:            {count} (+{generated} сгенерировано)"),
    "post.summary.static_meshes": ("static meshes:      {count}",
                                   "статик-мешей:       {count}"),
    "post.summary.skeletal_meshes": ("skeletal meshes:    {count}",
                                     "скелетных мешей:    {count}"),
    "post.summary.animations": ("animations:         {count}",
                                "анимаций:           {count}"),
    "post.summary.materials": (
        "materials:          {count} ({shader} on their own shader, {fallback} on Lit)",
        "материалов:         {count} ({shader} на своём шейдере, {fallback} на Lit)"),
    "post.summary.levels": ("levels:             {count}",
                            "уровней:            {count}"),
    "post.summary.manifest": ("manifest for Unity: {path}",
                              "манифест для Unity: {path}"),
    "post.summary.report": ("report:             {path}",
                            "отчёт:              {path}"),

    "post.loop.matched_loop": ("the name matched the loop pattern {pattern}",
                               "имя совпало с шаблоном цикла {pattern}"),
    "post.loop.matched_noloop": ("the name matched the non-loop pattern {pattern}",
                                 "имя совпало с шаблоном не-цикла {pattern}"),
    "post.loop.no_match": ("no pattern matched — looping left off",
                           "шаблоны не сработали — поставлен цикл выключен"),

    "post.note.no_source_art": (
        "source art was stripped from the asset — the export was made from platform "
        "data, quality is below the original",
        "source art вырезан из ассета — экспорт сделан из платформенных данных, "
        "качество ниже оригинала"),
    "post.note.unmatched_parameters": ("parameters with no match in the shader: {names}",
                                       "параметры без соответствия в шейдере: {names}"),
    "post.note.procedural_master": (
        "master {master} builds the surface procedurally (textures are used inside the "
        "graph rather than through parameters) — in Unity the material becomes a flat colour",
        "мастер {master} строит поверхность процедурно (текстуры используются внутри "
        "графа, а не через параметры) — в Unity материал станет плоским цветом"),
    "post.note.no_master": (
        "no master material found in the chain — values come only from instance overrides",
        "не найден мастер-материал в цепочке — значения взяты только из переопределений инстанса"),
    "post.note.loop_guess": ("looping was guessed heuristically ({why}) — check it in Unity",
                             "зацикливание определено эвристикой ({why}) — проверь в Unity"),
    "post.note.dropped_parameters": (
        "Unreal parameters with no input in the URP lighting models, values dropped: {names}",
        "параметры Unreal без входа в URP-модели освещения, значения отброшены: {names}"),

    # -- Постобработка: уровни ----------------------------------------------
    "post.level.file_missing": (
        "the level file {file} is listed in the manifest but not found on disk",
        "файл уровня {file} указан в манифесте, но не найден на диске"),
    "post.level.missing_ref": (
        "references a {kind} that was not exported: {path}",
        "ссылается на {kind}, который не экспортирован: {path}"),
    "post.level.skipped_component": (
        "component {component} was not transferred — {reason}",
        "компонент {component} не перенесён — {reason}"),
    "post.level.light_note": ("light: {note}", "свет: {note}"),
    "post.level.light_no_intensity": (
        "the light has no intensity value, brightness left at zero",
        "у источника нет значения интенсивности, яркость оставлена нулевой"),
    "post.level.light_lumens": (
        "intensity converted from lumens to candelas approximately",
        "интенсивность приблизительно пересчитана из люменов в канделы"),
    "post.level.light_unit_unknown": (
        "the intensity unit is not recognised, the raw number was kept",
        "единица интенсивности не распознана, взято исходное число"),

    # -- Отчёт unsupported.md ------------------------------------------------
    "section.textures": ("Textures", "Текстуры"),
    "section.materials": ("Materials", "Материалы"),
    "section.animations": ("Animations", "Анимации"),
    "section.shaders": ("Shaders", "Шейдеры"),
    "section.levels": ("Levels", "Уровни"),

    "report.title": ("What did not transfer automatically",
                     "Что не перенеслось автоматически"),
    "report.regenerated": ("This report is rebuilt on every post-processing run.",
                           "Отчёт собирается заново при каждом запуске постобработки."),
    "report.summary": ("Summary", "Сводка"),
    "report.column.category": ("Category", "Категория"),
    "report.column.count": ("Count", "Штук"),
    "report.row.materials_shader": ("Materials on a transpiled shader",
                                    "Материалов на транспилированном шейдере"),
    "report.row.materials_fallback": ("Materials on the pipeline default Lit",
                                      "Материалов на дефолтном Lit"),
    "report.row.textures_exported": ("Textures exported", "Текстур экспортировано"),
    "report.row.textures_generated": ("Textures generated (masks, normals)",
                                      "Текстур сгенерировано (маски, нормали)"),
    "report.row.static_meshes": ("Static meshes", "Статик-мешей"),
    "report.row.skeletal_meshes": ("Skeletal meshes", "Скелетных мешей"),
    "report.row.animations": ("Animations", "Анимаций"),
    "report.row.export_errors": ("Export errors", "Ошибок при экспорте"),
    "report.export_errors": ("Export errors", "Ошибки экспорта"),
    "report.skipped": ("Skipped assets", "Пропущенные ассеты"),
    "report.count_of": ("{count} item(s)", "{count} шт."),
    "report.and_more": ("…and {count} more", "…и ещё {count}"),
    "report.needs_check": ("{section} — needs checking", "{section} — требует проверки"),
    "report.inexact_shaders": ("Shaders with inexact transpilation",
                               "Шейдеры с неточной транспиляцией"),
    "report.todo_line": ("node `{type}` #{index} — {message}",
                         "нода `{type}` #{index} — {message}"),

    # -- Unity: общее --------------------------------------------------------
    "unity.browse": ("Browse…", "Обзор…"),
    "unity.log": ("Log", "Лог"),
    "unity.language": ("Language", "Язык"),

    # -- Unity: окно импорта -------------------------------------------------
    "unity.import.window_title": ("Uasset Import", "Импорт Uasset"),
    "unity.import.header": ("Import assets converted from Unreal",
                            "Импорт ассетов, сконвертированных из Unreal"),
    "unity.import.output_dir": ("Output folder", "Папка output"),
    "unity.import.pick_output": ("Converter output folder", "Папка output конвертера"),
    "unity.import.target_root": ("Where in the project", "Куда в проекте"),
    "unity.import.avatar": ("Skeletal avatar", "Аватар скелеток"),
    "unity.import.overwrite": ("Overwrite files", "Перезаписывать файлы"),
    "unity.import.run": ("Import", "Импортировать"),
    "unity.import.no_manifest": (
        "There is no unity_manifest.json in that folder.\n"
        "Run the “Export from Unreal” and “Post-process” steps in the converter first.",
        "В указанной папке нет unity_manifest.json.\n"
        "Сначала выполни в конвертере шаги «Экспорт из Unreal» и «Постобработка»."),
    "unity.import.manifest_unreadable": ("could not read the manifest: {error}",
                                         "не удалось прочитать манифест: {error}"),
    "unity.import.manifest_read": (
        "manifest read: {textures} textures, {meshes} meshes, {skeletal} skeletal, "
        "{clips} clips, {materials} materials",
        "манифест прочитан: {textures} текстур, {meshes} мешей, {skeletal} скелеток, "
        "{clips} клипов, {materials} материалов"),
    "unity.import.copied": ("files copied: {count}", "скопировано файлов: {count}"),
    "unity.import.step_textures": ("Configuring textures…", "Настройка текстур…"),
    "unity.import.step_materials": ("Creating materials…", "Создание материалов…"),
    "unity.import.step_models": ("Models…", "Модели…"),
    "unity.import.step_animations": ("Animations…", "Анимации…"),
    "unity.import.textures_done": ("textures configured: {count}",
                                   "настроено текстур: {count}"),
    "unity.import.materials_done": (
        "materials created: {count} (on their own shader {shader}, "
        "on the default Lit {fallback}, failed {failed})",
        "материалов создано: {count} (на своём шейдере {shader}, "
        "на дефолтном Lit {fallback}, не вышло {failed})"),
    "unity.import.meshes_done": ("static meshes configured: {count}",
                                 "настроено статик-мешей: {count}"),
    "unity.import.skeletal_done": ("skeletal meshes configured: {count}",
                                   "настроено скелетных мешей: {count}"),
    "unity.import.clips_done": ("clips configured: {count}", "настроено клипов: {count}"),
    "unity.import.finished": ("done", "готово"),
    "unity.import.aborted": ("import aborted: {error}", "импорт прерван: {error}"),
    "unity.import.source_missing": ("source file is missing: {path}",
                                    "нет исходного файла: {path}"),
    "unity.import.what": ("What to import", "Что импортировать"),
    "unity.import.select_all": ("Select all", "Выбрать всё"),
    "unity.import.select_none": ("Clear all", "Снять всё"),
    "unity.import.cat_textures": ("Textures ({count})", "Текстуры ({count})"),
    "unity.import.cat_materials": ("Materials ({count})", "Материалы ({count})"),
    "unity.import.cat_meshes": ("Static meshes ({count})", "Статик-меши ({count})"),
    "unity.import.cat_skeletal": ("Skeletal meshes ({count})", "Скелетные меши ({count})"),
    "unity.import.cat_animations": ("Animations ({count})", "Анимации ({count})"),
    "unity.import.step_skeletal": ("Skeletal meshes…", "Скелетные меши…"),
    "unity.import.nothing_selected": ("nothing selected to import",
                                      "не выбрано, что импортировать"),
    "unity.import.avatars_from_existing": (
        "animations without skeletal meshes: looking for avatars among already imported models",
        "анимации без скелетных мешей: аватары ищу среди уже импортированных моделей"),

    # -- Unity: окно сборки сцен из уровней ----------------------------------
    "unity.level.window_title": ("Uasset Levels", "Уровни Uasset"),
    "unity.level.header": ("Build Unity scenes from Unreal levels",
                           "Сборка сцен Unity из уровней Unreal"),
    "unity.level.hint": (
        "Import meshes and materials with “Import” first — this step only places "
        "already-imported assets into scenes.",
        "Сначала импортируй меши и материалы через «Импорт» — этот шаг только "
        "расставляет уже импортированные ассеты по сценам."),
    "unity.level.run": ("Build scenes", "Собрать сцены"),
    "unity.level.step": ("Building scenes…", "Сборка сцен…"),
    "unity.level.none": ("there are no levels in the manifest",
                         "в манифесте нет уровней"),
    "unity.level.file_missing": ("level file not found: {path}",
                                 "файл уровня не найден: {path}"),
    "unity.level.unreadable": ("could not read level {name}: {error}",
                               "не удалось прочитать уровень {name}: {error}"),
    "unity.level.schema_mismatch": (
        "level {name} skipped: schema version {got}, expected {want}",
        "уровень {name} пропущен: версия схемы {got}, ожидалась {want}"),
    "unity.level.mesh_missing": (
        "mesh {mesh} is not in the manifest — object skipped in {level}",
        "меша {mesh} нет в манифесте — объект пропущен в {level}"),
    "unity.level.model_missing": (
        "imported model not found: {path} — object skipped in {level}",
        "импортированная модель не найдена: {path} — объект пропущен в {level}"),
    "unity.level.override_missing": (
        "override material {material} not found in {level}",
        "материал-переопределение {material} не найден в {level}"),
    "unity.level.built": (
        "scene {name} built: {objects} objects, {lights} lights, {missing} missing",
        "сцена {name} собрана: объектов {objects}, светов {lights}, пропущено {missing}"),
    "unity.level.finished": ("scenes built: {count}", "собрано сцен: {count}"),
    "unity.level.exists_title": ("Scene already exists", "Сцена уже существует"),
    "unity.level.exists_body": ("Scene {path} already exists. Overwrite it?",
                                "Сцена {path} уже существует. Перезаписать?"),
    "unity.level.overwrite": ("Overwrite", "Перезаписать"),
    "unity.level.skip": ("Skip", "Пропустить"),
    "unity.level.skipped": ("level {name} skipped (scene not overwritten)",
                            "уровень {name} пропущен (сцена не перезаписана)"),

    # -- Unity: сообщения импортёров ----------------------------------------
    "unity.texture.no_importer": ("no texture importer for {path}",
                                  "нет импортёра текстуры для {path}"),
    "unity.model.no_importer": ("no model importer for {path}",
                                "нет импортёра модели для {path}"),
    "unity.model.slot_mismatch": (
        "{name}: renderer material count ({found}) does not match manifest slots "
        "({expected}) — materials bound by name only, some sections may stay default",
        "{name}: число материалов рендерера ({found}) не совпадает со слотами "
        "манифеста ({expected}) — привязка только по имени, часть секций может "
        "остаться с дефолтным материалом"),
    "unity.clip.no_importer": ("no importer for clip {path}",
                               "нет импортёра для клипа {path}"),
    "unity.clip.no_matching_skeleton": (
        "no skeletal mesh with the same skeleton was found for clip {clip} — "
        "the clip was imported as a separate Generic rig, bind it by hand",
        "для клипа {clip} не найден скелетный меш с тем же скелетом — "
        "клип импортирован как отдельный Generic-риг, привязку придётся задать вручную"),
    "unity.clip.no_avatar": ("model {model} has no avatar for clip {clip}",
                             "в модели {model} не оказалось аватара для клипа {clip}"),
    "unity.material.no_shader_at_all": (
        "neither shader {shader} nor the fallback {fallback} was found — skipping {path}",
        "не найден ни шейдер {shader}, ни запасной {fallback} — пропускаю {path}"),
    "unity.material.shader_missing": (
        "shader {shader} not found, {name} was built on {fallback}",
        "шейдер {shader} не найден, {name} собран на {fallback}"),
    "unity.material.texture_missing": ("texture not found: {path}",
                                       "текстура не найдена: {path}"),

    # -- Unity: окно конвертера ---------------------------------------------
    "unity.convert.window_title": ("Uasset Converter", "Конвертер Uasset"),
    "unity.convert.header": ("Convert assets straight from an Unreal project",
                             "Конвертация ассетов прямо из проекта Unreal"),
    "unity.convert.converter_dir": ("Converter folder", "Папка конвертера"),
    "unity.convert.pick_converter": ("Folder with convert.py", "Папка с convert.py"),
    "unity.convert.no_converter": (
        "convert.py was not found in that folder. Point at the folder where the "
        "converter is unpacked.",
        "В этой папке нет convert.py. Укажи папку, куда распакован конвертер."),
    "unity.convert.python": ("Python", "Python"),
    "unity.convert.python_autodetect": ("Detect", "Найти"),
    "unity.convert.python_found": ("Python found: {path}", "Python найден: {path}"),
    "unity.convert.python_not_found": (
        "Python 3 was not found. Install it from python.org and press “Detect” again, "
        "or type the path to python.exe by hand.",
        "Python 3 не найден. Установи его с python.org и нажми «Найти» ещё раз "
        "или впиши путь к python.exe вручную."),
    "unity.convert.deps_missing": (
        "Python is there, but the packages are missing: {packages}. Run:\n{command}",
        "Python есть, но не хватает пакетов: {packages}. Выполни:\n{command}"),
    "unity.convert.deps_ok": ("Python and its packages are in place",
                              "Python и пакеты на месте"),
    "unity.convert.section_paths": ("Paths", "Пути"),
    "unity.convert.section_what": ("What to convert", "Что конвертировать"),
    "unity.convert.section_folders": ("Folders", "Папки"),
    "unity.convert.section_run": ("Run", "Запуск"),
    "unity.convert.reload_config": ("Reload settings", "Перечитать настройки"),
    "unity.convert.refresh_folders": ("Refresh folder list", "Обновить список папок"),
    "unity.convert.folders_hint": (
        "Folders matter only for the “Selected folders” scope.",
        "Папки нужны только для режима «Выбранные папки»."),
    "unity.convert.step_export": ("Export from Unreal", "Экспорт из Unreal"),
    "unity.convert.step_shaders": ("Shaders", "Шейдеры"),
    "unity.convert.step_shadergraph": ("Shader Graph", "Shader Graph"),
    "unity.convert.step_postprocess": ("Post-process", "Постобработка"),
    "unity.convert.step_all": ("Everything", "Всё целиком"),
    "unity.convert.convert_and_import": ("Convert and import",
                                         "Конвертировать и импортировать"),
    "unity.convert.cancel": ("Cancel", "Отмена"),
    "unity.convert.import_only": ("Import only", "Только импорт"),
    "unity.convert.running": ("Running: {step}", "Выполняется: {step}"),
    "unity.convert.finished": ("Step {step} finished", "Шаг {step} завершён"),
    "unity.convert.failed": ("Step {step} failed with code {code}",
                             "Шаг {step} завершился с кодом {code}"),
    "unity.convert.cancelled": ("Cancelled", "Отменено"),
    "unity.convert.starting_import": ("Conversion finished, starting the import",
                                      "Конвертация закончена, запускаю импорт"),
    "unity.convert.config_saved": ("Settings saved to config.json",
                                   "Настройки сохранены в config.json"),
    "unity.convert.config_failed": ("Could not read the settings: {error}",
                                    "Не удалось прочитать настройки: {error}"),
    "unity.convert.no_output_yet": (
        "The converter has not produced unity_manifest.json yet — run the conversion.",
        "Конвертер ещё не создал unity_manifest.json — запусти конвертацию."),

    # -- Пакетные проверки ---------------------------------------------------
    # Пояснительный текст локализуется, машинные маркеры — нет: строку
    # RESULT: OK и префикс [UASSET-VERIFY] грепает CI, и они обязаны остаться
    # неизменными на любом языке.
    "verify.no_output_arg": ("-uassetOutput was not passed",
                             "не передан -uassetOutput"),
    "verify.no_manifest": ("no manifest at {path}", "нет манифеста {path}"),
    "verify.manifest_unreadable": ("manifest is unreadable: {error}",
                                   "манифест не читается: {error}"),
    "verify.active_pipeline": ("active RenderPipeline: {pipeline}",
                               "активный RenderPipeline: {pipeline}"),
    "verify.textures_configured": ("textures configured: {count}",
                                   "настроено текстур: {count}"),
    "verify.materials": (
        "materials: {count} (own shader {shader}, Lit {fallback}, failed {failed})",
        "материалов: {count} (свой шейдер {shader}, Lit {fallback}, не вышло {failed})"),
    "verify.meshes": ("meshes: {meshes}, skeletal: {skeletal}, clips: {clips}",
                      "меши: {meshes}, скелетки: {skeletal}, клипы: {clips}"),
    "verify.import_log": ("---- IMPORT LOG ----", "---- ЛОГ ИМПОРТА ----"),
    "verify.summary": ("---- SUMMARY ----", "---- ИТОГ ----"),
    "verify.shaders_with_errors": ("shaders with import errors: {count}",
                                   "шейдеров с ошибками импорта: {count}"),
    "verify.hlsl_not_checked": (
        "HLSL compilation is NOT checked here — that needs a render pass ({method})",
        "компиляция HLSL здесь НЕ проверяется — для этого нужен рендер ({method})"),
    "verify.materials_without_shader": ("materials without a shader: {count}",
                                        "материалов без шейдера: {count}"),
    "verify.models_unbound": ("models with unbound materials: {count}",
                              "моделей с непривязанными материалами: {count}"),
    "verify.files_copied": ("files copied: {count}", "скопировано файлов: {count}"),

    "verify.shader.failed_to_load": ("SHADER FAILED TO LOAD: {path}",
                                     "ШЕЙДЕР НЕ ЗАГРУЗИЛСЯ: {path}"),
    "verify.shader.errors_in": ("ERRORS in {shader}:", "ОШИБКИ в {shader}:"),
    "verify.shader.error_line": ("    line {line}: {message}",
                                 "    строка {line}: {message}"),
    "verify.shader.imported": ("imported      {shader}", "импортирован  {shader}"),
    "verify.shader.warnings": ("  (warnings: {count})", "  (предупреждений: {count})"),
    "verify.shader.find_returned_null": (
        "    Shader.Find({shader}) returned null",
        "    Shader.Find({shader}) вернул null"),
    "verify.shader.compiled": ("  compiled: {shader}", "  скомпилирован: {shader}"),

    "verify.binding.model_failed": ("model failed to load: {path}",
                                    "модель не загрузилась: {path}"),
    "verify.binding.empty_slot": ("  {model}: empty material slot",
                                  "  {model}: пустой слот материала"),
    "verify.binding.foreign_material": ("  {model}: slot holds a foreign material {material}",
                                        "  {model}: слот занят чужим материалом {material}"),
    "verify.binding.no_renderers": ("  {model}: model has no renderers",
                                    "  {model}: у модели нет рендереров"),
    "verify.binding.ok": ("  binding OK: {model} ({count} slot(s))",
                          "  привязка OK: {model} ({count} слот(ов))"),
    "verify.material.not_created": ("material was not created: {path}",
                                    "материал не создан: {path}"),
    "verify.material.broken_shader": ("material with a broken shader: {path}",
                                      "материал с битым шейдером: {path}"),

    "verify.render.screenshot_saved": ("screenshot saved: {path}",
                                       "снимок сохранён: {path}"),
    "verify.render.models_screenshot": ("model screenshot: {path} ({count} items)",
                                        "снимок моделей: {path} ({count} шт.)"),
    "verify.render.no_models": ("no models found to photograph",
                                "моделей для съёмки не нашлось"),
    "verify.render.objects_in_frame": ("objects in frame: {count}",
                                       "объектов в кадре: {count}"),
    "verify.render.shaders_with_errors": ("shaders with errors: {count}",
                                          "шейдеров с ошибками: {count}"),
    "verify.render.material_row": (
        "  {material} shader={shader} supported={supported} messages={messages}",
        "  {material} шейдер={shader} поддерживается={supported} сообщений={messages}"),
    "verify.render.no_shader": ("<no shader>", "<нет шейдера>"),
    "verify.render.failure": ("failure: {error}", "провал: {error}"),
}

LANGUAGES = {
    "en": {"index": 0, "name": "English", "fallback": "en"},
    "ru": {"index": 1, "name": "Русский", "fallback": "en"},
}


def build():
    os.makedirs(LOCALES, exist_ok=True)
    for code, meta in LANGUAGES.items():
        data = {
            "code": code,
            "name": meta["name"],
            "fallback": meta["fallback"],
            "authors": ["Uasset Converter"],
            "entries": [{"k": key, "v": STRINGS[key][meta["index"]]}
                        for key in sorted(STRINGS)],
        }
        path = os.path.join(LOCALES, "%s.json" % code)
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print("%s: %d ключей" % (path, len(data["entries"])))


if __name__ == "__main__":
    build()
