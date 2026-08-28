using System;
using System.Collections.Generic;

namespace UassetImporter
{
    // Модель манифеста, который пишет postprocess.py.
    //
    // Формат специально плоский — только массивы и примитивы, без словарей:
    // JsonUtility в Unity словари не умеет, а тащить в проект сторонний
    // JSON-парсер ради одного файла не хочется.

    [Serializable]
    public class NamedTexture
    {
        public string name;     // имя свойства шейдера, например _BaseMap
        public string uePath;   // путь ассета в Unreal, если текстура пришла оттуда
        public string file;     // путь относительно output, если карта сгенерирована
    }

    [Serializable]
    public class NamedFloat
    {
        public string name;
        public float value;
    }

    [Serializable]
    public class NamedColor
    {
        public string name;
        public float r, g, b, a;
    }

    [Serializable]
    public class NamedKeyword
    {
        public string name;
        public bool enabled;
    }

    [Serializable]
    public class MaterialSlot
    {
        public string slot;
        public string material;
    }

    [Serializable]
    public class TextureEntry
    {
        public string uePath;
        public string file;
        public bool srgb;
        public bool isNormal;
        public string wrapU;
        public string wrapV;
        public bool generated;
    }

    [Serializable]
    public class MeshEntry
    {
        public string uePath;
        public string file;
        public MaterialSlot[] materialSlots;
    }

    [Serializable]
    public class SkeletalMeshEntry
    {
        public string uePath;
        public string file;
        public string skeleton;
        public MaterialSlot[] materialSlots;
    }

    [Serializable]
    public class AnimationEntry
    {
        public string uePath;
        public string file;
        public string clipName;
        public string skeleton;
        public bool loopTime;
        public bool rootMotion;
    }

    [Serializable]
    public class MaterialEntry
    {
        public string uePath;
        public string mode;          // "shader" — свой транспилированный, "fallback" — URP/Lit
        public string shader;
        public bool twoSided;
        public string blendMode;     // BLEND_OPAQUE / BLEND_MASKED / BLEND_TRANSLUCENT / ...
        public float alphaCutoff;
        public bool emission;
        public NamedTexture[] textures;
        public NamedFloat[] floats;
        public NamedColor[] colors;
        public NamedKeyword[] keywords;
    }

    [Serializable]
    public class ShaderEntry
    {
        public string uePath;
        public string shader;
        public string file;
    }

    [Serializable]
    public class UassetManifest
    {
        public string pipeline;
        public TextureEntry[] textures;
        public MeshEntry[] meshes;
        public SkeletalMeshEntry[] skeletalMeshes;
        public AnimationEntry[] animations;
        public MaterialEntry[] materials;
        public ShaderEntry[] shaders;

        public static UassetManifest Load(string json)
        {
            var manifest = UnityEngine.JsonUtility.FromJson<UassetManifest>(json);
            manifest.textures ??= Array.Empty<TextureEntry>();
            manifest.meshes ??= Array.Empty<MeshEntry>();
            manifest.skeletalMeshes ??= Array.Empty<SkeletalMeshEntry>();
            manifest.animations ??= Array.Empty<AnimationEntry>();
            manifest.materials ??= Array.Empty<MaterialEntry>();
            manifest.shaders ??= Array.Empty<ShaderEntry>();
            return manifest;
        }
    }

    /// <summary>
    /// Переводит пути ассетов Unreal (/Game/Foo/Bar) в пути внутри Assets и обратно.
    /// Держим в одном месте, чтобы правила раскладки не разъехались между шагами.
    /// </summary>
    public static class PathMap
    {
        public static string StripGame(string uePath)
        {
            if (string.IsNullOrEmpty(uePath)) return string.Empty;
            const string prefix = "/Game/";
            return uePath.StartsWith(prefix) ? uePath.Substring(prefix.Length) : uePath.TrimStart('/');
        }

        /// <summary>Путь в Assets для файла, лежащего в output по относительному пути.</summary>
        public static string AssetPathFor(string targetRoot, string relativeFile)
        {
            return $"{targetRoot}/{relativeFile.Replace('\\', '/')}";
        }
    }
}
