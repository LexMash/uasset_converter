using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

namespace UassetImporter
{
    /// <summary>
    /// Прогон импорта из командной строки без открытия редактора.
    ///
    /// Нужен для проверки: сгенерированные шейдеры должны компилироваться, а
    /// материалы — собираться. Ошибку компиляции шейдера иначе видно только
    /// глазами в инспекторе, а это плохая обратная связь для конвертера,
    /// который генерирует их пачками.
    ///
    /// Пояснительный текст локализуется, машинные маркеры — нет. Строку
    /// RESULT: OK и префикс [UASSET-VERIFY] читает CI, и они обязаны остаться
    /// одинаковыми на любом языке, иначе по логу нельзя грепать. Язык берётся
    /// из -uassetLang, иначе из настроек редактора.
    ///
    /// Запуск:
    ///   Unity.exe -batchmode -quit -projectPath ... \
    ///     -executeMethod UassetImporter.UassetBatchVerify.Run \
    ///     -uassetOutput "C:\...\output" [-uassetLang ru]
    /// </summary>
    public static class UassetBatchVerify
    {
        const string TargetRoot = "Assets/UassetConverted";

        public static void Run()
        {
            // Язык — до первого Say. На машине CI настроек редактора нет,
            // поэтому аргумент важнее их, а не наоборот.
            var language = ArgumentValue("-uassetLang");
            if (!string.IsNullOrEmpty(language)) Loc.SetLanguage(language);

            var outputDir = ArgumentValue("-uassetOutput");
            if (string.IsNullOrEmpty(outputDir))
            {
                Fail(Loc.T("verify.no_output_arg"));
                return;
            }

            var manifestPath = Path.Combine(outputDir, "unity_manifest.json");
            if (!File.Exists(manifestPath))
            {
                Fail(Loc.T("verify.no_manifest", "path", manifestPath));
                return;
            }

            var report = new List<string>();
            void Log(string message) => report.Add(message);

            UassetManifest manifest;
            try
            {
                manifest = UassetManifest.Load(File.ReadAllText(manifestPath));
            }
            catch (Exception error)
            {
                Fail(Loc.T("verify.manifest_unreadable", "error", error.Message));
                return;
            }

            Say(Loc.T("verify.pipeline_from_manifest", "pipeline", manifest.pipeline));
            Say(Loc.T("verify.active_pipeline", "pipeline",
                      GraphicsSettings.currentRenderPipeline != null
                          ? GraphicsSettings.currentRenderPipeline.GetType().Name
                          : "Built-in"));

            CopyAll(manifest, outputDir, Log);
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

            // Здесь ловятся только ошибки импорта и разбора ShaderLab. Сам HLSL
            // компилируется лениво — когда впервые понадобился вариант, — так
            // что настоящие ошибки кода видит рендер-проверка, а не эта.
            var shaderErrors = CheckShaders(manifest);

            var textures = TextureSettings.Apply(manifest, TargetRoot, Log);
            Say(Loc.T("verify.textures_configured", "count", textures));

            var materials = MaterialBuilder.Build(manifest, TargetRoot, Log,
                out var shaderMode, out var fallbackMode, out var failed);
            Say(Loc.T("verify.materials", "count", materials.Count, "shader", shaderMode,
                      "fallback", fallbackMode, "failed", failed));

            var avatars = new Dictionary<string, string>();
            var meshes = ModelImportSettings.ApplyStatic(manifest, TargetRoot, materials, Log);
            var skeletal = ModelImportSettings.ApplySkeletal(manifest, TargetRoot, materials,
                                                             "Generic", avatars, Log);
            var clips = AnimationImportSettings.Apply(manifest, TargetRoot, avatars, Log);
            Say(Loc.T("verify.meshes", "meshes", meshes, "skeletal", skeletal,
                      "clips", clips));

            AssetDatabase.SaveAssets();

            var missingMaterials = CheckMaterials(manifest, materials);
            var unboundSlots = CheckMeshBindings(manifest, materials);

            Say(Loc.T("verify.import_log"));
            foreach (var line in report) Say("  " + line);

            Say(Loc.T("verify.summary"));
            Say(Loc.T("verify.shaders_with_errors", "count", shaderErrors));
            Say(Loc.T("verify.hlsl_not_checked", "method",
                      "UassetImporter.Verify.UassetRenderCheck.Run"));
            Say(Loc.T("verify.materials_without_shader", "count", missingMaterials));
            Say(Loc.T("verify.models_unbound", "count", unboundSlots));

            var ok = shaderErrors == 0 && missingMaterials == 0 && failed == 0 && unboundSlots == 0;
            // Не локализуется намеренно: по этой строке грепает CI.
            Say(ok ? "RESULT: OK" : "RESULT: PROBLEMS FOUND");

            EditorApplication.Exit(ok ? 0 : 1);
        }

        static int CheckShaders(UassetManifest manifest)
        {
            var withErrors = 0;

            foreach (var entry in manifest.shaders)
            {
                if (string.IsNullOrEmpty(entry.file)) continue;
                var assetPath = PathMap.AssetPathFor(TargetRoot, entry.file);
                var shader = AssetDatabase.LoadAssetAtPath<Shader>(assetPath);

                if (shader == null)
                {
                    Say(Loc.T("verify.shader.failed_to_load", "path", assetPath));
                    withErrors++;
                    continue;
                }

                var messages = ShaderUtil.GetShaderMessages(shader);
                var errors = messages.Where(m => m.severity == UnityEditor.Rendering.ShaderCompilerMessageSeverity.Error).ToArray();
                var warnings = messages.Length - errors.Length;

                if (errors.Length > 0)
                {
                    withErrors++;
                    Say(Loc.T("verify.shader.errors_in", "shader", entry.shader));
                    foreach (var message in errors.Take(10))
                        Say(Loc.T("verify.shader.error_line", "line", message.line,
                              "message", $"{message.message} {message.messageDetails}"));
                }
                else
                {
                    Say(Loc.T("verify.shader.imported", "shader", entry.shader)
                        + (warnings > 0 ? Loc.T("verify.shader.warnings", "count", warnings) : ""));
                }

                // Shader.Find обязан находить шейдер по имени: именно так его
                // ищет MaterialBuilder, и опечатка в имени всплывёт здесь.
                if (Shader.Find(entry.shader) == null)
                {
                    Say(Loc.T("verify.shader.find_returned_null", "shader", entry.shader));
                    withErrors++;
                }
            }

            return withErrors;
        }

        /// <summary>
        /// Главная проверка привязки: на импортированной модели должны стоять
        /// ИМЕННО наши материалы.
        ///
        /// Появилась после реального бага: таблица ремапов честно писалась в
        /// .meta, но при materialImportMode = None Unity её игнорировала, и меши
        /// приезжали серыми. Все остальные проверки при этом рапортовали успех —
        /// материалы созданы, шейдеры компилируются, ремапы записаны.
        /// </summary>
        static int CheckMeshBindings(UassetManifest manifest, Dictionary<string, Material> materials)
        {
            var ours = new HashSet<Material>(materials.Values);
            var problems = 0;

            var models = manifest.meshes.Select(m => (m.uePath, m.file))
                .Concat(manifest.skeletalMeshes.Select(m => (m.uePath, m.file)));

            foreach (var (uePath, file) in models)
            {
                var assetPath = PathMap.AssetPathFor(TargetRoot, file);
                var root = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
                if (root == null)
                {
                    Say(Loc.T("verify.binding.model_failed", "path", assetPath));
                    problems++;
                    continue;
                }

                var slots = 0;
                var bound = 0;
                foreach (var renderer in root.GetComponentsInChildren<Renderer>(true))
                {
                    foreach (var material in renderer.sharedMaterials)
                    {
                        slots++;
                        if (material == null)
                            Say(Loc.T("verify.binding.empty_slot", "model", MaterialBuilder.ShortName(uePath)));
                        else if (!ours.Contains(material))
                            Say(Loc.T("verify.binding.foreign_material",
                                      "model", MaterialBuilder.ShortName(uePath),
                                      "material", material.name));
                        else
                            bound++;
                    }
                }

                if (slots == 0)
                {
                    Say(Loc.T("verify.binding.no_renderers", "model", MaterialBuilder.ShortName(uePath)));
                    problems++;
                }
                else if (bound < slots)
                {
                    problems++;
                }
                else
                {
                    Say(Loc.T("verify.binding.ok", "model", MaterialBuilder.ShortName(uePath),
                              "count", bound));
                }
            }

            return problems;
        }

        static int CheckMaterials(UassetManifest manifest, Dictionary<string, Material> materials)
        {
            var problems = 0;
            foreach (var entry in manifest.materials)
            {
                if (!materials.TryGetValue(entry.uePath, out var material) || material == null)
                {
                    Say(Loc.T("verify.material.not_created", "path", entry.uePath));
                    problems++;
                    continue;
                }
                if (material.shader == null || material.shader.name == "Hidden/InternalErrorShader")
                {
                    Say(Loc.T("verify.material.broken_shader", "path", entry.uePath));
                    problems++;
                }
            }
            return problems;
        }

        static void CopyAll(UassetManifest manifest, string outputDir, Action<string> log)
        {
            var files = new List<string>();
            files.AddRange(manifest.textures.Select(t => t.file));
            files.AddRange(manifest.meshes.Select(m => m.file));
            files.AddRange(manifest.skeletalMeshes.Select(m => m.file));
            files.AddRange(manifest.animations.Select(a => a.file));
            files.AddRange(manifest.shaders.Select(s => s.file));

            var copied = 0;
            foreach (var relative in files.Where(f => !string.IsNullOrEmpty(f)))
            {
                var source = Path.Combine(outputDir, relative.Replace('/', Path.DirectorySeparatorChar));
                if (!File.Exists(source)) continue;

                var destination = Path.Combine(Directory.GetCurrentDirectory(),
                    $"{TargetRoot}/{relative}".Replace('/', Path.DirectorySeparatorChar));
                Directory.CreateDirectory(Path.GetDirectoryName(destination));
                File.Copy(source, destination, true);
                copied++;
            }
            log(Loc.T("verify.files_copied", "count", copied));
        }

        static string ArgumentValue(string name)
        {
            var args = Environment.GetCommandLineArgs();
            for (var i = 0; i < args.Length - 1; i++)
                if (args[i] == name)
                    return args[i + 1];
            return null;
        }

        static void Say(string message) => Debug.Log($"[UASSET-VERIFY] {message}");

        static void Fail(string message)
        {
            Say(message);
            Say("RESULT: PROBLEMS FOUND");
            EditorApplication.Exit(2);
        }
    }
}
