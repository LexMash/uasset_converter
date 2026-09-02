# Unity CLI: шейдеры, Shader Graph и запуск редакторного C#-кода — исследовательский отчёт

**Дата исследования:** актуально на 21 августа 2026 (последний релиз Unity CLI `1.0.0-beta.6`).
**Метод:** web_search был недоступен (в окружении нет API-ключа `DEEPSEEK_API_KEY`, вызовы возвращали ошибку), поэтому все страницы документации прочитаны напрямую (HTTP GET скриптами Python, все URL ниже проверены, ответ `200`). Список страниц взят из официального sitemap (`https://docs.unity.com/sitemap/unity-cli.xml`): раздел **unity-cli** содержит ровно **6 уникальных страниц** — подстраниц `/user-guide`, `/examples`, `/concepts`, `/admin` **не существует**.

**Обозначения источников:**
- **(а) официальная документация** — страницы docs.unity.com / docs.unity3d.com, прочитанные и проверенные в этой сессии;
- **(б) форумы/блоги** — не использовались (все утверждения взяты из официальных страниц);
- **(в) мои выводы/реконструкция** — помечены явно, где нет прямого текста документации.

---

## 0. Главное, что нужно знать сразу (чтобы не путать инструменты)

1. **«unity-cli» — это не «аргументы командной строки редактора».** Это отдельный standalone-бинарник от Unity для установки/управления редакторами и модулями с терминала. **Имя исполняемого файла — `unity`**, а имя пакета/формулы — `unity-cli`. ([intro](https://docs.unity.com/en-us/unity-cli/unity-cli), [use](https://docs.unity.com/en-us/unity-cli/use-unity-cli))
2. Инструмент **экспериментальный** («The Unity CLI is experimental. The features and documentation might change in an upcoming release») — это указано на каждой странице. ([main](https://docs.unity.com/en-us/unity-cli))
3. Сам unity-cli **не умеет напрямую выполнять код внутри редактора**. Чтобы CLI управлял редактором (команды, eval, batch-запуски с C#-кодом), нужно установить **Unity Pipeline package** (`com.unity.pipeline`) — локальный HTTP-сервер внутри редактора. Это ключевое ограничение для всех вопросов про C#-код и шейдеры. ([main](https://docs.unity.com/en-us/unity-cli), [pipeline](https://docs.unity.com/en-us/unity-production-pipeline/local-tools-cli/unity-pipeline-package))
4. **Шейдерных/Shader Graph-команд в unity-cli нет вообще** (проверено grep'ом по всем страницам раздела: 0 упоминаний «shader»/«ShaderGraph»). В пакете Unity Pipeline есть только **интроспективные** команды по материалам/шейдерам (`list_shaders`, `get_shader_properties`, `set_material_properties`, `get_material_properties`) и **общие** ассет-команды `import_asset` / `set_import_settings`, через которые можно переимпортировать любой ассет, включая `.shadergraph`. Подробно — в разделах 2 и 4.

---

## 1. Unity CLI: назначение и полный список команд (с синтаксисами)

Официальное описание: «The Unity CLI is a standalone command-line tool for installing and managing Unity Editors and modules from a terminal… Well suited for CI and build agents… scripting and automation that needs structured output (JSON or TSV) and predictable exit codes». Unity Hub теперь устанавливает CLI автоматически. ([main](https://docs.unity.com/en-us/unity-cli), [intro](https://docs.unity.com/en-us/unity-cli/unity-cli))

Типовые команды из «Use the Unity CLI» ([use](https://docs.unity.com/en-us/unity-cli/use-unity-cli)):

```shell
unity --help                                # Top-level help
unity install lts                           # Установить последний LTS-редактор
unity install-modules -e 6000.3.7f1 -m ios  # Добавить модуль iOS
unity editors -i                            # Список установленных редакторов
unity open ./MyProject                      # Открыть проект
unity shell                                 # Интерактивная сессия (много команд в одном процессе)
unity doctor                                # Диагностический снимок
```

Полный каталог команд из страницы **Unity CLI reference** ([reference](https://docs.unity.com/en-us/unity-cli/unity-cli-reference)) — сгруппирован по разделам документации:

### Редакторы и модули
| Команда | Алиас | Назначение |
|---|---|---|
| `unity install [version] [options]` | `i` | Установить версию редактора, опционально с модулями. Версия позиционная; на неинтерактивном терминале (CI) без версии — ошибка. Алиасы версий: `latest`, `lts`, `default`, `6`, `6.5`, `2022` (мажор/minor-поток), версии вида `6000.3.7f1`. Опции: `-c/--changeset <hash>`, `-m/--module <id>` (несколько), `--cm/--childModules`, `-a/--architecture` (macOS) |
| `unity install-modules [options]` | `im` | Добавить модули к установленному редактору. Опции: `-e/--editor-version <version>`, `-m/--module <id>`, `-l/--list`, `--all`, `--cm`, `--no-cm` |
| `unity uninstall <version>` | `u` | Удалить установленную версию редактора |
| `unity editors [options]` | `e` | Список релизов/установленных редакторов, добавление локальных установок, установка редактора по умолчанию, апгрейд, список запущенных экземпляров. Подкоманды: `editors add <path...>`, `editors default [version]`, `editors prune`, `editors verify <version>`, `editors upgrade [editor]`, `editors running`, `editors info <version>`, `editors path <version>`, `editors list`. Опции: `-r/--releases`, `-i/--installed`, `-a/--architecture`, `--json`, `-w/--watch` |
| `unity editor ...` | — | Управление одной установкой редактора (например, `editor module remove` — удалить модули конкретной версии) |
| `unity install-path [options]` | `ip` | Показать/изменить путь установки редакторов |
| `unity modules list <version>` | — | Список доступных модулей для версии редактора |
| `unity releases` | — | Список доступных релизов Unity из release feed |
| `unity hub install` | — | Установить Unity Hub desktop |

### Проекты, сборки, шаблоны
| Команда | Алиас | Назначение |
|---|---|---|
| `unity open <path>` (или просто `unity ./MyProject`) | — | Открыть проект в подходящей версии редактора (версия резолвится из настроек проекта); `--editor-version <version>`; `--args` — проброс аргументов редактору |
| `unity projects [subcommand]` | `p` | Управление проектами в реестре Hub: `list`, `create`, `clone`, `open`, `new <name> --template <id> --editor-version <version>`, `link vcs`, `pin/unpin`, `export/import`, `info <path>`, `upgrade ... --to <version>`, `require <module>...`, `size`, `close`, `clean`, `exec -- <command>`, `verify` |
| `unity templates` | `t` | Шаблоны проектов: `list`, `info`, `create`, `edit`, `delete`, `pack`, `location` |
| `unity build ...` | — | Сборка проекта в batch mode с CI-флагами: `--target <BuildTarget>`, `--output-path`, `--profile <path-or-name>` (Unity 6+, Build Profile), `--execute-method <MethodName>` (по-прежнему работает и имеет приоритет), Android-подпись (keystore и т.д.), `--versioning-strategy`, `--build-version`, `--allow-dirty-build`, `--timeout <сек>` + `UNITY_BUILD_TIMEOUT`, `--provenance-path`/`--no-provenance` |
| `unity run [project]` | — | Запуск проекта в batch mode (поток логов, возврат кода выхода редактора) **или** `unity run --command <name>` — headless-запуск зарегистрированной `[CliCommand]`-команды редактора |
| `unity test [project]` | — | Edit Mode / Play Mode тесты редакторным тест-раннером; опции `--mode`, `--filter`, `--output` (NUnit XML, дефолт `test-results.xml`), `--editor-version`, `--editor-path`, `--architecture`, `--allow-install`, `--timeout`, `--shard N/M`, `--retries N`, `--rerun-failed`; коды выхода `6`/`8` |

### Аккаунты, лицензии, Unity Cloud
| Команда | Алиас | Назначение |
|---|---|---|
| `unity auth login` / `auth status` / `auth logout` | `a` | Вход (браузер), статус, выход. Мультиаккаунт: `auth list`, `auth switch <account>`, `auth default <account>` |
| `unity license` | — | Лицензии: `license list`, `license status`, `license activate` (serial / Personal / floating / offline file / offline request), `license return`, `license server status`, `license server list` |
| `unity cloud` | — | Статус облачного входа, организации, облачные проекты (`cloud status`, `cloud project list`) |

### Unity Collaboration
| Команда | Назначение |
|---|---|
| `unity collaboration` (`collab`) | Аннотации, вложения, thumbnails, реакции, привязка Jira |

### Подключённые редакторы и AI-агенты (требуют Unity Pipeline package)
| Команда | Алиас | Назначение |
|---|---|---|
| `unity command [<name>]` | `cmd`, `request` | Выполнить команду подключённого редактора (или список команд); опции `--project-path`, `--detach` (job), `--query/--tag/--group_by/--sort/--order/--offset/--limit/--detail` |
| `unity list` | — | Список всех инструментов (команд), регистрируемых подключённым редактором, со схемами параметров |
| `unity pipeline install` / `upgrade` / `list` / `list-versions` | `pipe` | Установка/обновление/просмотр пакета Unity Pipeline, подключающего CLI к редактору |
| `unity status` | — | Живое состояние подключённых редакторов: порт, путь проекта, версия, PID |
| `unity mcp` / `mcp configure <client>` | — | MCP-сервер для AI-агентов (Claude, Cursor, VS Code и др., 16 клиентов) |
| `unity eval '<expr>'` | — | Выполнить C#-выражение против подключённого редактора (через Pipeline server); `--detach` для долгих операций |
| `unity job wait` / `status` / `cancel` | — | Управление detached-джобами редактора |
| `unity skill install <client>` / `refresh` | — | Установить «скилл» Unity CLI в конфиг AI-агента |

### Диагностика и конфигурация
`unity doctor` (снимок + health-проверки; `doctor --ci` — preflight «сможет ли машина собрать/протестировать»), `unity diagnose` (`diagnose update`, `diagnose proxy`), `unity logs` (читать/следить `cli-log.json`), `unity env`, `unity config` (`config proxy`, `config update-check off`), `unity analytics opt-in|opt-out|status`, `unity cache` (`cache info`, `cache clean`, `cache key <project>` — детерминированный ключ кэша `Library`), `unity bug`, `unity language` (`lang`), `unity completion bash|zsh|fish|powershell`, `unity shell`.

### Жизненный цикл CLI
`unity upgrade` (самообновление; `--target <version>`, `--rollback`, `--changelog`), `unity self-uninstall`, `unity help` / `unity --help` / `unity <command> --help` / `unity <command> <subcommand> --help`.

> Документация прямо предупреждает: «Run `unity --help` to check the authoritative command list for your installed version, including any commands not yet covered on this page» ([reference](https://docs.unity.com/en-us/unity-cli/unity-cli-reference)).

### Выходные форматы, коды выхода, env-переменные (для CI)
- **Форматы:** `human` (интерактивный терминал), `tsv` (при перенаправлении в pipe/файл — по умолчанию!), `json` (`--format json` / глобальный флаг `--json`), `ndjson` (поток прогресса для долгих команд), `github` (аннотации GitHub Actions). Ошибки пишутся в **stderr** (`{"error": "..."}` в JSON-режиме).
- **Коды выхода:** `0` успех; `1` общая ошибка; `2` usage-ошибка; `3` ошибка аутентификации/авторизации; `4` требуется конфигурация; `6` основная операция не выполнена (например, сборка упала, редактор завершился с ошибкой); `7` сервис Unity недоступен (безопасно ретраить); `8` (только `unity test`) тесты прошли, но есть падения; `130` Ctrl+C; `143` SIGTERM.
- **Env-переменные:** `UNITY_FORMAT`, `UNITY_QUIET`, `UNITY_NO_BANNER`, `UNITY_NON_INTERACTIVE`, `UNITY_PAGER`, `UNITY_NO_PAGER`, `UNITY_PROJECT_PATH`, `UNITY_CLOUD_ORG`, `UNITY_PROXY`, `UNITY_LOG_PROXY`, `UNITY_INSTALL_RETRIES`, `UNITY_NO_ELEVATE`, `UNITY_BUILD_TIMEOUT`, `UNITY_NO_UPDATE_CHECK`, `UNITY_NO_CRASH_REPORT`, `UNITY_NO_CONSENT_PROMPT`, **`UNITY_SERVICE_ACCOUNT_ID` + `UNITY_SERVICE_ACCOUNT_SECRET`** (вход сервисным аккаунтом без браузера), `UNITY_TEST_TIMEOUT`.
  ([reference](https://docs.unity.com/en-us/unity-cli/unity-cli-reference))

---

## 2. Команды для шейдеров / Shader Graph — **их нет**

**Прямой ответ: в unity-cli нет ни одной команды, относящейся к шейдерам или Shader Graph.** Не найдено ни:
- создания шейдерных ассетов,
- импорта шейдеров,
- компиляции шейдеров / Shader Graph,
- каких-либо «shader»-подкоманд.

Проверка: полностраничный поиск по всем 6 страницам раздела (включая .md-версии) по словам `shader`, `ShaderGraph`, `executeMethod` → **0 совпадений** у `shader`/`ShaderGraph`; единственные упоминания `executeMethod` — флаг `--execute-method` у `unity build` (см. раздел 3). В каталоге команд reference-страницы раздела «шейдеры» отсутствует.

Ближайшее, что существует, — **в Unity Pipeline package** (это не команды CLI, а команды *редактора*, которые CLI вызывает через `unity command` / `unity eval` / `unity run --command`). Из официального справочника команд пакета ([materials](https://docs.unity3d.com/Packages/com.unity.pipeline@0.6/manual/commands/materials.html), [index](https://docs.unity3d.com/Packages/com.unity.pipeline@0.6/manual/index.html)):

| Команда пакета | Назначение |
|---|---|
| `list_shaders` | Список доступных шейдеров `[{name, assetPath, isBuiltin, isSupported}]`; фильтр `filter` (подстрока), `includeBuiltin`, `limit` (дефолт 200) |
| `get_shader_properties` | Интроспекция объявленных свойств шейдера (name, type Color/Vector/Float/Range/TexEnv/Int, range, textureDimension, flags); по `shader` (имени) или `material` |
| `get_material_properties` | Чтение шейдера, render queue, keywords и всех свойств материала |
| `set_material_properties` | Установка свойств (`_BaseColor` и т.п.), смена шейдера (можно указать «или имя шейдера Shader Graph»), render queue, keywords |

Из ассет-команд ([assets-and-files](https://docs.unity3d.com/Packages/com.unity.pipeline@0.6/manual/commands/assets-and-files.html)) релевантны общие: `import_asset` (скопировать внешний файл в проект и импортировать), `set_import_settings` (изменить настройки AssetImporter и **переимпортировать** ассет), `get_import_settings`, `find_assets`, `create_asset`, `write_text_file` (записать файл в проект и импортировать его). Все пути ограничены «authoring root»-песочницей. Ни одна из них не заточена под шейдеры/Shader Graph — это generic-операции с ассетами.

В разделах б/в: на форумах/блогах рецепты «Shader Graph в CI» сводятся к переимпорту `.shadergraph`-ассета через `AssetDatabase.ImportAsset` в `-executeMethod`-контексте (см. раздел 4); отдельных официальных команд для этого не существует ни в CLI, ни в пакете.

---

## 3. Запуск произвольного C#-кода редактора (аналог `-executeMethod`)

Да, это возможно, но **только через Unity Pipeline package** и в нескольких формах. Официальные примитивы (все подтверждены документацией):

### 3.1. `unity run --command <name>` — headless-аналог `-executeMethod` (batch mode)
Из официальных release notes (`1.0.0-beta.3`, 23 июля 2026): «Added `unity run --command <name>` to run a registered `[CliCommand]` Editor command headlessly in a single invocation. **The CLI starts the Editor in batch mode, runs the command, prints the return value, and shuts the Editor down. Requires the Unity pipeline package.**» ([release-notes](https://docs.unity.com/en-us/unity-cli/release-notes))

Reference-страница описывает команду так: «Run a project in batch mode, **or run a registered Editor command headlessly with `--command`**». ([reference](https://docs.unity.com/en-us/unity-cli/unity-cli-reference))

Значит:
- команда должна быть **зарегистрирована как `[CliCommand]`-метод** (см. 3.4) в проекте, где установлен пакет Unity Pipeline;
- `unity run --command <имя>` сам поднимет редактор в batch mode, выполнит команду, напечатает возвращаемое значение и закроет редактор.

### 3.2. `unity command <name>` — против уже запущенного редактора
«Forward a command to a connected Unity Editor, or list the commands a connected Editor exposes» ([reference](https://docs.unity.com/en-us/unity-cli/unity-cli-reference)); «Added `unity command` (`unity cmd`, `unity request`) to execute commands against connected Unity Editor instances or list available commands» ([release-notes](https://docs.unity.com/en-us/unity-cli/release-notes)). Плюс `unity list` — «list every tool a connected Editor registers, with its parameter schema». Требует запущенного редактора с установленным Pipeline-пакетом (авто-поиск из текущей директории или `--project-path`).

### 3.3. `unity eval '<expr>'` — C#-выражение «на лету»
«Added `unity eval '<expr>'` to evaluate C# expressions against a connected Unity Editor through the Pipeline server» ([release-notes](https://docs.unity.com/en-us/unity-cli/release-notes), `0.1.0-beta.6`). Примеры из документации пакета ([runtime](https://docs.unity3d.com/Packages/com.unity.pipeline@0.6/manual/commands/runtime.html)): команды `eval` (параметр `code`, `timeout` в мс, дефолт 5000) и `eval_file` (`file` — путь к `.cs`, `timeout`). Требует **подключённого запущенного редактора** (не headless).

### 3.4. Собственная `[CliCommand]`-команда (C#-код в проекте)
API авторинга команд ([creating-commands](https://docs.unity3d.com/Packages/com.unity.pipeline@0.6/manual/creating-commands.html)):
- статический метод, помеченный `[CliCommand("name", "description", MainThreadRequired = true, RuntimeOnly = false)]`;
- параметры через `[CliArg("name", "description", Required = false, DefaultValue = ...)]`;
- метод может быть `public/internal/private static` (доступность неважна, вызов через reflection);
- возвращаемое значение оборачивается в `CommandExecutionResponse` (`success`, `result`, `error`, ...);
- авто-обнаружение через `TypeCache`; новая команда доступна после рекомпиляции проекта;
- пример из документации:

```csharp
using Unity.Pipeline.Commands;
using UnityEditor;
using UnityEngine;

public static class PlayModeCommands
{
    [CliCommand("editor_play", "Enter Unity Editor play mode")]
    public static string EnterPlayMode()
    {
        if (EditorApplication.isPlaying)
            return "Already in play mode";

        EditorApplication.isPlaying = true;
        return "Entered play mode";
    }
}
```

Минимальный шаблон из документации: `unity command my_command --text hello --count 3` ([creating-commands](https://docs.unity3d.com/Packages/com.unity.pipeline@0.6/manual/creating-commands.html)).

### 3.5. `run_script` — выполнение `.cs`-файла без перезагрузки домена
Команда пакета ([scripts](https://docs.unity3d.com/Packages/com.unity.pipeline@0.6/manual/commands/scripts.html)): «Compile a single project `.cs` file in memory (no domain reload...) and execute a named static entry point». Пример: `run_script --file AgentScripts/Build.cs --entry Build.All`. Вызывается как `unity command run_script --file ... --entry ...` (или напрямую по HTTP). Это самый близкий официальный аналог «запустить метод по пути к файлу», без редактирования `Assets/` (файл может лежать вне `Assets/`, например `AgentScripts/`, чтобы не триггерить импорт).

### 3.6. `unity build --execute-method <MethodName>` — CLI-флаг, аналог `-executeMethod`
Из release notes (`1.0.0-beta.4`): «`unity build` no longer requires `--execute-method`. On Unity 6 and newer, `--profile <path-or-name>` builds a Build Profile, and on every version the desktop targets build without a method, so `unity build --target StandaloneWindows64 --output-path Build/MyGame.exe` works with no project-side C# code. **`--execute-method` keeps working and takes precedence.**» ([release-notes](https://docs.unity.com/en-us/unity-cli/release-notes)) — то есть внутри сборки можно выполнить статический редакторный метод (классический public static метод без аргументов, как для `-executeMethod`).

### 3.7. Классический `-executeMethod` (вне unity-cli, для сравнения)
Официальный Manual по аргументам редактора: `unity.exe -batchmode -quit -executeMethod MyClass.MyMethod` ([EditorCommandLineArguments](https://docs.unity3d.com/Manual/EditorCommandLineArguments.html)). Это аргументы самого редактора; unity-cli их «оборачивает» только через `run --command` / `build --execute-method`.

**Вывод (в):** «аналог `-executeMethod` через unity-cli» = `unity run --command <name>` (после установки Pipeline-пакета и регистрации `[CliCommand]`), либо `unity build --execute-method <Method>`, либо `unity eval '<expr>'` против запущенного редактора. Ни одна из форм не выполняет произвольный код без предварительной подготовки проекта (пакет + метод либо запущенный редактор).

---

## 4. Практический рецепт: Shader Graph-ассет в CI/batch-режиме

**Официального рецепта «собрать/переимпортировать Shader Graph через unity-cli» в документации нет** (раздел 2: шейдерных команд не существует). Ниже — реконструкция **(в-вывод)** из проверенных официальных примитивов.

Общие факты про Shader Graph (известные свойства движка, **(в)**, API-страницы Unity проверены — см. ниже):
- `.shadergraph` — это сериализованный ассет; «компиляция» шейдера происходит в момент **импорта** ассета редактором (плюс генерация кода при сохранении графа). Поэтому «собрать Shader Graph» в batch/CI = **заставить редактор переимпортировать файл** `.shadergraph` с принудительной синхронной пересборкой.
- Переимпорт любого ассета: `AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport)` ([AssetDatabase.ImportAsset](https://docs.unity3d.com/ScriptReference/AssetDatabase.ImportAsset.html), [ImportAssetOptions](https://docs.unity3d.com/ScriptReference/ImportAssetOptions.html): `ForceUpdate` — «User initiated asset import», `ForceSynchronousImport` — «Import all assets synchronously»), затем `AssetDatabase.Refresh()` ([AssetDatabase](https://docs.unity3d.com/ScriptReference/AssetDatabase.html)). Для ассетов шейдеров есть `ShaderImporter` ([ShaderImporter](https://docs.unity3d.com/ScriptReference/ShaderImporter.html)).

### Вариант А (рекомендуемый, чистый unity-cli): регистрируемая команда + `unity run --command`

1. Установить Unity CLI и Pipeline-пакет в проект (разделы 5, 6):
   ```shell
   unity auth login
   unity pipeline install          # из директории проекта
   ```
   Требование: Unity Editor **6.0 или новее** ([pipeline](https://docs.unity.com/en-us/unity-production-pipeline/local-tools-cli/unity-pipeline-package)).
2. Добавить в проект C#-файл (Editor-скрипт) с `[CliCommand]` (шаблон из [creating-commands](https://docs.unity3d.com/Packages/com.unity.pipeline@0.6/manual/creating-commands.html)):
   ```csharp
   using Unity.Pipeline.Commands;
   using UnityEditor;

   public static class ShaderReimportCommands
   {
       [CliCommand("reimport_shadergraph", "Force reimport of Shader Graph assets")]
       public static string ReimportShaders(
           [CliArg("path", "Asset path, e.g. Assets/Shaders/Foliage.shadergraph", Required = true)] string path)
       {
           AssetDatabase.ImportAsset(path,
               ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
           AssetDatabase.Refresh();
           var importer = AssetImporter.GetAtPath(path);
           return importer == null ? "not found: " + path : "reimported: " + path;
       }
   }
   ```
   *(код — моя реконструкция, **(в)**; API-классы проверены по ScriptReference)*
3. Дождаться рекомпиляции проекта в редакторе (локально один раз).
4. В CI выполнить headless-переимпорт:
   ```shell
   unity run --command reimport_shadergraph --path Assets/Shaders/Foliage.shadergraph
   ```
   — CLI поднимет редактор в batch mode, выполнит команду, напечатает результат и закроет редактор ([release-notes](https://docs.unity.com/en-us/unity-cli/release-notes), [reference](https://docs.unity.com/en-us/unity-cli/unity-cli-reference)). Для массового переимпорта можно использовать `-path "Assets/Shaders"` + `ImportAssetOptions.ImportRecursive` или `find_assets` ([assets-and-files](https://docs.unity3d.com/Packages/com.unity.pipeline@0.6/manual/commands/assets-and-files.html)) и цикл внутри метода.

### Вариант Б: `unity build --execute-method <Method>`
Статический метод (например, тот же переимпорт) выполнится при сборке через `unity build --target StandaloneWindows64 --execute-method ShaderReimportCommands.ReimportShaders` — флаг `--execute-method` поддерживается и имеет приоритет ([release-notes](https://docs.unity.com/en-us/unity-cli/release-notes)). Минус: метод исполняется в контексте сборки плеера, а не как отдельная операция.

### Вариант В: `unity eval` против запущенного редактора (не headless)
`unity eval 'AssetDatabase.ImportAsset("Assets/Shaders/Foliage.shadergraph", (int)(ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport)); AssetDatabase.Refresh(); "ok"'` против редактора с установленным Pipeline-пакетом ([release-notes](https://docs.unity.com/en-us/unity-cli/release-notes), [runtime](https://docs.unity3d.com/Packages/com.unity.pipeline@0.6/manual/commands/runtime.html)). Для больших кусков кода — `run_script --file AgentScripts/Build.cs --entry ...` ([scripts](https://docs.unity3d.com/Packages/com.unity.pipeline@0.6/manual/commands/scripts.html)).

### Вариант Г: классика (вне unity-cli, но комбинируемая)
Тот же статический метод, но через прямой запуск редактора:
```shell
Unity.exe -batchmode -quit -projectPath <path> -executeMethod ShaderReimportCommands.ReimportShaders
```
([EditorCommandLineArguments](https://docs.unity3d.com/Manual/EditorCommandLineArguments.html)). unity-cli в этой связке используется как установщик редактора/модулей и менеджер лицензий.

**Предостережения (в-выводы на основе документации):**
- Всё, что гоняет редактор, требует **лицензии** на машине CI: `unity license status`/`activate`, floating-лицензии через `unity license server status`; preflight — `unity doctor --ci` (проверяет «activatable license, project editor, disk space, network») ([release-notes](https://docs.unity.com/en-us/unity-cli/release-notes), [use](https://docs.unity.com/en-us/unity-cli/use-unity-cli)).
- `[CliCommand]`-метод должен существовать в скомпилированной сборке проекта; fresh checkout в CI должен сначала дать редактору импортировать/скомпилировать проект (холодный импорт — это «settling»-окно сервера, см. [connectivity](https://docs.unity3d.com/Packages/com.unity.pipeline@0.6/manual/connectivity.html)).
- Пакет должен быть в проекте (добавляется `unity pipeline install`); для Shader Graph нужен сам пакет `com.unity.shadergraph` в проекте (стандартное UPM-требование, **(в)**, в документации unity-cli об этом не сказано).

---

## 5. Требования, установка, версии Unity, лицензии

### Поддерживаемые ОС ([use](https://docs.unity.com/en-us/unity-cli/use-unity-cli)):
- Windows 10 21H1 и новее;
- Linux: RHEL 9, Ubuntu 22.04+ (glibc 2.34+; Debian 11, CentOS 8, Amazon Linux 2 — **не поддерживаются**; c `1.0.0-beta.4`);
- macOS 14 и новее.

### Установка ([use](https://docs.unity.com/en-us/unity-cli/use-unity-cli)):
```powershell
# Windows (PowerShell), beta-канал:
$env:UNITY_CLI_CHANNEL='beta'; irm https://public-cdn.cloud.unity3d.com/hub/prod/cli/install.ps1 | iex
```
```bash
# macOS/Linux:
curl -fsSL https://public-cdn.cloud.unity3d.com/hub/prod/cli/install.sh | UNITY_CLI_CHANNEL=beta bash
```
Альтернативы: **winget** `winget install Unity.CLI` / `winget upgrade Unity.CLI`; **Homebrew** `brew install --cask unity-cli` / `brew upgrade unity-cli` (команда ставится как `unity`); **apt/rpm** репозитории Unity (`unity-cli`); **MSIX** (Windows, самообновление); в release notes также упомянут npm: `npm i -g @unity/cli@latest`. Обновление: `unity upgrade`. Проверка: `unity --version`. Есть `unity diagnose update` — что за резолвилось и какой командой обновляется.

### Версии Unity ([pipeline](https://docs.unity.com/en-us/unity-production-pipeline/local-tools-cli/unity-pipeline-package), [replace-mcp](https://docs.unity.com/en-us/unity-cli/replace-mcp-server-unity-cli)):
- **Сам CLI управляет любой версией редактора** («The CLI manages any Editor version»);
- **Управление редактором через Unity Pipeline package требует Unity 6.0 или новее** («Install the Unity Editor version 6.0 or later»; «To drive a running Editor through the Unity Pipeline package, use Unity 6.0 LTS or later»);
- В примерах документации фигурируют `6000.3.7f1` и т.п.; текущая версия CLI — `1.0.0-beta.6` (21 августа 2026), полная история — в [release-notes](https://docs.unity.com/en-us/unity-cli/release-notes). Страницы помечены «experimental».

### Связывание CLI с установленной версией редактора:
- CLI/Hub-установленные редакторы управляются автоматически; для вручную установленных — `unity editors add <path>` (можно несколько путей), затем `unity editors default [version]`, путь установки — `unity install-path`, инфо — `unity editors info <version>`, путь — `unity editors path <version>` ([reference](https://docs.unity.com/en-us/unity-cli/unity-cli-reference));
- `unity open <path>` сам резолвит версию из настроек проекта; можно указать `--editor-version <version>`.

### Авторизация / лицензии:
- **Аккаунт:** `unity auth login` (браузер), `auth status`, `auth logout`; мультиаккаунт `auth list/switch/default`. Для CI — **сервисный аккаунт** через env: `UNITY_SERVICE_ACCOUNT_ID` + `UNITY_SERVICE_ACCOUNT_SECRET` (создание сервисного аккаунта — [Service accounts](/cloud/accounts/create-service-account.md)) ([use](https://docs.unity.com/en-us/unity-cli/use-unity-cli), [reference](https://docs.unity.com/en-us/unity-cli/unity-cli-reference)).
- **Лицензии редактора:** `unity license list/status/activate/return` (+ `license server status/list` для floating-лицензий). `license activate` поддерживает serial, Personal, floating, offline file, offline request ([release-notes](https://docs.unity.com/en-us/unity-cli/release-notes)). Дефолтный код выхода при проблемах аутентификации — `3`.
- Сам CLI бесплатен и не требует подписки Unity AI ([replace-mcp](https://docs.unity.com/en-us/unity-cli/replace-mcp-server-unity-cli)); лицензия нужна самому редактору при любом batch-запуске (в-вывод, подтверждённый существованием `unity license` и `doctor --ci`).

---

## 6. Прочие полезные команды (запуск/остановка редактора, логи, batch mode)

- **Запуск редактора:** `unity open <path>` (или сокращение `unity ./MyProject`); shorthand `unity <version> [path]` — запуск редактора конкретной версии с опциональным путём ([reference](https://docs.unity.com/en-us/unity-cli/unity-cli-reference), [release-notes](https://docs.unity.com/en-us/unity-cli/release-notes)); проброс аргументов редактору — `unity open ... --args ...` (упомянуто в фиксах release notes: «`unity open` did not correctly forward `--args` to the Unity Editor»).
- **Запущенные/остановка:** `unity editors running` (экземпляры: проект, версия, PID), `unity status` (порт, путь, версия, PID подключённых редакторов), `unity projects close [project]` (закрыть редактор с проектом; `--timeout`, `--force`).
- **Batch mode:** `unity run [project]` (запуск проекта в batch, поток логов, код выхода редактора), `unity build` (сборка, CI-флаги, см. раздел 1), `unity test` (Edit/Play Mode, NUnit-отчёт), `unity run --command <name>` (headless `[CliCommand]`).
- **Логи:** свой лог CLI `cli-log.json` в общей папке логов Hub: Windows `%UserProfile%\AppData\Roaming\UnityHub\logs`, macOS `~/Library/Application Support/UnityHub/logs`, Linux `~/.config/UnityHub/logs`; читать — `unity logs`, в диагностику — `unity doctor` ([reference](https://docs.unity.com/en-us/unity-cli/unity-cli-reference)). Логи самого редактора — стандартные (Editor.log), сервер Pipeline пишет строки `Pipeline: ...` и порт ([connectivity](https://docs.unity3d.com/Packages/com.unity.pipeline@0.6/manual/connectivity.html)).
- **Автоматизация:** `--format json|tsv|ndjson|github`, коды выхода, `--non-interactive, --quiet, --no-banner, --yes`, auth по сервисному аккаунту, detached-джобы `unity command <name> --detach` + `job wait/status/cancel`, `unity cache key` для CI-кэша `Library`, `unity doctor --ci` как preflight, `unity projects exec -- <command>` (выполнить команду по всем зарегистрированным проектам) ([release-notes](https://docs.unity.com/en-us/unity-cli/release-notes), [reference](https://docs.unity.com/en-us/unity-cli/unity-cli-reference)).

---

## 7. Ограничения (чего CLI НЕ умеет)

1. **Нет команд для шейдеров / Shader Graph** — ни создания, ни компиляции, ни импорта шейдерных ассетов (раздел 2).
2. **Не выполняет произвольный C#-код сам по себе**: нужен Unity Pipeline package и предварительно зарегистрированный `[CliCommand]` (или запущенный редактор для `command`/`eval`). «Из коробки» — только `build --execute-method` (в контексте сборки) и `run --command` (после установки пакета).
3. **Не управляет ассетами/проектными файлами напрямую** — ассет-операции возможны только через команды редактора (Pipeline) или через `-executeMethod`-методы; в самом CLI ассет-команд нет.
4. **Экспериментальный статус** и неполнота документации: официальная страница прямо отсылает к `unity --help` как к авторитетному источнику флагов/команд своей версии ([reference](https://docs.unity.com/en-us/unity-cli/unity-cli-reference)).
5. **Pipeline работает только локально**: сервер биндится на `127.0.0.1` (порты редактора 7800–7849, runtime 7900–7949), дескриптор-файл `Library/Pipeline/.unity-pipeline-port` с bearer-токеном; никакого доступа с других машин; auth через `Authorization: Bearer <evalToken>` ([connectivity](https://docs.unity3d.com/Packages/com.unity.pipeline@0.6/manual/connectivity.html), [replace-mcp](https://docs.unity.com/en-us/unity-cli/replace-mcp-server-unity-cli)).
6. **Требует Unity 6.0+** для управления редактором через Pipeline (сам CLI ставит любые версии редакторов) ([pipeline](https://docs.unity.com/en-us/unity-production-pipeline/local-tools-cli/unity-pipeline-package)).
7. **Не отменяет лицензирование редактора**: batch-запуски редактора требуют лицензии (личной/серийной/floating/offline); CLI лишь автоматизирует активацию/возврат и диагностику (`unity license`, `doctor --ci`) ([release-notes](https://docs.unity.com/en-us/unity-cli/release-notes)).
8. **Редактор не обязан быть установлен через Hub/CLI**, но модули добавляются только к редакторам, установленным Hub или CLI («You can only add modules to Editors installed through the Hub or the CLI») ([reference](https://docs.unity.com/en-us/unity-cli/unity-cli-reference)).
9. **Не запускает облачные сборки Unity Cloud Build/DevOps** — это не тот инструмент; unity-cli управляет локальными редакторами/проектами/licensing и локальным batch. (В-вывод: в документации раздела нет упоминаний облачных пайплайнов; `cloud`-команды — только статус/организации/проекты.)
10. **Нет страниц** `/user-guide`, `/examples`, `/concepts`, `/admin` — раздел состоит ровно из 6 страниц (подтверждено sitemap), «reference» фактически живёт по адресу `/unity-cli-reference`.

---

## Источники (все проверены HTTP 200 в этой сессии)

**Unity CLI (docs.unity.com):**
- https://docs.unity.com/en-us/unity-cli — главная (experimental, Hub ставит CLI автоматически, для управления редактором нужен Unity Pipeline package)
- https://docs.unity.com/en-us/unity-cli/unity-cli — введение (назначение, CI, JSON/TSV, миграция с Hub CLI `-- --headless`)
- https://docs.unity.com/en-us/unity-cli/use-unity-cli — установка (install.ps1/sh, winget, brew, apt/rpm), совместимость, задачи (install/install-modules/open/auth, сервисные аккаунты)
- https://docs.unity.com/en-us/unity-cli/unity-cli-reference — справочник команд, опции, форматы, коды выхода, env, миграция с Hub CLI
- https://docs.unity.com/en-us/unity-cli/release-notes — релизы 0.1.0-beta.1 … 1.0.0-beta.6 (run --command, build --execute-method, license, eval, mcp и т.д.)
- https://docs.unity.com/en-us/unity-cli/replace-mcp-server-unity-cli — замена in-Editor MCP-сервера; Unity 6.0 LTS+ для Pipeline; localhost-only; CLI бесплатен

**Unity Pipeline package (docs.unity3d.com/Packages/com.unity.pipeline@0.6):**
- https://docs.unity.com/en-us/unity-production-pipeline/local-tools-cli/unity-pipeline-package — установка пакета, порт 7800, `unity pipeline install/list`, `unity command`
- https://docs.unity3d.com/Packages/com.unity.pipeline@0.6/index.html и …/manual/index.html — обзор
- …/manual/creating-commands.html — `[CliCommand]`/`[CliArg]`, CommandExecutionResponse, шаблон, теги (в т.ч. `materials/shaders`)
- …/manual/connectivity.html — loopback 127.0.0.1, порты 7800–7849, дескриптор-файл, bearer-token, `/api/status|commands|exec`, джобы, токенизатор
- …/manual/runtime-setup.html — runtime-сервер в Development Build (порты 7900–7949)
- …/manual/commands/materials.html — `list_shaders`, `get_shader_properties`, `get_material_properties`, `set_material_properties`
- …/manual/commands/assets-and-files.html — `import_asset`, `set_import_settings`, `get_import_settings`, `find_assets`, `write_text_file` и др.
- …/manual/commands/scripts.html — `create_script`, `attach_script`, `set/get_serialized_field`, `run_script` (builder-паттерн)
- …/manual/commands/build-and-compilation.html — `build`, `build_status`, `recompile`, `switch_build_target`, `list_build_profiles`
- …/manual/commands/runtime.html — `eval`, `eval_file`, `console`, hot-reload команды

**Unity ScriptReference / Manual (для рецепта):**
- https://docs.unity3d.com/ScriptReference/AssetDatabase.ImportAsset.html
- https://docs.unity3d.com/ScriptReference/ImportAssetOptions.html (ForceUpdate, ForceSynchronousImport — проверено)
- https://docs.unity3d.com/ScriptReference/AssetDatabase.html
- https://docs.unity3d.com/ScriptReference/ShaderImporter.html
- https://docs.unity3d.com/Manual/EditorCommandLineArguments.html (`-batchmode -quit -executeMethod MyClass.MyMethod` — проверено)

**Дополнительные артефакты сессии (для перепроверки):** исходные файлы страниц лежат в `%TEMP%\ucli_docs\` и `%TEMP%\pipeline_docs\` (HTML + извлечённый текст); скрипты загрузки — в `C:\Users\StratoCat\Desktop\uasset_converter\.research_unitycli\` (links.py, fetch.py, fetch_md.py, fetch_pipeline.py, fetch_pipeline_all.py, pipeline_toc.py, to_text.py, fetch_pipeline_pages.py).