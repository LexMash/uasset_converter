using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace UassetImporter
{
    /// <summary>
    /// Настройка импортёра FBX и привязка созданных материалов к слотам модели.
    ///
    /// Материалы из FBX не импортируем: они уже собраны шагом MaterialBuilder
    /// по данным Unreal и знают куда больше, чем то, что доживает до FBX.
    /// </summary>
    public static class ModelImportSettings
    {
        public static int ApplyStatic(UassetManifest manifest, string targetRoot,
                                      Dictionary<string, Material> materials,
                                      System.Action<string> log)
        {
            var count = 0;
            foreach (var mesh in manifest.meshes)
            {
                var importer = Get(targetRoot, mesh.file, log);
                if (importer == null) continue;

                Configure(importer, animated: false);
                RemapMaterials(importer, mesh.materialSlots, materials);
                importer.SaveAndReimport();
                count++;
            }
            return count;
        }

        public static int ApplySkeletal(UassetManifest manifest, string targetRoot,
                                        Dictionary<string, Material> materials,
                                        string avatarType,
                                        Dictionary<string, string> avatarBySkeleton,
                                        System.Action<string> log)
        {
            var count = 0;
            foreach (var mesh in manifest.skeletalMeshes)
            {
                var importer = Get(targetRoot, mesh.file, log);
                if (importer == null) continue;

                Configure(importer, animated: true);
                importer.animationType = avatarType == "Humanoid"
                    ? ModelImporterAnimationType.Human
                    : ModelImporterAnimationType.Generic;
                importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;

                RemapMaterials(importer, mesh.materialSlots, materials);
                importer.SaveAndReimport();

                // Клипы будут искать аватар именно по скелету Unreal — запоминаем,
                // какая модель его определяет.
                if (!string.IsNullOrEmpty(mesh.skeleton) && !avatarBySkeleton.ContainsKey(mesh.skeleton))
                    avatarBySkeleton[mesh.skeleton] = PathMap.AssetPathFor(targetRoot, mesh.file);

                count++;
            }
            return count;
        }

        static ModelImporter Get(string targetRoot, string file, System.Action<string> log)
        {
            var assetPath = PathMap.AssetPathFor(targetRoot, file);
            var importer = AssetImporter.GetAtPath(assetPath) as ModelImporter;
            if (importer == null)
                log(Loc.T("unity.model.no_importer", "path", assetPath));
            return importer;
        }

        static void Configure(ModelImporter importer, bool animated)
        {
            // Материалы из FBX как ассеты нам не нужны — свои уже собраны из
            // данных Unreal. Но полностью выключать импорт материалов нельзя:
            // при materialImportMode = None Unity вообще не смотрит на имена
            // материалов в файле, и таблица ремапов становится мёртвым грузом —
            // меш приезжает с дефолтным серым материалом.
            // ImportViaMaterialDescription + InPrefab — это «Import via
            // MaterialDescription» и «Use Embedded Materials» в инспекторе,
            // при которых раздел Remapped Materials работает.
            importer.materialImportMode = ModelImporterMaterialImportMode.ImportViaMaterialDescription;
            importer.materialLocation = ModelImporterMaterialLocation.InPrefab;
            importer.importCameras = false;
            importer.importLights = false;
            importer.importVisibility = false;
            // Unreal мерит в сантиметрах, Unity в метрах. Конвертацию делает сам
            // импортёр по единицам, записанным в FBX — своим множителем сверху
            // это только сломать.
            importer.useFileScale = true;
            importer.importBlendShapes = animated;
            importer.importAnimation = animated;
            importer.weldVertices = true;
            importer.indexFormat = ModelImporterIndexFormat.Auto;
        }

        /// <summary>
        /// Экспортёр FBX называет материалы по имени ассета Unreal, а не по имени
        /// слота, поэтому регистрируем оба варианта: лишний remap безвреден,
        /// а недостающий оставил бы модель с розовым материалом.
        /// </summary>
        static void RemapMaterials(ModelImporter importer, MaterialSlot[] slots,
                                   Dictionary<string, Material> materials)
        {
            if (slots == null) return;

            foreach (var slot in slots)
            {
                if (string.IsNullOrEmpty(slot.material)) continue;
                if (!materials.TryGetValue(slot.material, out var material)) continue;

                var names = new HashSet<string> { MaterialBuilder.ShortName(slot.material) };
                if (!string.IsNullOrEmpty(slot.slot))
                    names.Add(slot.slot);

                foreach (var name in names)
                {
                    importer.AddRemap(
                        new AssetImporter.SourceAssetIdentifier(typeof(Material), name),
                        material);
                }
            }
        }
    }
}
