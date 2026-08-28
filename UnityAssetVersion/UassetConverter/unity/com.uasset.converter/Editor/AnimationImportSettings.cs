using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace UassetImporter
{
    /// <summary>
    /// Настройка импорта анимационных клипов.
    ///
    /// Порядок здесь принципиален: клип должен ссылаться на аватар скелетного
    /// меша (sourceAvatar), иначе Unity построит для каждого FBX свой аватар,
    /// и клипы не сядут на риг персонажа. Поэтому скелетные меши обязаны быть
    /// импортированы раньше.
    /// </summary>
    public static class AnimationImportSettings
    {
        public static int Apply(UassetManifest manifest, string targetRoot,
                                Dictionary<string, string> avatarBySkeleton,
                                System.Action<string> log)
        {
            var count = 0;

            foreach (var animation in manifest.animations)
            {
                var assetPath = PathMap.AssetPathFor(targetRoot, animation.file);
                var importer = AssetImporter.GetAtPath(assetPath) as ModelImporter;
                if (importer == null)
                {
                    log(Loc.T("unity.clip.no_importer", "path", assetPath));
                    continue;
                }

                importer.materialImportMode = ModelImporterMaterialImportMode.None;
                importer.importCameras = false;
                importer.importLights = false;
                importer.importAnimation = true;
                importer.animationType = ModelImporterAnimationType.Generic;

                var avatar = FindAvatar(animation.skeleton, avatarBySkeleton, log, animation.clipName);
                if (avatar != null)
                {
                    importer.animationType = avatar.isHuman
                        ? ModelImporterAnimationType.Human
                        : ModelImporterAnimationType.Generic;
                    importer.avatarSetup = ModelImporterAvatarSetup.CopyFromOther;
                    importer.sourceAvatar = avatar;
                }

                ApplyClipSettings(importer, animation);
                importer.SaveAndReimport();
                count++;
            }

            return count;
        }

        static Avatar FindAvatar(string skeleton, Dictionary<string, string> avatarBySkeleton,
                                 System.Action<string> log, string clipName)
        {
            if (string.IsNullOrEmpty(skeleton) || !avatarBySkeleton.TryGetValue(skeleton, out var modelPath))
            {
                log(Loc.T("unity.clip.no_matching_skeleton", "clip", clipName));
                return null;
            }

            foreach (var asset in AssetDatabase.LoadAllAssetsAtPath(modelPath))
                if (asset is Avatar found)
                    return found;

            log(Loc.T("unity.clip.no_avatar", "model", modelPath, "clip", clipName));
            return null;
        }

        /// <summary>
        /// Unreal не хранит признак зацикливания, поэтому значение приходит из
        /// эвристики по имени, посчитанной в postprocess.py. Всё, что здесь
        /// можно сделать честно, — аккуратно его применить.
        /// </summary>
        static void ApplyClipSettings(ModelImporter importer, AnimationEntry animation)
        {
            var existing = importer.defaultClipAnimations;
            if (existing == null || existing.Length == 0)
                existing = importer.clipAnimations;
            if (existing == null || existing.Length == 0)
                return;

            var clips = new List<ModelImporterClipAnimation>();
            for (var i = 0; i < existing.Length; i++)
            {
                var clip = existing[i];
                // Имя ассета Unreal информативнее, чем "Take 001" из FBX.
                if (existing.Length == 1)
                    clip.name = animation.clipName;

                clip.loopTime = animation.loopTime;
                clip.loopPose = animation.loopTime;
                clips.Add(clip);
            }

            importer.clipAnimations = clips.ToArray();
        }
    }
}
