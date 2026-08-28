using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace UassetImporter.Verify
{
    /// <summary>
    /// Визуальная проверка: включает URP, собирает сцену из сконвертированных
    /// мешей и рендерит её в PNG.
    ///
    /// Проверка компиляции шейдеров отвечает только на вопрос «собирается ли».
    /// Ответ на «выглядит ли правильно» — направление нормалей, металличность,
    /// свечение — можно получить только картинкой.
    ///
    /// Лежит отдельно от импортёра и в своей сборке: импортёр обязан работать
    /// и в HDRP-проекте, а этот файл жёстко завязан на URP.
    ///
    /// Пояснительный текст локализуется, машинные маркеры — нет: строку
    /// RESULT: OK и префикс [UASSET-VERIFY] грепает CI. Язык берётся из
    /// -uassetLang, иначе из настроек редактора.
    /// </summary>
    public static class UassetRenderCheck
    {
        const string TargetRoot = "Assets/UassetConverted";
        const string PipelineAssetPath = "Assets/VerifyURP.asset";
        const string RendererDataPath = "Assets/VerifyURP_Renderer.asset";

        public static void Run()
        {
            // Язык — до первого Say: на машине CI настроек редактора нет.
            var language = ArgumentValue("-uassetLang");
            if (!string.IsNullOrEmpty(language)) Loc.SetLanguage(language);

            var outputPath = ArgumentValue("-uassetShot") ?? "verify_shot.png";

            try
            {
                // Синхронная компиляция: иначе первый кадр рисуется заглушкой,
                // и по картинке не отличить «шейдер ещё компилируется» от
                // «шейдер сломан».
                EditorSettings.asyncShaderCompilation = false;
                ShaderUtil.allowAsyncCompilation = false;

                EnsureUrpActive();
                var scene = BuildScene(out var camera);

                // Первый рендер нужен именно как компиляция: HLSL собирается
                // не при импорте шейдера, а когда впервые понадобился вариант.
                // Поэтому спрашивать про ошибки можно только ПОСЛЕ рендера.
                Render(camera, 64, 64);
                var shaderErrors = ReportShaderErrors();

                var image = Render(camera, 1280, 720);
                File.WriteAllBytes(outputPath, image.EncodeToPNG());
                Say(Loc.T("verify.render.screenshot_saved", "path", outputPath));

                // Сферы показывают материалы, но не отвечают на вопрос, сели ли
                // они на слоты настоящих мешей. Второй кадр — про это.
                var modelsPath = Path.Combine(Path.GetDirectoryName(outputPath) ?? ".",
                    Path.GetFileNameWithoutExtension(outputPath) + "_models.png");
                var placed = BuildModelScene(out var modelCamera);
                if (placed > 0)
                {
                    Render(modelCamera, 64, 64);
                    var modelImage = Render(modelCamera, 1280, 720);
                    File.WriteAllBytes(modelsPath, modelImage.EncodeToPNG());
                    Say(Loc.T("verify.render.models_screenshot", "path", modelsPath,
                                  "count", placed));
                }
                else
                {
                    Say(Loc.T("verify.render.no_models"));
                }
                Say(Loc.T("verify.render.objects_in_frame", "count", scene));
                Say(Loc.T("verify.render.shaders_with_errors", "count", shaderErrors));
                // Не локализуется намеренно: по этой строке грепает CI.
                Say(shaderErrors == 0 ? "RESULT: OK" : "RESULT: PROBLEMS FOUND");
                EditorApplication.Exit(shaderErrors == 0 ? 0 : 1);
            }
            catch (Exception error)
            {
                Say(Loc.T("verify.render.failure", "error", error));
                Say("RESULT: PROBLEMS FOUND");
                EditorApplication.Exit(1);
            }
        }

        /// <summary>
        /// Пустой проект создаётся без URP-ассета, и без него шейдеры пайплайна
        /// просто не рисуются. Создаём и назначаем его сами.
        /// </summary>
        static void EnsureUrpActive()
        {
            var pipeline = AssetDatabase.LoadAssetAtPath<UniversalRenderPipelineAsset>(PipelineAssetPath);
            if (pipeline == null)
            {
                var rendererData = ScriptableObject.CreateInstance<UniversalRendererData>();
                AssetDatabase.CreateAsset(rendererData, RendererDataPath);

                pipeline = UniversalRenderPipelineAsset.Create(rendererData);
                AssetDatabase.CreateAsset(pipeline, PipelineAssetPath);
                AssetDatabase.SaveAssets();
            }

            GraphicsSettings.defaultRenderPipeline = pipeline;
            QualitySettings.renderPipeline = pipeline;
            Say(Loc.T("verify.active_pipeline", "pipeline",
                      GraphicsSettings.currentRenderPipeline != null
                          ? GraphicsSettings.currentRenderPipeline.GetType().Name
                          : "Built-in"));
        }

        static int BuildScene(out Camera camera)
        {
            var light = new GameObject("Sun").AddComponent<Light>();
            light.type = LightType.Directional;
            light.intensity = 1.6f;
            light.transform.rotation = Quaternion.Euler(38f, -140f, 0f);
            light.shadows = LightShadows.Soft;

            RenderSettings.ambientMode = AmbientMode.Flat;
            RenderSettings.ambientLight = new Color(0.18f, 0.20f, 0.24f);

            var cameraObject = new GameObject("Camera");
            camera = cameraObject.AddComponent<Camera>();
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.09f, 0.10f, 0.12f);
            camera.fieldOfView = 45f;

            // Ставим сферы с материалами в ряд: на шаре видно и металличность,
            // и работу нормалей, и свечение — на кубе половина этого теряется.
            var materials = AssetDatabase.FindAssets("t:Material", new[] { $"{TargetRoot}/Materials" })
                .Select(AssetDatabase.GUIDToAssetPath)
                .Select(AssetDatabase.LoadAssetAtPath<Material>)
                .Where(m => m != null)
                .OrderBy(m => m.name)
                .Take(12)
                .ToList();

            // Розовая сфера в кадре ничего не объясняет сама по себе — нужно
            // знать, какой материал на каком шейдере и поддерживается ли он.
            foreach (var material in materials)
            {
                var shaderName = material.shader != null
                    ? material.shader.name : Loc.T("verify.render.no_shader");
                var supported = material.shader != null && material.shader.isSupported;
                var messages = material.shader != null
                    ? UnityEditor.ShaderUtil.GetShaderMessageCount(material.shader) : -1;
                Say(Loc.T("verify.render.material_row",
                          "material", $"{material.name,-28}", "shader", shaderName,
                          "supported", supported, "messages", messages));
            }

            var columns = Mathf.CeilToInt(Mathf.Sqrt(materials.Count));
            for (var i = 0; i < materials.Count; i++)
            {
                var sphere = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                sphere.name = materials[i].name;
                sphere.GetComponent<Renderer>().sharedMaterial = materials[i];
                var row = i / columns;
                var column = i % columns;
                sphere.transform.position = new Vector3((column - (columns - 1) * 0.5f) * 1.25f,
                                                        -row * 1.25f, 0f);
            }

            var rows = Mathf.CeilToInt(materials.Count / (float)columns);
            var center = new Vector3(0f, -(rows - 1) * 0.625f, 0f);
            camera.transform.position = center + new Vector3(0f, 0f, -Mathf.Max(columns, rows) * 1.9f - 1.5f);
            camera.transform.LookAt(center);

            return materials.Count;
        }

        /// <summary>
        /// Собирает ошибки компиляции по всем сгенерированным шейдерам.
        /// Вызывать строго после рендера — до него список пуст, потому что
        /// варианты ещё не собирались, и проверка молча рапортует «всё хорошо».
        /// </summary>
        static int ReportShaderErrors()
        {
            var withErrors = 0;

            foreach (var guid in AssetDatabase.FindAssets("t:Shader", new[] { $"{TargetRoot}/Shaders" }))
            {
                var path = AssetDatabase.GUIDToAssetPath(guid);
                var shader = AssetDatabase.LoadAssetAtPath<Shader>(path);
                if (shader == null) continue;

                var errors = ShaderUtil.GetShaderMessages(shader)
                    .Where(m => m.severity == UnityEditor.Rendering.ShaderCompilerMessageSeverity.Error)
                    .ToArray();

                if (errors.Length == 0)
                {
                    Say(Loc.T("verify.shader.compiled", "shader", shader.name));
                    continue;
                }

                withErrors++;
                Say(Loc.T("verify.shader.errors_in", "shader", shader.name));
                foreach (var message in errors.Take(6))
                    Say(Loc.T("verify.shader.error_line", "line", message.line,
                        "message", $"{message.message} {message.messageDetails}"));
            }

            return withErrors;
        }

        /// <summary>
        /// Ставит в кадр импортированные меши как есть — без ручной подмены
        /// материалов. Если материалы не привязались, объекты будут серыми,
        /// и это видно сразу.
        /// </summary>
        static int BuildModelScene(out Camera camera)
        {
            foreach (var existing in UnityEngine.Object.FindObjectsByType<MeshRenderer>(
                         FindObjectsSortMode.None))
                UnityEngine.Object.DestroyImmediate(existing.gameObject);

            var models = AssetDatabase.FindAssets("t:Model", new[] { $"{TargetRoot}/Meshes" })
                .Select(AssetDatabase.GUIDToAssetPath)
                .Select(AssetDatabase.LoadAssetAtPath<GameObject>)
                .Where(m => m != null)
                .OrderBy(m => m.name)
                .Take(8)
                .ToList();

            camera = UnityEngine.Object.FindAnyObjectByType<Camera>();
            if (models.Count == 0 || camera == null)
                return 0;

            var bounds = new Bounds(Vector3.zero, Vector3.zero);
            var started = false;
            var offset = 0f;

            foreach (var model in models)
            {
                var instance = (GameObject)PrefabUtility.InstantiatePrefab(model);
                var renderers = instance.GetComponentsInChildren<Renderer>();
                if (renderers.Length == 0)
                {
                    UnityEngine.Object.DestroyImmediate(instance);
                    continue;
                }

                var local = renderers[0].bounds;
                foreach (var renderer in renderers) local.Encapsulate(renderer.bounds);

                instance.transform.position = new Vector3(offset - local.center.x, -local.min.y, 0f);
                offset += local.size.x * 1.25f + 0.4f;

                foreach (var renderer in instance.GetComponentsInChildren<Renderer>())
                {
                    if (!started) { bounds = renderer.bounds; started = true; }
                    else bounds.Encapsulate(renderer.bounds);
                }
            }

            if (!started) return 0;

            // Кадрируем по описанной сфере и углу обзора, иначе на объектах
            // разного размера камера уезжает в никуда.
            var radius = bounds.extents.magnitude;
            var distance = radius / Mathf.Tan(camera.fieldOfView * 0.5f * Mathf.Deg2Rad) * 1.25f;

            // Смотрим сверху-сбоку: в лоб плоские детали интерьера читаются плохо.
            var direction = Quaternion.Euler(22f, -28f, 0f) * Vector3.forward;
            camera.transform.position = bounds.center - direction * distance;
            camera.transform.LookAt(bounds.center);

            // Свет ставился под сетку сфер — для моделей направляем его от камеры.
            var sun = UnityEngine.Object.FindAnyObjectByType<Light>();
            if (sun != null)
            {
                sun.transform.rotation = Quaternion.LookRotation(
                    Quaternion.Euler(35f, 25f, 0f) * camera.transform.forward);
                sun.intensity = 2.2f;
            }

            return models.Count;
        }

        static Texture2D Render(Camera camera, int width, int height)
        {
            var target = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32)
            {
                antiAliasing = 4
            };
            camera.targetTexture = target;
            camera.Render();

            var previous = RenderTexture.active;
            RenderTexture.active = target;
            var image = new Texture2D(width, height, TextureFormat.RGB24, false);
            image.ReadPixels(new Rect(0, 0, width, height), 0, 0);
            image.Apply();
            RenderTexture.active = previous;

            camera.targetTexture = null;
            return image;
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
    }
}
