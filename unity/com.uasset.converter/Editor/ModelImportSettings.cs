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
                ImportWithRemap(importer, PathMap.AssetPathFor(targetRoot, mesh.file),
                                mesh.materialSlots, materials, log);
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

                ImportWithRemap(importer, PathMap.AssetPathFor(targetRoot, mesh.file),
                                mesh.materialSlots, materials, log);

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
            // Модели в Unreal НЕ повёрнуты — разворот на ±90° вносит сам импортёр
            // FBX (оси Z-up -> Y-up). Запекаем эту конвертацию в вершины: меш
            // приезжает верно ориентированным при identity, и размещение уровня
            // не требует доводки поворотом (та ломала пивоты и давала смещения).
            importer.bakeAxisConversion = true;
            importer.importBlendShapes = animated;
            importer.importAnimation = animated;
            importer.weldVertices = true;
            importer.indexFormat = ModelImporterIndexFormat.Auto;
        }

        /// <summary>
        /// Импортирует модель в два прохода и привязывает материалы по НАСТОЯЩИМ
        /// именам секций FBX.
        ///
        /// Unity ищет ремап по имени материала, записанному внутри FBX. Экспортёр
        /// UE называет секцию по имени назначенного материала-ассета, а дубликат
        /// того же материала на другом слоте получает числовой суффикс по индексу
        /// (MI_Trim_A_Black -> MI_Trim_A_Black_4). Секции без материала сохраняют
        /// имя из исходного DCC (lambert1, phong1, pasted__...). Угадать эти имена
        /// заранее нельзя — поэтому читаем их из уже импортированной модели.
        /// </summary>
        static void ImportWithRemap(ModelImporter importer, string assetPath,
                                    MaterialSlot[] slots,
                                    Dictionary<string, Material> materials,
                                    System.Action<string> log)
        {
            // Снимаем прошлые материал-ремапы: иначе после предыдущих прогонов
            // sharedMaterials вернёт имена уже привязанных внешних .mat, а нам
            // нужны сырые имена секций из FBX.
            foreach (var pair in importer.GetExternalObjectMap())
                if (pair.Key.type == typeof(Material))
                    importer.RemoveRemap(pair.Key);

            // Проход 1: секции получают встроенные материалы с сырыми именами FBX
            // (Configure уже выставил ImportViaMaterialDescription + InPrefab).
            importer.SaveAndReimport();

            var realNames = RealNamesByIndex(assetPath, slots?.Length ?? 0, log);
            RemapMaterials(importer, slots, materials, realNames);

            // Проход 2: применяем ремапы.
            importer.SaveAndReimport();
        }

        /// <summary>
        /// Настоящие имена материалов секций импортированной модели, по индексу
        /// сабмеша (== индекс слота манифеста). null, если рендерер не найден или
        /// число материалов не совпало со слотами — тогда привязка идёт только по
        /// именам-догадкам.
        /// </summary>
        static string[] RealNamesByIndex(string assetPath, int expected,
                                         System.Action<string> log)
        {
            var go = AssetDatabase.LoadMainAssetAtPath(assetPath) as GameObject;
            if (go == null) return null;

            Material[] shared = null;
            var mr = go.GetComponentInChildren<MeshRenderer>(true);
            if (mr != null)
                shared = mr.sharedMaterials;
            else
            {
                var smr = go.GetComponentInChildren<SkinnedMeshRenderer>(true);
                if (smr != null) shared = smr.sharedMaterials;
            }
            if (shared == null) return null;

            if (expected > 0 && shared.Length != expected)
            {
                log(Loc.T("unity.model.slot_mismatch", "name", go.name,
                          "found", shared.Length, "expected", expected));
                return null;
            }

            var names = new string[shared.Length];
            for (var i = 0; i < shared.Length; i++)
                names[i] = shared[i] != null ? shared[i].name : null;
            return names;
        }

        /// <summary>
        /// Регистрирует ремапы материалов. Ключ, по которому Unity реально ищет —
        /// это настоящее имя секции FBX (realNames по индексу слота). Дополнительно
        /// регистрируем имя ассета и имя слота: лишний remap безвреден, а для
        /// краевых случаев (не удалось прочитать имена) он остаётся страховкой.
        /// </summary>
        static void RemapMaterials(ModelImporter importer, MaterialSlot[] slots,
                                   Dictionary<string, Material> materials,
                                   string[] realNames = null)
        {
            if (slots == null) return;

            for (var i = 0; i < slots.Length; i++)
            {
                var slot = slots[i];
                if (string.IsNullOrEmpty(slot.material)) continue;
                if (!materials.TryGetValue(slot.material, out var material)) continue;

                var names = new HashSet<string> { MaterialBuilder.ShortName(slot.material) };
                if (!string.IsNullOrEmpty(slot.slot))
                    names.Add(slot.slot);
                if (realNames != null && i < realNames.Length &&
                    !string.IsNullOrEmpty(realNames[i]))
                    names.Add(realNames[i]);

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
