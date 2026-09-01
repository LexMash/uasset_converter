using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace UassetImporter
{
    /// <summary>
    /// Отдельный шаг: сборка Unity-сцен из уровней Unreal.
    ///
    /// Вынесен из обычного Import сознательно — сцена меняет открытый в редакторе
    /// контент, и запускать её пользователь должен явно. Меши и материалы к этому
    /// моменту уже должны быть импортированы обычным Import; сам шаг их не создаёт,
    /// а только расставляет по местам.
    /// </summary>
    public class LevelImportWindow : EditorWindow
    {
        // Те же ключи, что у окна импорта, — чтобы папки совпадали и пользователю
        // не приходилось вводить пути дважды.
        const string OutputKey = "UassetImporter.OutputDir";
        const string TargetKey = "UassetImporter.TargetRoot";

        string _outputDir = "";
        string _targetRoot = "Assets/UassetConverted";
        Vector2 _logScroll;
        readonly List<string> _log = new List<string>();

        [MenuItem("Tools/Uasset Converter/Import Levels", false, 21)]
        public static void Open()
        {
            var window = GetWindow<LevelImportWindow>(Loc.T("unity.level.window_title"));
            window.minSize = new Vector2(540, 420);
        }

        void OnEnable()
        {
            _outputDir = EditorPrefs.GetString(OutputKey, "");
            _targetRoot = EditorPrefs.GetString(TargetKey, "Assets/UassetConverted");
        }

        void OnGUI()
        {
            EditorGUILayout.LabelField(Loc.T("unity.level.header"), EditorStyles.boldLabel);
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

            EditorGUILayout.Space();
            EditorGUILayout.HelpBox(Loc.T("unity.level.hint"), MessageType.Info);

            var manifestPath = UassetImportWindow.ManifestPathFor(_outputDir);
            var ready = manifestPath != null && File.Exists(manifestPath);

            if (!ready && !string.IsNullOrEmpty(_outputDir))
                EditorGUILayout.HelpBox(Loc.T("unity.import.no_manifest"), MessageType.Warning);

            using (new EditorGUI.DisabledScope(!ready))
            {
                if (GUILayout.Button(Loc.T("unity.level.run"), GUILayout.Height(30)))
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

            UassetManifest manifest;
            try
            {
                manifest = UassetManifest.Load(File.ReadAllText(manifestPath));
            }
            catch (Exception error)
            {
                Log(Loc.T("unity.import.manifest_unreadable", "error", error.Message));
                return;
            }

            if (manifest.levels.Length == 0)
            {
                Log(Loc.T("unity.level.none"));
                return;
            }

            // Материалы уже созданы обычным Import — берём готовые, не пересоздаём.
            var materials = MaterialBuilder.LoadExisting(manifest, _targetRoot);

            try
            {
                EditorUtility.DisplayProgressBar(Loc.T("unity.level.window_title"),
                                                 Loc.T("unity.level.step"), 0.5f);
                var built = SceneBuilder.Build(manifest, _outputDir, _targetRoot, materials, Log);
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();
                Log(Loc.T("unity.level.finished", "count", built));
            }
            catch (Exception error)
            {
                Log(Loc.T("unity.import.aborted", "error", error));
            }
            finally
            {
                EditorUtility.ClearProgressBar();
            }
        }
    }
}
