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
    /// Порядок шагов не произволен:
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

        string _outputDir = "";
        string _targetRoot = "Assets/UassetConverted";
        string _avatarType = "Generic";
        bool _overwriteExisting = true;
        Vector2 _logScroll;
        readonly List<string> _log = new List<string>();

        [MenuItem("Tools/Uasset Converter/Import", false, 20)]
        public static void Open()
        {
            var window = GetWindow<UassetImportWindow>(Loc.T("unity.import.window_title"));
            window.minSize = new Vector2(540, 420);
        }

        void OnEnable()
        {
            _outputDir = EditorPrefs.GetString(OutputKey, "");
            _targetRoot = EditorPrefs.GetString(TargetKey, "Assets/UassetConverted");
        }

        void OnGUI()
        {
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

            using (new EditorGUI.DisabledScope(!ready))
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
            Import(manifestPath, _outputDir, _targetRoot, _avatarType, _overwriteExisting, Log);
        }

        /// <summary>
        /// Импорт как отдельная операция, а не метод окна: то же самое вызывает
        /// окно конвертера сразу после успешной конвертации, чтобы пользователю
        /// не приходилось открывать второе окно и нажимать вторую кнопку.
        /// </summary>
        public static void Import(string manifestPath, string outputDir, string targetRoot,
                                  string avatarType, bool overwriteExisting,
                                  Action<string> log)
        {
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

            try
            {
                // Копирование и первичный импорт — одной пачкой. Без
                // StartAssetEditing Unity переимпортирует каждый файл по
                // отдельности, и на сотнях мешей это занимает часы.
                AssetDatabase.StartAssetEditing();
                try
                {
                    var copied = CopyFiles(manifest, outputDir, targetRoot,
                                           overwriteExisting, log);
                    log(Loc.T("unity.import.copied", "count", copied));
                }
                finally
                {
                    AssetDatabase.StopAssetEditing();
                }
                AssetDatabase.Refresh();

                EditorUtility.DisplayProgressBar(Loc.T("unity.import.window_title"),
                                                 Loc.T("unity.import.step_textures"), 0.3f);
                var textures = TextureSettings.Apply(manifest, targetRoot, log);
                log(Loc.T("unity.import.textures_done", "count", textures));

                EditorUtility.DisplayProgressBar(Loc.T("unity.import.window_title"),
                                                 Loc.T("unity.import.step_materials"), 0.5f);
                var materials = MaterialBuilder.Build(manifest, targetRoot, log,
                    out var shaderMode, out var fallbackMode, out var failed);
                log(Loc.T("unity.import.materials_done",
                          "count", materials.Count, "shader", shaderMode,
                          "fallback", fallbackMode, "failed", failed));

                EditorUtility.DisplayProgressBar(Loc.T("unity.import.window_title"),
                                                 Loc.T("unity.import.step_models"), 0.7f);
                var meshes = ModelImportSettings.ApplyStatic(manifest, targetRoot, materials, log);
                log(Loc.T("unity.import.meshes_done", "count", meshes));

                var skeletal = ModelImportSettings.ApplySkeletal(
                    manifest, targetRoot, materials, avatarType, avatarBySkeleton, log);
                log(Loc.T("unity.import.skeletal_done", "count", skeletal));

                EditorUtility.DisplayProgressBar(Loc.T("unity.import.window_title"),
                                                 Loc.T("unity.import.step_animations"), 0.9f);
                var clips = AnimationImportSettings.Apply(manifest, targetRoot,
                                                          avatarBySkeleton, log);
                log(Loc.T("unity.import.clips_done", "count", clips));

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

        /// <summary>
        /// Копирует всё, что перечислено в манифесте, плюс шейдеры, сохраняя
        /// структуру папок Unreal — так в проекте легко найти исходник ассета.
        /// </summary>
        static int CopyFiles(UassetManifest manifest, string outputDir, string targetRoot,
                             bool overwriteExisting, Action<string> log)
        {
            var files = new List<string>();
            foreach (var texture in manifest.textures) files.Add(texture.file);
            foreach (var mesh in manifest.meshes) files.Add(mesh.file);
            foreach (var mesh in manifest.skeletalMeshes) files.Add(mesh.file);
            foreach (var animation in manifest.animations) files.Add(animation.file);
            foreach (var shader in manifest.shaders)
                if (!string.IsNullOrEmpty(shader.file)) files.Add(shader.file);

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
