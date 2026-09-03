using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace UassetImporter
{
    /// <summary>
    /// Окно импорта: указываешь папку output конвертера — получаешь текстуры,
    /// модели, шейдеры и материалы в проекте.
    ///
    /// Импорт разбит на пункты (категории), каждый включается галочкой — чтобы
    /// повторный прогон не тащил всё заново, а добирал только нужное. «Полный»
    /// импорт — это просто все галочки разом (кнопка «Выбрать всё»).
    ///
    /// Порядок шагов не произволен и сохраняется независимо от набора галочек:
    ///   1. копирование файлов и шейдеров — материалам нужно, на что садиться;
    ///   2. настройки текстур — материалам нужны корректные нормали и sRGB;
    ///   3. материалы;
    ///   4. модели с привязкой материалов;
    ///   5. скелетные меши (создают аватары);
    ///   6. клипы — им нужны аватары из шага 5.
    /// </summary>
    public class UassetImportWindow : EditorWindow
    {
        const string OutputKey = "UassetImporter.OutputDir";
        const string TargetKey = "UassetImporter.TargetRoot";
        const string SelPrefix = "UassetImporter.Import.";

        string _outputDir = "";
        string _targetRoot = "Assets/UassetConverted";
        string _avatarType = "Generic";
        bool _overwriteExisting = true;
        Vector2 _logScroll;
        readonly List<string> _log = new List<string>();

        // Что импортировать. Каждая галочка помнится между запусками.
        ImportSelection _selection = ImportSelection.All();

        // Список кодов языков и их подписи для выпадашки в шапке.
        string[] _languages;
        string[] _languageNames;

        /// <summary>Набор категорий импорта — какие пункты гнать в этот запуск.</summary>
        public struct ImportSelection
        {
            public bool textures;
            public bool materials;
            public bool meshes;
            public bool skeletal;
            public bool animations;

            public static ImportSelection All()
            {
                return new ImportSelection
                {
                    textures = true, materials = true, meshes = true,
                    skeletal = true, animations = true,
                };
            }

            public bool Any => textures || materials || meshes || skeletal || animations;

            public int StepCount =>
                (textures ? 1 : 0) + (materials ? 1 : 0) + (meshes ? 1 : 0) +
                (skeletal ? 1 : 0) + (animations ? 1 : 0);
        }

        [MenuItem("Tools/Uasset Converter/Import", false, 20)]
        public static void Open()
        {
            var window = GetWindow<UassetImportWindow>(Loc.T("unity.import.window_title"));
            window.minSize = new Vector2(540, 520);
        }

        void OnEnable()
        {
            _outputDir = EditorPrefs.GetString(OutputKey, "");
            _targetRoot = EditorPrefs.GetString(TargetKey, "Assets/UassetConverted");

            _selection.textures = EditorPrefs.GetBool(SelPrefix + "textures", true);
            _selection.materials = EditorPrefs.GetBool(SelPrefix + "materials", true);
            _selection.meshes = EditorPrefs.GetBool(SelPrefix + "meshes", true);
            _selection.skeletal = EditorPrefs.GetBool(SelPrefix + "skeletal", true);
            _selection.animations = EditorPrefs.GetBool(SelPrefix + "animations", true);

            RefreshLanguages();
        }

        void RefreshLanguages()
        {
            var codes = Loc.AvailableCodes();
            _languages = codes.ToArray();
            _languageNames = new string[_languages.Length];
            for (var i = 0; i < _languages.Length; i++)
                _languageNames[i] = Loc.DisplayName(_languages[i]);
        }

        void OnGUI()
        {
            DrawLanguage();
            EditorGUILayout.Space();

            EditorGUILayout.LabelField(Loc.T("unity.import.header"), EditorStyles.boldLabel);
            EditorGUILayout.Space();

            using (new EditorGUILayout.HorizontalScope())
            {
                _outputDir = EditorGUILayout.TextField(Loc.T("unity.import.output_dir"), _outputDir);
                if (GUILayout.Button(Loc.T("unity.browse"), GUILayout.Width(90)))
                {
                    var picked = EditorUtility.OpenFolderPanel(
                        Loc.T("unity.import.pick_output"), _outputDir, "");
                    if (!string.IsNullOrEmpty(picked)) _outputDir = picked;
                }
            }

            _targetRoot = EditorGUILayout.TextField(Loc.T("unity.import.target_root"), _targetRoot);
            _avatarType = EditorGUILayout.Popup(Loc.T("unity.import.avatar"),
                _avatarType == "Humanoid" ? 1 : 0,
                new[] { "Generic", "Humanoid" }) == 1 ? "Humanoid" : "Generic";
            _overwriteExisting = EditorGUILayout.Toggle(Loc.T("unity.import.overwrite"),
                                                        _overwriteExisting);

            EditorGUILayout.Space();

            var manifestPath = ManifestPathFor(_outputDir);
            var ready = manifestPath != null && File.Exists(manifestPath);

            if (!ready && !string.IsNullOrEmpty(_outputDir))
                EditorGUILayout.HelpBox(Loc.T("unity.import.no_manifest"), MessageType.Warning);

            DrawWhat(ready ? TryCounts(manifestPath) : (int[])null);

            EditorGUILayout.Space();

            using (new EditorGUI.DisabledScope(!ready || !_selection.Any))
            {
                if (GUILayout.Button(Loc.T("unity.import.run"), GUILayout.Height(30)))
                    Run(manifestPath);
            }

            EditorGUILayout.Space();
            EditorGUILayout.LabelField(Loc.T("unity.log"), EditorStyles.boldLabel);
            using (var scroll = new EditorGUILayout.ScrollViewScope(_logScroll))
            {
                _logScroll = scroll.scrollPosition;
                foreach (var line in _log)
                    EditorGUILayout.LabelField(line, EditorStyles.wordWrappedMiniLabel);
            }
        }

        void DrawLanguage()
        {
            if (_languages == null || _languages.Length == 0) RefreshLanguages();

            var current = Loc.Current;
            var index = Array.IndexOf(_languages, current);
            if (index < 0) index = 0;

            using (new EditorGUILayout.HorizontalScope())
            {
                var picked = EditorGUILayout.Popup(Loc.T("unity.language"), index, _languageNames);
                if (picked != index)
                {
                    Loc.SetLanguage(_languages[picked]);
                    Repaint();
                }
            }
        }

        /// <summary>
        /// Блок «Что импортировать» — галочки по категориям с числом ассетов из
        /// манифеста. counts может быть null (манифест ещё не прочитан) — тогда
        /// счётчики не показываем.
        /// </summary>
        void DrawWhat(int[] counts)
        {
            EditorGUILayout.LabelField(Loc.T("unity.import.what"), EditorStyles.boldLabel);

            using (new EditorGUILayout.HorizontalScope())
            {
                if (GUILayout.Button(Loc.T("unity.import.select_all"), GUILayout.Width(120)))
                    SetSelection(ImportSelection.All());
                if (GUILayout.Button(Loc.T("unity.import.select_none"), GUILayout.Width(120)))
                    SetSelection(default);
            }

            _selection.textures = Category("textures", "unity.import.cat_textures",
                                           _selection.textures, counts, 0);
            _selection.materials = Category("materials", "unity.import.cat_materials",
                                            _selection.materials, counts, 4);
            _selection.meshes = Category("meshes", "unity.import.cat_meshes",
                                         _selection.meshes, counts, 1);
            _selection.skeletal = Category("skeletal", "unity.import.cat_skeletal",
                                           _selection.skeletal, counts, 2);
            _selection.animations = Category("animations", "unity.import.cat_animations",
                                             _selection.animations, counts, 3);
        }

        bool Category(string key, string label, bool value, int[] counts, int countIndex)
        {
            var text = counts != null
                ? Loc.T(label, "count", counts[countIndex])
                : Loc.T(label, "count", "?");
            var next = EditorGUILayout.ToggleLeft(text, value);
            if (next != value)
                EditorPrefs.SetBool(SelPrefix + key, next);
            return next;
        }

        void SetSelection(ImportSelection selection)
        {
            _selection = selection;
            EditorPrefs.SetBool(SelPrefix + "textures", selection.textures);
            EditorPrefs.SetBool(SelPrefix + "materials", selection.materials);
            EditorPrefs.SetBool(SelPrefix + "meshes", selection.meshes);
            EditorPrefs.SetBool(SelPrefix + "skeletal", selection.skeletal);
            EditorPrefs.SetBool(SelPrefix + "animations", selection.animations);
        }

        /// <summary>
        /// Число ассетов по категориям для подписей галочек. Индексы совпадают с
        /// countIndex в DrawWhat: [0]=текстуры [1]=меши [2]=скелетные [3]=клипы
        /// [4]=материалы. При нечитаемом манифесте возвращает null — счётчики
        /// просто не рисуются, окно не должно падать из-за битого файла.
        /// </summary>
        static int[] TryCounts(string manifestPath)
        {
            try
            {
                var manifest = UassetManifest.Load(File.ReadAllText(manifestPath));
                return new[]
                {
                    manifest.textures.Length,
                    manifest.meshes.Length,
                    manifest.skeletalMeshes.Length,
                    manifest.animations.Length,
                    manifest.materials.Length,
                };
            }
            catch
            {
                return null;
            }
        }

        public static string ManifestPathFor(string outputDir)
        {
            return string.IsNullOrEmpty(outputDir)
                ? null : Path.Combine(outputDir, "unity_manifest.json");
        }

        void Log(string message)
        {
            _log.Add(message);
            Debug.Log($"[Uasset] {message}");
            Repaint();
        }

        void Run(string manifestPath)
        {
            _log.Clear();
            EditorPrefs.SetString(OutputKey, _outputDir);
            EditorPrefs.SetString(TargetKey, _targetRoot);
            Import(manifestPath, _outputDir, _targetRoot, _avatarType, _overwriteExisting,
                   _selection, Log);
        }

        /// <summary>
        /// Импорт как отдельная операция, а не метод окна. Категории, которые
        /// нужно импортировать, задаёт selection — так повторный запуск добирает
        /// только выбранное, а не переимпортирует всё.
        /// </summary>
        public static void Import(string manifestPath, string outputDir, string targetRoot,
                                  string avatarType, bool overwriteExisting,
                                  ImportSelection selection, Action<string> log)
        {
            if (!selection.Any)
            {
                log(Loc.T("unity.import.nothing_selected"));
                return;
            }

            UassetManifest manifest;
            try
            {
                manifest = UassetManifest.Load(File.ReadAllText(manifestPath));
            }
            catch (Exception error)
            {
                log(Loc.T("unity.import.manifest_unreadable", "error", error.Message));
                return;
            }

            log(Loc.T("unity.import.manifest_read",
                      "textures", manifest.textures.Length,
                      "meshes", manifest.meshes.Length,
                      "skeletal", manifest.skeletalMeshes.Length,
                      "clips", manifest.animations.Length,
                      "materials", manifest.materials.Length));

            var avatarBySkeleton = new Dictionary<string, string>();
            // Клипам нужен аватар скелетного меша. Если анимации выбраны без
            // скелетных мешей, аватары ищем среди уже импортированных моделей —
            // путь ассета детерминирован (PathMap), поэтому знаем, куда смотреть.
            if (selection.animations && !selection.skeletal)
            {
                foreach (var mesh in manifest.skeletalMeshes)
                    if (!string.IsNullOrEmpty(mesh.skeleton) &&
                        !avatarBySkeleton.ContainsKey(mesh.skeleton))
                        avatarBySkeleton[mesh.skeleton] =
                            PathMap.AssetPathFor(targetRoot, mesh.file);
                if (manifest.skeletalMeshes.Length > 0)
                    log(Loc.T("unity.import.avatars_from_existing"));
            }

            var totalSteps = Math.Max(1, selection.StepCount);
            var step = 0;

            try
            {
                // Копирование и первичный импорт — одной пачкой. Без
                // StartAssetEditing Unity переимпортирует каждый файл по
                // отдельности, и на сотнях мешей это занимает часы.
                AssetDatabase.StartAssetEditing();
                try
                {
                    var copied = CopyFiles(manifest, outputDir, targetRoot,
                                           overwriteExisting, selection, log);
                    log(Loc.T("unity.import.copied", "count", copied));
                }
                finally
                {
                    AssetDatabase.StopAssetEditing();
                }
                AssetDatabase.Refresh();

                if (selection.textures)
                {
                    Progress(ref step, totalSteps, "unity.import.step_textures");
                    var textures = TextureSettings.Apply(manifest, targetRoot, log);
                    log(Loc.T("unity.import.textures_done", "count", textures));
                }

                var materials = new Dictionary<string, Material>();
                if (selection.materials)
                {
                    Progress(ref step, totalSteps, "unity.import.step_materials");
                    materials = MaterialBuilder.Build(manifest, targetRoot, log,
                        out var shaderMode, out var fallbackMode, out var failed);
                    log(Loc.T("unity.import.materials_done",
                              "count", materials.Count, "shader", shaderMode,
                              "fallback", fallbackMode, "failed", failed));
                }
                else if (selection.meshes || selection.skeletal)
                {
                    // Мешам нужны материалы для привязки к слотам. Если материалы
                    // в этот прогон не собираем — берём уже лежащие в проекте.
                    materials = MaterialBuilder.LoadExisting(manifest, targetRoot);
                }

                if (selection.meshes)
                {
                    Progress(ref step, totalSteps, "unity.import.step_models");
                    var meshes = ModelImportSettings.ApplyStatic(manifest, targetRoot, materials, log);
                    log(Loc.T("unity.import.meshes_done", "count", meshes));
                }

                if (selection.skeletal)
                {
                    Progress(ref step, totalSteps, "unity.import.step_skeletal");
                    var skeletal = ModelImportSettings.ApplySkeletal(
                        manifest, targetRoot, materials, avatarType, avatarBySkeleton, log);
                    log(Loc.T("unity.import.skeletal_done", "count", skeletal));
                }

                if (selection.animations)
                {
                    Progress(ref step, totalSteps, "unity.import.step_animations");
                    var clips = AnimationImportSettings.Apply(manifest, targetRoot,
                                                              avatarBySkeleton, log);
                    log(Loc.T("unity.import.clips_done", "count", clips));
                }

                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();
                log(Loc.T("unity.import.finished"));
            }
            catch (Exception error)
            {
                log(Loc.T("unity.import.aborted", "error", error));
            }
            finally
            {
                EditorUtility.ClearProgressBar();
            }
        }

        static void Progress(ref int step, int total, string labelKey)
        {
            step++;
            EditorUtility.DisplayProgressBar(Loc.T("unity.import.window_title"),
                                             Loc.T(labelKey), (float)step / total);
        }

        /// <summary>
        /// Копирует файлы выбранных категорий, сохраняя структуру папок Unreal.
        /// Шейдеры и шейдер-графы копируются вместе с материалами — материалам
        /// нужно, на что садиться.
        /// </summary>
        static int CopyFiles(UassetManifest manifest, string outputDir, string targetRoot,
                             bool overwriteExisting, ImportSelection selection,
                             Action<string> log)
        {
            var files = new List<string>();
            if (selection.textures)
                foreach (var texture in manifest.textures) files.Add(texture.file);
            if (selection.meshes)
                foreach (var mesh in manifest.meshes) files.Add(mesh.file);
            if (selection.skeletal)
                foreach (var mesh in manifest.skeletalMeshes) files.Add(mesh.file);
            if (selection.animations)
                foreach (var animation in manifest.animations) files.Add(animation.file);
            if (selection.materials)
            {
                foreach (var shader in manifest.shaders)
                    if (!string.IsNullOrEmpty(shader.file)) files.Add(shader.file);
                foreach (var graph in manifest.shadergraphs)
                    if (!string.IsNullOrEmpty(graph.file)) files.Add(graph.file);
            }

            var copied = 0;
            foreach (var relative in files)
            {
                if (string.IsNullOrEmpty(relative)) continue;

                var source = Path.Combine(outputDir, relative.Replace('/', Path.DirectorySeparatorChar));
                if (!File.Exists(source))
                {
                    log(Loc.T("unity.import.source_missing", "path", source));
                    continue;
                }

                var destination = Path.Combine(
                    Directory.GetCurrentDirectory(),
                    PathMap.AssetPathFor(targetRoot, relative).Replace('/', Path.DirectorySeparatorChar));

                if (File.Exists(destination) && !overwriteExisting)
                    continue;

                Directory.CreateDirectory(Path.GetDirectoryName(destination));
                File.Copy(source, destination, true);
                copied++;
            }

            return copied;
        }
    }
}
