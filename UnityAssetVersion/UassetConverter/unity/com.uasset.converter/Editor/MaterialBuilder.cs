using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace UassetImporter
{
    /// <summary>
    /// Создаёт материалы Unity по манифесту.
    ///
    /// Два пути. Если мастер-материал Unreal удалось транспилировать, материал
    /// садится на свой шейдер и параметры проставляются по именам один в один.
    /// Иначе — дефолтный Lit пайплайна, куда значения раскладываются по ролям,
    /// вычисленным ещё в postprocess.py.
    /// </summary>
    public static class MaterialBuilder
    {
        public static Dictionary<string, Material> Build(
            UassetManifest manifest, string targetRoot, System.Action<string> log,
            out int shaderMode, out int fallbackMode, out int failed)
        {
            var result = new Dictionary<string, Material>();
            shaderMode = fallbackMode = failed = 0;

            var materialsRoot = $"{targetRoot}/Materials";
            EnsureFolder(materialsRoot);

            foreach (var entry in manifest.materials)
            {
                var shader = Shader.Find(entry.shader);
                if (shader == null)
                {
                    // Свой шейдер мог не скомпилироваться — тогда честнее взять
                    // Lit, чем оставить розовый материал без объяснений.
                    var fallbackName = FallbackShaderName();
                    shader = Shader.Find(fallbackName);
                    if (shader == null)
                    {
                        log(Loc.T("unity.material.no_shader_at_all", "shader", entry.shader,
                              "fallback", fallbackName, "path", entry.uePath));
                        failed++;
                        continue;
                    }
                    log(Loc.T("unity.material.shader_missing", "shader", entry.shader,
                        "name", ShortName(entry.uePath), "fallback", fallbackName));
                }

                var assetPath = $"{materialsRoot}/{ShortName(entry.uePath)}.mat";
                var material = AssetDatabase.LoadAssetAtPath<Material>(assetPath);
                if (material == null)
                {
                    material = new Material(shader);
                    AssetDatabase.CreateAsset(material, assetPath);
                }
                else if (material.shader != shader)
                {
                    material.shader = shader;
                }

                ApplyValues(material, entry, manifest, targetRoot, log);
                ApplyRenderState(material, entry);

                EditorUtility.SetDirty(material);
                result[entry.uePath] = material;

                if (entry.mode == "shader") shaderMode++;
                else fallbackMode++;
            }

            return result;
        }

        /// <summary>
        /// Загружает уже созданные материалы по манифесту, ничего не пересоздавая.
        /// Нужно шагам, которым материалы требуются готовыми (сборка сцены), но
        /// которые не должны запускать полный Build с его побочными эффектами.
        /// Отсутствующий на диске материал просто не попадает в словарь.
        /// </summary>
        public static Dictionary<string, Material> LoadExisting(
            UassetManifest manifest, string targetRoot)
        {
            var result = new Dictionary<string, Material>();
            var materialsRoot = $"{targetRoot}/Materials";
            foreach (var entry in manifest.materials)
            {
                var assetPath = $"{materialsRoot}/{ShortName(entry.uePath)}.mat";
                var material = AssetDatabase.LoadAssetAtPath<Material>(assetPath);
                if (material != null)
                    result[entry.uePath] = material;
            }
            return result;
        }

        static void ApplyValues(Material material, MaterialEntry entry,
                                UassetManifest manifest, string targetRoot,
                                System.Action<string> log)
        {
            foreach (var texture in entry.textures ?? new NamedTexture[0])
            {
                if (!material.HasProperty(texture.name))
                    continue;

                var path = ResolveTexturePath(texture, manifest, targetRoot);
                if (string.IsNullOrEmpty(path))
                    continue;

                var asset = AssetDatabase.LoadAssetAtPath<Texture>(path);
                if (asset == null)
                {
                    log(Loc.T("unity.material.texture_missing", "path", path));
                    continue;
                }
                material.SetTexture(texture.name, asset);
            }

            foreach (var number in entry.floats ?? new NamedFloat[0])
                if (material.HasProperty(number.name))
                    material.SetFloat(number.name, number.value);

            foreach (var color in entry.colors ?? new NamedColor[0])
                if (material.HasProperty(color.name))
                    material.SetColor(color.name, new Color(color.r, color.g, color.b, color.a));

            foreach (var keyword in entry.keywords ?? new NamedKeyword[0])
            {
                if (keyword.enabled) material.EnableKeyword(keyword.name);
                else material.DisableKeyword(keyword.name);
            }

            if (entry.emission)
            {
                material.EnableKeyword("_EMISSION");
                material.globalIlluminationFlags = MaterialGlobalIlluminationFlags.RealtimeEmissive;
            }
        }

        /// <summary>
        /// Текстура приходит либо как ассет Unreal, либо как сгенерированная
        /// постобработкой карта (упакованные маски, перевёрнутые нормали).
        /// </summary>
        static string ResolveTexturePath(NamedTexture texture, UassetManifest manifest, string targetRoot)
        {
            if (!string.IsNullOrEmpty(texture.file))
                return PathMap.AssetPathFor(targetRoot, texture.file);

            if (string.IsNullOrEmpty(texture.uePath))
                return null;

            foreach (var candidate in manifest.textures)
                if (candidate.uePath == texture.uePath)
                    return PathMap.AssetPathFor(targetRoot, candidate.file);

            return null;
        }

        static void ApplyRenderState(Material material, MaterialEntry entry)
        {
            if (material.HasProperty("_Cull"))
                material.SetFloat("_Cull", entry.twoSided ? (float)UnityEngine.Rendering.CullMode.Off : (float)UnityEngine.Rendering.CullMode.Back);
            material.doubleSidedGI = entry.twoSided;

            if (material.HasProperty("_Cutoff"))
                material.SetFloat("_Cutoff", entry.alphaCutoff);

            // Свой шейдер уже несёт нужные Blend/ZWrite прямо в ShaderLab —
            // трогать его настройки поверх не нужно и вредно.
            if (entry.mode == "shader")
                return;

            var alphaClip = entry.blendMode == "BLEND_MASKED";
            var transparent = entry.blendMode == "BLEND_TRANSLUCENT" ||
                              entry.blendMode == "BLEND_ADDITIVE" ||
                              entry.blendMode == "BLEND_MODULATE";

            if (alphaClip)
                SetupUrpSurface(material, opaque: true, alphaClip: true, queue: 2450);
            else if (transparent)
                SetupUrpSurface(material, opaque: false, alphaClip: false, queue: 3000);
            else
                SetupUrpSurface(material, opaque: true, alphaClip: false, queue: 2000);
        }

        /// <summary>
        /// Переключение Surface Type у URP/Lit — это не одно свойство, а связка
        /// из нескольких флагов, ключевого слова и режима блендинга. Инспектор
        /// делает это за кадром, из кода приходится руками.
        /// </summary>
        static void SetupUrpSurface(Material material, bool opaque, bool alphaClip, int queue)
        {
            if (material.HasProperty("_Surface"))
                material.SetFloat("_Surface", opaque ? 0f : 1f);
            if (material.HasProperty("_AlphaClip"))
                material.SetFloat("_AlphaClip", alphaClip ? 1f : 0f);

            if (alphaClip) material.EnableKeyword("_ALPHATEST_ON");
            else material.DisableKeyword("_ALPHATEST_ON");

            if (opaque)
            {
                material.SetOverrideTag("RenderType", alphaClip ? "TransparentCutout" : "Opaque");
                material.DisableKeyword("_SURFACE_TYPE_TRANSPARENT");
                material.DisableKeyword("_ALPHAPREMULTIPLY_ON");
                if (material.HasProperty("_SrcBlend")) material.SetFloat("_SrcBlend", (float)UnityEngine.Rendering.BlendMode.One);
                if (material.HasProperty("_DstBlend")) material.SetFloat("_DstBlend", (float)UnityEngine.Rendering.BlendMode.Zero);
                if (material.HasProperty("_ZWrite")) material.SetFloat("_ZWrite", 1f);
            }
            else
            {
                material.SetOverrideTag("RenderType", "Transparent");
                material.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
                if (material.HasProperty("_SrcBlend")) material.SetFloat("_SrcBlend", (float)UnityEngine.Rendering.BlendMode.SrcAlpha);
                if (material.HasProperty("_DstBlend")) material.SetFloat("_DstBlend", (float)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
                if (material.HasProperty("_ZWrite")) material.SetFloat("_ZWrite", 0f);
            }

            material.renderQueue = queue;
        }

        public static string FallbackShaderName()
        {
            return "Universal Render Pipeline/Lit";
        }

        public static string ShortName(string uePath)
        {
            if (string.IsNullOrEmpty(uePath)) return "Material";
            var index = uePath.LastIndexOf('/');
            return index >= 0 ? uePath.Substring(index + 1) : uePath;
        }

        public static void EnsureFolder(string assetFolder)
        {
            if (AssetDatabase.IsValidFolder(assetFolder)) return;

            var parts = assetFolder.Split('/');
            var current = parts[0];
            for (var i = 1; i < parts.Length; i++)
            {
                var next = $"{current}/{parts[i]}";
                if (!AssetDatabase.IsValidFolder(next))
                    AssetDatabase.CreateFolder(current, parts[i]);
                current = next;
            }
        }
    }
}
