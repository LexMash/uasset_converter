using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace UassetImporter
{
    /// <summary>
    /// Окно конвертера: запуск всей цепочки прямо из редактора.
    ///
    /// Настройки живут в том же config.json, что и у отдельного GUI, и правятся
    /// не разбором JSON на C#, а служебными командами convert.py
    /// (--config-dump / --config-set). Так семантика конфига остаётся в одном
    /// месте: добавили ключ в Python — он тут же виден здесь, и наоборот,
    /// невозможно записать сюда значение не того типа.
    /// </summary>
    public class ConverterWindow : EditorWindow
    {
        const string ConverterDirKey = "UassetImporter.ConverterDir";

        // Ключи конфига, которые окно показывает и правит. Остальные остаются
        // как есть: окно не должно уметь испортить то, чего не понимает.
        static readonly (string Key, string LabelKey)[] ExportToggles =
        {
            ("export.textures", "gui.export.textures"),
            ("export.static_meshes", "gui.export.static_meshes"),
            ("export.skeletal_meshes", "gui.export.skeletal_meshes"),
            ("export.animations", "gui.export.animations"),
            ("export.materials", "gui.export.materials"),
            ("export.material_graphs", "gui.export.material_graphs"),
        };

        static readonly string[] ScopeValues = { "test", "selected", "all" };
        static readonly string[] ScopeLabelKeys = { "gui.scope.test", "gui.scope.selected", "gui.scope.all" };

        string _converterDir = "";
        Dictionary<string, string> _config = new Dictionary<string, string>();
        string _configError;

        PythonLocator.Result _python;
        readonly ProcessRunner _runner = new ProcessRunner();

        List<string> _folders = new List<string>();
        HashSet<string> _selectedFolders = new HashSet<string>();
        bool _foldersLoaded;

        readonly List<string> _log = new List<string>();
        Vector2 _logScroll;
        Vector2 _folderScroll;
        string _status = "";
        float _progress;
        string _runningStep;
        bool _importAfterConvert;
        List<string> _languages;

        [MenuItem("Tools/Uasset Converter/Convert", false, 10)]
        public static void Open()
        {
            var window = GetWindow<ConverterWindow>(Loc.T("unity.convert.window_title"));
            window.minSize = new Vector2(560, 640);
        }

        void OnEnable()
        {
            _converterDir = EditorPrefs.GetString(ConverterDirKey, GuessConverterDir());
            _languages = Loc.AvailableCodes();
            if (HasConverter) ReloadConfig();
        }

        bool HasConverter =>
            !string.IsNullOrEmpty(_converterDir) &&
            File.Exists(Path.Combine(_converterDir, "convert.py"));

        /// <summary>
        /// Python-часть лежит рядом с этим C#-кодом (в этом ассете convert.py на
        /// пару уровней выше папки Editor). Находим сам скрипт окна через
        /// AssetDatabase и идём вверх по дереву до папки с convert.py — так путь
        /// угадывается и когда ассет лежит в Assets/, и в старой репо-разработке.
        /// Вводить папку руками нужно только тем, кто разнёс части по разным местам.
        /// </summary>
        static string GuessConverterDir()
        {
            try
            {
                foreach (var guid in AssetDatabase.FindAssets("ConverterWindow t:MonoScript"))
                {
                    var scriptPath = AssetDatabase.GUIDToAssetPath(guid);
                    if (!scriptPath.EndsWith("ConverterWindow.cs", StringComparison.Ordinal))
                        continue;

                    var dir = Path.GetDirectoryName(Path.GetFullPath(scriptPath));
                    while (dir != null && !File.Exists(Path.Combine(dir, "convert.py")))
                        dir = Directory.GetParent(dir)?.FullName;
                    if (dir != null) return dir;
                }
            }
            catch (Exception)
            {
                // Не удалось развернуть путь скрипта — падаем на старый способ ниже.
            }

            try
            {
                // Фолбэк для старой раскладки: пакет подключён как UPM в Packages/.
                var packageDir = Path.GetFullPath("Packages/com.uasset.converter");
                var candidate = Directory.GetParent(packageDir)?.Parent?.FullName;
                if (candidate != null && File.Exists(Path.Combine(candidate, "convert.py")))
                    return candidate;
            }
            catch (Exception)
            {
                // Пакет может быть подключён так, что путь не разворачивается —
                // тогда просто просим указать папку руками.
            }
            return "";
        }

        // -- отрисовка ---------------------------------------------------------

        void OnGUI()
        {
            DrawHeader();
            if (!HasConverter)
            {
                EditorGUILayout.HelpBox(Loc.T("unity.convert.no_converter"), MessageType.Warning);
                DrawLog();
                return;
            }

            DrawPython();
            if (_configError != null)
            {
                EditorGUILayout.HelpBox(_configError, MessageType.Error);
                DrawLog();
                return;
            }

            DrawPaths();
            DrawWhat();
            DrawFolders();
            DrawRun();
            DrawLog();
        }

        void DrawHeader()
        {
            EditorGUILayout.LabelField(Loc.T("unity.convert.header"), EditorStyles.boldLabel);

            using (new EditorGUILayout.HorizontalScope())
            {
                var names = new string[_languages.Count];
                for (var i = 0; i < _languages.Count; i++)
                    names[i] = Loc.DisplayName(_languages[i]);

                var index = Mathf.Max(0, _languages.IndexOf(Loc.Current));
                var picked = EditorGUILayout.Popup(Loc.T("unity.language"), index, names);
                if (picked != index)
                {
                    Loc.SetLanguage(_languages[picked]);
                    // Язык — общая настройка конвертера, а не только редактора:
                    // отчёт unsupported.md должен собираться на том же языке.
                    if (HasConverter) SetConfig("language", _languages[picked]);
                    titleContent = new GUIContent(Loc.T("unity.convert.window_title"));
                }
            }

            using (new EditorGUILayout.HorizontalScope())
            {
                var edited = EditorGUILayout.TextField(Loc.T("unity.convert.converter_dir"), _converterDir);
                if (edited != _converterDir)
                {
                    _converterDir = edited;
                    EditorPrefs.SetString(ConverterDirKey, _converterDir);
                    _foldersLoaded = false;
                    if (HasConverter) ReloadConfig();
                }
                if (GUILayout.Button(Loc.T("unity.browse"), GUILayout.Width(90)))
                {
                    var picked = EditorUtility.OpenFolderPanel(
                        Loc.T("unity.convert.pick_converter"), _converterDir, "");
                    if (!string.IsNullOrEmpty(picked))
                    {
                        _converterDir = picked;
                        EditorPrefs.SetString(ConverterDirKey, _converterDir);
                        _foldersLoaded = false;
                        if (HasConverter) ReloadConfig();
                    }
                }
            }
            EditorGUILayout.Space();
        }

        void DrawPython()
        {
            using (new EditorGUILayout.HorizontalScope())
            {
                var path = _python?.Path ?? Config("paths.python");
                var edited = EditorGUILayout.TextField(Loc.T("unity.convert.python"), path);
                if (edited != path)
                {
                    SetConfig("paths.python", edited);
                    _python = null;
                }
                if (GUILayout.Button(Loc.T("unity.convert.python_autodetect"), GUILayout.Width(90)))
                    DetectPython();
            }

            if (_python == null)
            {
                DetectPython();
                return;
            }

            if (_python.Ok)
            {
                EditorGUILayout.HelpBox(Loc.T("unity.convert.deps_ok"), MessageType.None);
            }
            else if (string.IsNullOrEmpty(_python.Path))
            {
                EditorGUILayout.HelpBox(_python.Error, MessageType.Error);
            }
            else
            {
                EditorGUILayout.HelpBox(
                    Loc.T("unity.convert.deps_missing",
                          "packages", string.Join(", ", _python.MissingPackages),
                          "command", PythonLocator.InstallCommand(_python.Path, _converterDir)),
                    MessageType.Error);
            }
            EditorGUILayout.Space();
        }

        void DrawPaths()
        {
            EditorGUILayout.LabelField(Loc.T("unity.convert.section_paths"), EditorStyles.boldLabel);
            ConfigTextField("paths.ue_project", "gui.path.project", pickFile: true);
            ConfigTextField("paths.ue_engine_dir", "gui.path.engine", pickFile: false);
            ConfigTextField("paths.output_dir", "gui.path.output", pickFile: false);
            EditorGUILayout.Space();
        }

        void DrawWhat()
        {
            EditorGUILayout.LabelField(Loc.T("unity.convert.section_what"), EditorStyles.boldLabel);
            foreach (var (key, labelKey) in ExportToggles)
                ConfigToggle(key, labelKey);

            ConfigPopup("scope.mode", "gui.option.scope", ScopeValues, ScopeLabelKeys);
            ConfigToggle("textures.flip_normal_green", "gui.option.flip_normal");
            EditorGUILayout.Space();
        }

        void DrawFolders()
        {
            if (Config("scope.mode") != "selected") return;

            EditorGUILayout.LabelField(Loc.T("unity.convert.section_folders"), EditorStyles.boldLabel);
            EditorGUILayout.LabelField(Loc.T("unity.convert.folders_hint"),
                                       EditorStyles.wordWrappedMiniLabel);

            if (!_foldersLoaded && GUILayout.Button(Loc.T("unity.convert.refresh_folders")))
                LoadFolders();

            if (_folders.Count == 0) return;

            using (var scroll = new EditorGUILayout.ScrollViewScope(_folderScroll, GUILayout.Height(140)))
            {
                _folderScroll = scroll.scrollPosition;
                foreach (var folder in _folders)
                {
                    var was = _selectedFolders.Contains(folder);
                    // Вложенность показываем отступом: список плоский, а иерархия
                    // в путях есть, и без отступа он читается как каша.
                    var depth = folder.Split('/').Length - 2;
                    using (new EditorGUILayout.HorizontalScope())
                    {
                        GUILayout.Space(depth * 14);
                        var now = EditorGUILayout.ToggleLeft(folder, was);
                        if (now == was) continue;
                        if (now) _selectedFolders.Add(folder);
                        else _selectedFolders.Remove(folder);
                        SetConfig("scope.include_paths", string.Join(";", _selectedFolders));
                    }
                }
            }

            if (GUILayout.Button(Loc.T("unity.convert.refresh_folders")))
                LoadFolders();
            EditorGUILayout.Space();
        }

        void DrawRun()
        {
            EditorGUILayout.LabelField(Loc.T("unity.convert.section_run"), EditorStyles.boldLabel);

            var busy = _runner.IsRunning;
            var blocked = busy || _python == null || !_python.Ok;

            using (new EditorGUI.DisabledScope(blocked))
            {
                using (new EditorGUILayout.HorizontalScope())
                {
                    if (GUILayout.Button(Loc.T("unity.convert.step_export"))) Run("export", false);
                    if (GUILayout.Button(Loc.T("unity.convert.step_shaders"))) Run("shaders", false);
                    if (GUILayout.Button(Loc.T("unity.convert.step_postprocess"))) Run("postprocess", false);
                }
                if (GUILayout.Button(Loc.T("unity.convert.convert_and_import"), GUILayout.Height(30)))
                    Run("all", true);
            }

            using (new EditorGUI.DisabledScope(!busy))
            {
                if (GUILayout.Button(Loc.T("unity.convert.cancel")))
                {
                    _runner.Cancel();
                    Append(Loc.T("unity.convert.cancelled"));
                }
            }

            using (new EditorGUI.DisabledScope(busy))
            {
                if (GUILayout.Button(Loc.T("unity.convert.import_only")))
                    ImportNow();
            }

            if (busy)
            {
                var rect = EditorGUILayout.GetControlRect(false, 18);
                EditorGUI.ProgressBar(rect, _progress, _status);
            }
            else if (!string.IsNullOrEmpty(_status))
            {
                EditorGUILayout.LabelField(_status);
            }
            EditorGUILayout.Space();
        }

        void DrawLog()
        {
            EditorGUILayout.LabelField(Loc.T("unity.log"), EditorStyles.boldLabel);
            using (var scroll = new EditorGUILayout.ScrollViewScope(_logScroll))
            {
                _logScroll = scroll.scrollPosition;
                foreach (var line in _log)
                    EditorGUILayout.LabelField(line, EditorStyles.wordWrappedMiniLabel);
            }
        }

        // -- поля конфига ------------------------------------------------------

        string Config(string key)
        {
            return _config.TryGetValue(key, out var value) ? value : "";
        }

        void ConfigTextField(string key, string labelKey, bool pickFile)
        {
            using (new EditorGUILayout.HorizontalScope())
            {
                var current = Config(key);
                var edited = EditorGUILayout.TextField(Loc.T(labelKey), current);
                if (edited != current) SetConfig(key, edited);

                if (GUILayout.Button(Loc.T("unity.browse"), GUILayout.Width(90)))
                {
                    var picked = pickFile
                        ? EditorUtility.OpenFilePanel(Loc.T(labelKey), current, "uproject")
                        : EditorUtility.OpenFolderPanel(Loc.T(labelKey), current, "");
                    if (!string.IsNullOrEmpty(picked)) SetConfig(key, picked);
                }
            }
        }

        void ConfigToggle(string key, string labelKey)
        {
            var current = Config(key) == "true";
            var edited = EditorGUILayout.Toggle(Loc.T(labelKey), current);
            if (edited != current) SetConfig(key, edited ? "true" : "false");
        }

        void ConfigPopup(string key, string labelKey, string[] values, string[] labelKeys)
        {
            var labels = new string[values.Length];
            for (var i = 0; i < values.Length; i++)
                labels[i] = labelKeys[i] == values[i] ? values[i].ToUpperInvariant() : Loc.T(labelKeys[i]);

            var index = Array.IndexOf(values, Config(key));
            if (index < 0) index = 0;
            var picked = EditorGUILayout.Popup(Loc.T(labelKey), index, labels);
            if (picked != index) SetConfig(key, values[picked]);
        }

        // -- работа с конвертером ---------------------------------------------

        void DetectPython()
        {
            _python = PythonLocator.Locate(Config("paths.python"));
            if (_python.Ok && Config("paths.python") != _python.Path)
            {
                // Найденный путь запоминаем в конфиге: следующий запуск, в том
                // числе из отдельного GUI, не будет искать заново.
                SetConfig("paths.python", _python.Path);
                Append(Loc.T("unity.convert.python_found", "path", _python.Path));
            }
        }

        void ReloadConfig()
        {
            _configError = null;
            var output = RunSync("--config-dump");
            if (output == null)
            {
                _configError = Loc.T("unity.convert.config_failed", "error", "convert.py --config-dump");
                return;
            }

            _config = new Dictionary<string, string>();
            foreach (var line in output.Split('\n'))
            {
                var trimmed = line.Trim('\r', '\n');
                var equals = trimmed.IndexOf('=');
                if (equals > 0) _config[trimmed.Substring(0, equals)] = trimmed.Substring(equals + 1);
            }

            _selectedFolders = new HashSet<string>();
            foreach (var path in Config("scope.include_paths").Split(';'))
                if (!string.IsNullOrEmpty(path)) _selectedFolders.Add(path);

            var language = Config("language");
            if (!string.IsNullOrEmpty(language) && language != "auto" && language != Loc.Current)
                Loc.SetLanguage(language);
        }

        void SetConfig(string key, string value)
        {
            _config[key] = value;
            RunSync($"--config-set \"{key}={value}\"");
        }

        void LoadFolders()
        {
            var output = RunSync("--scan-folders");
            _folders = new List<string>();
            if (output != null)
            {
                foreach (var line in output.Split('\n'))
                {
                    var trimmed = line.Trim('\r', '\n', ' ');
                    if (trimmed.StartsWith("/Game", StringComparison.Ordinal))
                        _folders.Add(trimmed);
                }
            }
            _foldersLoaded = true;
        }

        /// <summary>
        /// Короткие служебные вызовы (чтение конфига, список папок) идут
        /// синхронно: они занимают доли секунды, и ради них разводить
        /// асинхронность значило бы усложнить окно без всякой пользы.
        /// Длинные шаги конвертации — только через ProcessRunner.
        /// </summary>
        string RunSync(string arguments)
        {
            var python = _python?.Path;
            if (string.IsNullOrEmpty(python)) python = Config("paths.python");
            if (string.IsNullOrEmpty(python)) python = "python";

            var (file, prefix) = SplitLauncher(python);
            try
            {
                var info = new System.Diagnostics.ProcessStartInfo(file,
                    $"{prefix}\"{Path.Combine(_converterDir, "convert.py")}\" {arguments}")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    StandardOutputEncoding = System.Text.Encoding.UTF8,
                    StandardErrorEncoding = System.Text.Encoding.UTF8,
                    WorkingDirectory = _converterDir,
                };
                info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";

                using (var process = System.Diagnostics.Process.Start(info))
                {
                    if (process == null) return null;
                    var output = process.StandardOutput.ReadToEnd();
                    var errors = process.StandardError.ReadToEnd();
                    process.WaitForExit(20000);
                    if (process.ExitCode != 0 && !string.IsNullOrEmpty(errors))
                        Append(errors.Trim());
                    return output;
                }
            }
            catch (Exception error)
            {
                Append(Loc.T("unity.convert.config_failed", "error", error.Message));
                return null;
            }
        }

        /// <summary>
        /// Путь к Python может быть не файлом, а лаунчером с ключом ("py -3").
        /// Разделяем, потому что ProcessStartInfo хочет их порознь.
        /// </summary>
        static (string file, string prefix) SplitLauncher(string python)
        {
            var space = python.IndexOf(' ');
            if (space > 0 && !File.Exists(python))
                return (python.Substring(0, space), python.Substring(space + 1).Trim() + " ");
            return (python, "");
        }

        void Run(string step, bool importAfter)
        {
            _log.Clear();
            _progress = 0f;
            _runningStep = step;
            _importAfterConvert = importAfter;
            _status = Loc.T("unity.convert.running", "step", step);

            _runner.OnLog = Append;
            _runner.OnProgress = (done, total, label) =>
            {
                _progress = total > 0 ? (float)done / total : 0f;
                _status = $"{done}/{total}  {label}";
                Repaint();
            };
            _runner.OnFinished = OnStepFinished;

            var (file, prefix) = SplitLauncher(_python?.Path ?? "python");
            var arguments = $"{prefix}\"{Path.Combine(_converterDir, "convert.py")}\" " +
                            $"--porcelain --step {step}";
            try
            {
                _runner.Start(file, arguments, _converterDir);
            }
            catch (Exception error)
            {
                Append(Loc.T("unity.convert.config_failed", "error", error.Message));
                _status = "";
            }
        }

        void OnStepFinished(int code)
        {
            _progress = 1f;
            _status = code == 0
                ? Loc.T("unity.convert.finished", "step", _runningStep)
                : Loc.T("unity.convert.failed", "step", _runningStep, "code", code);
            Append(_status);
            Repaint();

            if (code != 0 || !_importAfterConvert) return;
            _importAfterConvert = false;
            Append(Loc.T("unity.convert.starting_import"));
            ImportNow();
        }

        void ImportNow()
        {
            var outputDir = Config("paths.output_dir");
            // В конфиге путь может быть относительным (по умолчанию "output"),
            // а рабочая папка Unity — это папка проекта, а не конвертера.
            if (!Path.IsPathRooted(outputDir))
                outputDir = Path.Combine(_converterDir, outputDir);

            var manifestPath = UassetImportWindow.ManifestPathFor(outputDir);
            if (manifestPath == null || !File.Exists(manifestPath))
            {
                Append(Loc.T("unity.convert.no_output_yet"));
                return;
            }

            UassetImportWindow.Import(manifestPath, outputDir,
                                      Config("unity.target_root"),
                                      Config("unity.avatar_type"),
                                      true, Append);
        }

        void Append(string message)
        {
            _log.Add(message);
            Repaint();
        }
    }
}
