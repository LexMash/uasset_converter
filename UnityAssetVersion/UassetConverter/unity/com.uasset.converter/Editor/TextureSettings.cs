using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace UassetImporter
{
    /// <summary>
    /// Настройка импортёра текстур по данным из манифеста.
    ///
    /// Это не косметика: если карта нормалей приедет как обычная текстура, а
    /// маска — как sRGB, картинка будет неправильной, и понять почему по одному
    /// виду материала практически невозможно.
    /// </summary>
    public static class TextureSettings
    {
        public static int Apply(UassetManifest manifest, string targetRoot, System.Action<string> log)
        {
            var changed = 0;

            foreach (var entry in manifest.textures)
            {
                var assetPath = PathMap.AssetPathFor(targetRoot, entry.file);
                var importer = AssetImporter.GetAtPath(assetPath) as TextureImporter;
                if (importer == null)
                {
                    log(Loc.T("unity.texture.no_importer", "path", assetPath));
                    continue;
                }

                var wanted = entry.isNormal ? TextureImporterType.NormalMap : TextureImporterType.Default;
                var wrapU = ToWrapMode(entry.wrapU);
                var wrapV = ToWrapMode(entry.wrapV);
                // Для карты нормалей флаг sRGB Unity игнорирует, но выставим
                // корректное значение, чтобы настройки не выглядели противоречиво.
                var wantedSrgb = !entry.isNormal && entry.srgb;

                var needsUpdate =
                    importer.textureType != wanted ||
                    importer.sRGBTexture != wantedSrgb ||
                    importer.wrapModeU != wrapU ||
                    importer.wrapModeV != wrapV;

                if (!needsUpdate)
                    continue;

                importer.textureType = wanted;
                importer.sRGBTexture = wantedSrgb;
                importer.wrapMode = wrapU;
                importer.wrapModeU = wrapU;
                importer.wrapModeV = wrapV;
                if (entry.isNormal)
                    importer.textureCompression = TextureImporterCompression.CompressedHQ;

                importer.SaveAndReimport();
                changed++;
            }

            return changed;
        }

        static TextureWrapMode ToWrapMode(string address)
        {
            if (string.IsNullOrEmpty(address)) return TextureWrapMode.Repeat;
            if (address.Contains("Clamp")) return TextureWrapMode.Clamp;
            if (address.Contains("Mirror")) return TextureWrapMode.Mirror;
            return TextureWrapMode.Repeat;
        }
    }
}
