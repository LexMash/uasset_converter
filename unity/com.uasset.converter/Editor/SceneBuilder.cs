using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace UassetImporter
{
    /// <summary>
    /// Собирает Unity-сцены из уровней Unreal. Одна сцена на уровень.
    ///
    /// level_export.py осознанно НЕ конвертирует координаты: level-JSON хранит
    /// сырые трансформы UE (сантиметры, кватернион в осях движка), а перевод в оси
    /// и метры Unity живёт здесь единственной формулой — так его нельзя
    /// рассинхронизировать между Python и C#. См. UeToUnity* ниже.
    ///
    /// Меши ставятся как prefab-инстансы уже импортированных моделей (связь с
    /// ассетом сохраняется), поверх накладываются материалы-переопределения.
    /// Предполагается, что обычный Import уже создал меши и материалы; отсутствие
    /// нужного ассета — строка лога, а не падение.
    /// </summary>
    public static class SceneBuilder
    {
        public static int Build(UassetManifest manifest, string outputDir, string targetRoot,
                                Dictionary<string, Material> materials,
                                System.Action<string> log)
        {
            // UE-путь меша -> относительный файл импортированной модели.
            var meshFiles = new Dictionary<string, string>();
            foreach (var mesh in manifest.meshes)
                if (!string.IsNullOrEmpty(mesh.uePath))
                    meshFiles[mesh.uePath] = mesh.file;

            var built = 0;
            foreach (var entry in manifest.levels)
            {
                if (string.IsNullOrEmpty(entry.file))
                {
                    log(Loc.T("unity.level.file_missing", "path", entry.name ?? ""));
                    continue;
                }
                var path = Path.Combine(outputDir, entry.file.Replace('/', Path.DirectorySeparatorChar));
                if (!File.Exists(path))
                {
                    log(Loc.T("unity.level.file_missing", "path", path));
                    continue;
                }

                LevelFile level;
                try
                {
                    level = LevelFile.Load(File.ReadAllText(path));
                }
                catch (System.Exception error)
                {
                    log(Loc.T("unity.level.unreadable", "name", entry.name, "error", error.Message));
                    continue;
                }

                if (level.schemaVersion != LevelFile.SupportedSchemaVersion)
                {
                    log(Loc.T("unity.level.schema_mismatch", "name", entry.name,
                              "got", level.schemaVersion, "want", LevelFile.SupportedSchemaVersion));
                    continue;
                }

                if (BuildOne(entry, level, targetRoot, meshFiles, materials, log))
                    built++;
            }

            if (built == 0)
                log(Loc.T("unity.level.none"));
            return built;
        }

        static bool BuildOne(LevelEntry entry, LevelFile level, string targetRoot,
                             Dictionary<string, string> meshFiles,
                             Dictionary<string, Material> materials,
                             System.Action<string> log)
        {
            var scenePath = ScenePathFor(targetRoot, entry);
            // Сцена уже сгенерирована ранее — спрашиваем, прежде чем перезаписать.
            if (File.Exists(scenePath) &&
                !EditorUtility.DisplayDialog(
                    Loc.T("unity.level.exists_title"),
                    Loc.T("unity.level.exists_body", "path", scenePath),
                    Loc.T("unity.level.overwrite"), Loc.T("unity.level.skip")))
            {
                log(Loc.T("unity.level.skipped", "name", entry.name));
                return false;
            }

            // Пустая additive-сцена: рабочую сцену пользователя не трогаем — объекты
            // строим в активной сцене, корни переносим в новую, её и сохраняем.
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            var created = new List<GameObject>();

            // Группируем компоненты по актёру и считаем, кто является родителем в
            // иерархии — по этим признакам решаем, схлопывать ли актёра в сам меш.
            var meshesByActor = new Dictionary<string, List<LevelObject>>();
            foreach (var obj in level.objects)
            {
                var key = obj.parentId ?? "";
                if (!meshesByActor.TryGetValue(key, out var list))
                    meshesByActor[key] = list = new List<LevelObject>();
                list.Add(obj);
            }
            var lightsByActor = new Dictionary<string, List<LevelLight>>();
            foreach (var lightData in level.lights)
            {
                if (!lightData.visible) continue;
                var key = lightData.parentId ?? "";
                if (!lightsByActor.TryGetValue(key, out var list))
                    lightsByActor[key] = list = new List<LevelLight>();
                list.Add(lightData);
            }
            // id актёра → его метка (имя как в аутлайнере UE) — для именования ламп.
            var actorNameById = new Dictionary<string, string>();
            foreach (var actor in level.actors)
                if (!string.IsNullOrEmpty(actor.id))
                    actorNameById[actor.id] = actor.name;
            var isParent = new HashSet<string>();
            foreach (var actor in level.actors)
                if (!string.IsNullOrEmpty(actor.parentId))
                    isParent.Add(actor.parentId);

            var placed = 0;
            var missingMesh = 0;

            // Первый проход: GameObject на актёра. Актёра с ровно одним мешем, без
            // ламп и без детей представляем САМИМ префаб-инстансом (имя = имя актёра,
            // без пустой обёртки). Актёра с одной лампой и без мешей — самим
            // объектом света. Остальные — контейнером; их компоненты добавим ниже.
            var actorGo = new Dictionary<string, GameObject>();
            var handledMesh = new HashSet<LevelObject>();
            var handledLight = new HashSet<LevelLight>();
            var lights = 0;
            foreach (var actor in level.actors)
            {
                if (string.IsNullOrEmpty(actor.id) || actorGo.ContainsKey(actor.id))
                    continue;

                meshesByActor.TryGetValue(actor.id, out var actorMeshes);
                lightsByActor.TryGetValue(actor.id, out var actorLights);
                var actorName = string.IsNullOrEmpty(actor.name) ? "Actor" : actor.name;

                GameObject go = null;
                if (actorMeshes != null && actorMeshes.Count == 1 &&
                    (actorLights == null || actorLights.Count == 0) &&
                    !isParent.Contains(actor.id))
                {
                    var solo = actorMeshes[0];
                    handledMesh.Add(solo);   // в любом случае не переобрабатываем ниже
                    var model = ResolveModel(solo, targetRoot, meshFiles, entry.name, log);
                    if (model != null)
                    {
                        go = (GameObject)PrefabUtility.InstantiatePrefab(model);
                        go.name = actorName;
                        ApplyTransform(go.transform, solo.transform);
                        ApplyOverrides(go, solo.materialOverrides, materials, entry.name, log);
                        MarkStaticIfMesh(go);
                        placed++;
                    }
                    else
                    {
                        missingMesh++;   // модель не найдена — оставим пустой контейнер
                    }
                }

                // Чистый light-актёр: сам объект несёт компонент Light, без пустой
                // обёртки. Имя = метка актёра (как в аутлайнере UE).
                if (go == null && (actorMeshes == null || actorMeshes.Count == 0) &&
                    actorLights != null && actorLights.Count == 1 &&
                    !isParent.Contains(actor.id))
                {
                    var light = actorLights[0];
                    go = new GameObject(actorName);
                    ApplyTransform(go.transform, light.transform);
                    // Unreal-лампы излучают вдоль локального +X, Unity — вдоль +Z:
                    // доворачиваем на +90° вокруг Y (Point изотропен — не трогаем).
                    if (light.unityType != "Point")
                        go.transform.rotation *= Quaternion.Euler(0f, 90f, 0f);
                    ConfigureLight(go.AddComponent<Light>(), light);
                    handledLight.Add(light);
                    lights++;
                }

                if (go == null)
                {
                    go = new GameObject(actorName);
                    ApplyTransform(go.transform, actor.transform);
                }

                go.SetActive(actor.active);
                actorGo[actor.id] = go;
                created.Add(go);
            }

            // Второй проход: связываем иерархию актёров, сохраняя мировые трансформы.
            foreach (var actor in level.actors)
            {
                if (actor.id == null || !actorGo.TryGetValue(actor.id, out var go)) continue;
                if (!string.IsNullOrEmpty(actor.parentId) &&
                    actorGo.TryGetValue(actor.parentId, out var parent))
                    go.transform.SetParent(parent.transform, true);
            }

            // Третий проход: оставшиеся меши (мульти-меш актёры, orphan-ы) — инстансами
            // под своим актёром, имя по ассету меша.
            foreach (var obj in level.objects)
            {
                if (handledMesh.Contains(obj)) continue;
                var model = ResolveModel(obj, targetRoot, meshFiles, entry.name, log);
                if (model == null) { missingMesh++; continue; }

                var instance = (GameObject)PrefabUtility.InstantiatePrefab(model);
                instance.name = MaterialBuilder.ShortName(obj.mesh);
                ApplyTransform(instance.transform, obj.transform);
                ParentTo(instance.transform, obj.parentId, actorGo);
                ApplyOverrides(instance, obj.materialOverrides, materials, entry.name, log);
                MarkStaticIfMesh(instance);
                created.Add(instance);
                placed++;
            }

            // Оставшиеся лампы (мульти-лайт актёры, лампы на mesh-актёрах) ставим
            // дочерним объектом. Схлопнутые в первом проходе — пропускаем.
            foreach (var lightData in level.lights)
            {
                if (!lightData.visible || handledLight.Contains(lightData)) continue;
                var go = new GameObject(LightName(lightData, actorNameById));
                ApplyTransform(go.transform, lightData.transform);
                // Unreal-лампы излучают вдоль локального +X, Unity — вдоль +Z.
                // После смены базиса доворачиваем на +90° вокруг локального Y,
                // чтобы луч совпал. Point изотропен — не трогаем. Знак проверять
                // визуально (RenderCheck).
                if (lightData.unityType != "Point")
                    go.transform.rotation *= Quaternion.Euler(0f, 90f, 0f);
                ParentTo(go.transform, lightData.parentId, actorGo);
                ConfigureLight(go.AddComponent<Light>(), lightData);
                created.Add(go);
                lights++;
            }

            // Корни (то, что осталось без родителя) переносим в нашу сцену; дети
            // едут вместе с ними. Так объекты не оседают в сцене пользователя.
            foreach (var go in created)
                if (go.transform.parent == null)
                    EditorSceneManager.MoveGameObjectToScene(go, scene);

            MaterialBuilder.EnsureFolder(Path.GetDirectoryName(scenePath).Replace('\\', '/'));
            EditorSceneManager.SaveScene(scene, scenePath);
            EditorSceneManager.CloseScene(scene, true);
            log(Loc.T("unity.level.built", "name", entry.name, "objects", placed,
                      "lights", lights, "missing", missingMesh));
            return true;
        }

        // Помечает статикой только неанимированную геометрию: без этого лайтмаппер
        // не считает меши статикой и запекание выходит мусорным. Скелетные меши
        // (есть SkinnedMeshRenderer) статикой помечать нельзя — их пропускаем.
        static void MarkStaticIfMesh(GameObject go)
        {
            if (go.GetComponentInChildren<SkinnedMeshRenderer>() != null)
                return;
            if (go.GetComponentInChildren<MeshRenderer>() == null)
                return;
            // Флаг нужен на объекте с рендерером, а он обычно на дочернем импорта.
            // Скелетных в иерархии нет (выше вышли), поэтому помечаем всё дерево.
            const StaticEditorFlags flags =
                StaticEditorFlags.ContributeGI | StaticEditorFlags.BatchingStatic |
                StaticEditorFlags.OccluderStatic | StaticEditorFlags.OccludeeStatic;
            foreach (var t in go.GetComponentsInChildren<Transform>(true))
                GameObjectUtility.SetStaticEditorFlags(t.gameObject, flags);
        }

        static void ParentTo(Transform child, string parentId,
                             Dictionary<string, GameObject> actorGo)
        {
            if (!string.IsNullOrEmpty(parentId) && actorGo.TryGetValue(parentId, out var parent))
                child.SetParent(parent.transform, true);
        }

        // Импортированная модель для меша объекта, или null (с логом причины).
        static GameObject ResolveModel(LevelObject obj, string targetRoot,
                                       Dictionary<string, string> meshFiles, string levelName,
                                       System.Action<string> log)
        {
            if (string.IsNullOrEmpty(obj.mesh) || !meshFiles.TryGetValue(obj.mesh, out var file))
            {
                log(Loc.T("unity.level.mesh_missing", "mesh", obj.mesh ?? "", "level", levelName));
                return null;
            }
            var assetPath = PathMap.AssetPathFor(targetRoot, file);
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            if (model == null)
                log(Loc.T("unity.level.model_missing", "path", assetPath, "level", levelName));
            return model;
        }

        static void ApplyOverrides(GameObject instance, MaterialOverride[] overrides,
                                   Dictionary<string, Material> materials, string levelName,
                                   System.Action<string> log)
        {
            if (overrides == null || overrides.Length == 0) return;

            var renderer = instance.GetComponentInChildren<Renderer>();
            if (renderer == null) return;

            var shared = renderer.sharedMaterials;
            foreach (var over in overrides)
            {
                if (over == null || string.IsNullOrEmpty(over.material)) continue;
                if (over.index < 0 || over.index >= shared.Length) continue;
                if (!materials.TryGetValue(over.material, out var material))
                {
                    log(Loc.T("unity.level.override_missing", "material", over.material,
                              "level", levelName));
                    continue;
                }
                shared[over.index] = material;
            }
            renderer.sharedMaterials = shared;
        }

        // Имя объекта света: метка актёра-владельца (как в аутлайнере UE). Если
        // актёр не найден — очищенный хвост UE-пути (id разделён '.'/':'/'/'),
        // а не сырой путь целиком.
        static string LightName(LevelLight data, Dictionary<string, string> actorNameById)
        {
            if (!string.IsNullOrEmpty(data.parentId) &&
                actorNameById.TryGetValue(data.parentId, out var label) &&
                !string.IsNullOrEmpty(label))
                return label;

            if (string.IsNullOrEmpty(data.id)) return "Light";
            var tail = data.id.LastIndexOfAny(new[] { '.', ':', '/' });
            return tail >= 0 ? data.id.Substring(tail + 1) : data.id;
        }

        static void ConfigureLight(Light light, LevelLight data)
        {
            switch (data.unityType)
            {
                case "Directional": light.type = LightType.Directional; break;
                case "Spot": light.type = LightType.Spot; break;
                case "Area": light.type = LightType.Rectangle; break;
                default: light.type = LightType.Point; break;
            }

            light.intensity = data.unityIntensity;
            light.color = data.color != null && data.color.Length >= 3
                ? new Color(data.color[0], data.color[1], data.color[2])
                : Color.white;
            light.shadows = data.castShadows ? LightShadows.Soft : LightShadows.None;

            if (light.type == LightType.Point || light.type == LightType.Spot)
                if (data.range > 0f) light.range = data.range;

            if (light.type == LightType.Spot)
            {
                if (data.spotAngle > 0f) light.spotAngle = data.spotAngle;
                if (data.innerSpotAngle > 0f) light.innerSpotAngle = data.innerSpotAngle;
            }

            if (light.type == LightType.Rectangle && data.areaSize != null && data.areaSize.Length >= 2)
                light.areaSize = new Vector2(data.areaSize[0], data.areaSize[1]);

            switch (data.lightmapMode)
            {
                case "Baked": light.lightmapBakeType = LightmapBakeType.Baked; break;
                case "Mixed": light.lightmapBakeType = LightmapBakeType.Mixed; break;
                default: light.lightmapBakeType = LightmapBakeType.Realtime; break;
            }
        }

        // -- Конвертация координат UE -> Unity (единственная точка) -----------
        //
        // Смена базиса координат UE -> Unity знаковой перестановкой BASIS. Оба
        // движка левосторонние, поэтому базис ОБЯЗАН быть пропером (det +1); базис-
        // отражение (det −1) переворачивает всю раскладку — сцена собирается
        // зеркально. Меши приезжают верно ориентированными за счёт
        // bakeAxisConversion на импорте (ModelImportSettings), доводка при
        // размещении не нужна.
        //
        // BASIS — куда идёт каждая ось Unity: три пары (индекс UE-оси, знак),
        // порядок Unity x,y,z. Индексы UE: 0=X, 1=Y, 2=Z. Меняется одной строкой.
        //
        // Пресеты (Unity <- UE), * = пропер (det +1), можно использовать:
        //   (X, Z, -Y) * = {0,+1, 2,+1, 1,-1}   <- текущий (не зеркалит)
        //   (-X, Z, Y) * = {0,-1, 2,+1, 1,+1}
        //   (Y, Z, X)  * = {1,+1, 2,+1, 0,+1}   (семантический fwd->fwd)
        //   (-Y, Z, -X)* = {1,-1, 2,+1, 0,-1}
        //   ОТРАЖЕНИЯ (зеркалят, НЕ использовать): (X,Z,Y), (-X,Z,-Y), (Y,Z,-X), (-Y,Z,X)
        static readonly int[] BASIS = { 0, +1,  2, +1,  1, -1 };  // (X, Z, -Y) — пропер (det+1), не зеркалит

        // Определитель знаковой перестановки: чётность перестановки осей * произведение знаков.
        // Нужен для поворота: при отражении (det -1) вектор кватерниона меняет знак,
        // иначе объекты «смотрят наоборот».
        static int BasisDet()
        {
            int c0 = BASIS[0], c1 = BASIS[2], c2 = BASIS[4];
            int signs = BASIS[1] * BASIS[3] * BASIS[5];
            int parity = ((c1 - c0) * (c2 - c0) * (c2 - c1)) > 0 ? 1 : -1;
            return signs * parity;
        }

        static void ApplyTransform(Transform target, LevelTransform t)
        {
            if (t == null) return;
            // Сначала выставляем как мировые (родитель — корень сцены), парентинг
            // с worldPositionStays происходит позже и мировые значения сохраняет.
            target.position = UeToUnityPosition(t.locationCm);
            target.rotation = UeToUnityRotation(t.rotationQuat);
            target.localScale = UeToUnityScale(t.scale);
        }

        static Vector3 UeToUnityPosition(float[] cm)
        {
            if (cm == null || cm.Length < 3) return Vector3.zero;
            return new Vector3(
                BASIS[1] * cm[BASIS[0]],
                BASIS[3] * cm[BASIS[2]],
                BASIS[5] * cm[BASIS[4]]) * 0.01f;
        }

        static Quaternion UeToUnityRotation(float[] q)
        {
            if (q == null || q.Length < 4) return Quaternion.identity;
            // Поворот через J: вектор кватерниона = det(J)·(J·v), скаляр w без изменений.
            // det учитывает отражение (иначе повороты идут в обратную сторону).
            int det = BasisDet();
            return new Quaternion(
                det * BASIS[1] * q[BASIS[0]],
                det * BASIS[3] * q[BASIS[2]],
                det * BASIS[5] * q[BASIS[4]],
                q[3]);
        }

        static Vector3 UeToUnityScale(float[] s)
        {
            if (s == null || s.Length < 3) return Vector3.one;
            // Масштаб — величины по осям, знаки перестановки отбрасываем.
            return new Vector3(s[BASIS[0]], s[BASIS[2]], s[BASIS[4]]);
        }

        static string ScenePathFor(string targetRoot, LevelEntry entry)
        {
            var rel = PathMap.StripGame(entry.uePath);
            var dir = rel.Contains("/") ? rel.Substring(0, rel.LastIndexOf('/')) : "";
            var name = string.IsNullOrEmpty(entry.name) ? "Level" : entry.name;
            return string.IsNullOrEmpty(dir)
                ? $"{targetRoot}/Scenes/{name}.unity"
                : $"{targetRoot}/Scenes/{dir}/{name}.unity";
        }
    }
}
