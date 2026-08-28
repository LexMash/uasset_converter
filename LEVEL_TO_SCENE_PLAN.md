# План: конвертация Unreal Level в Unity Scene

## Краткое резюме

Добавить отдельный режим `Levels`, в котором пользователь выбирает несколько `.umap`. Python внутри Unreal:

1. загружает каждый level;
2. собирает переносимые объекты и источники света;
3. строит замыкание связанных ассетов;
4. экспортирует каждый ассет только один раз;
5. пишет отдельный JSON-файл данных для каждого level.

Unity после обычного импорта ресурсов создаёт `.unity`-сцены с теми же именами и структурой объектов.

Текущий режим конвертации обычных ассетов сохраняется без изменений.

## Основные изменения

### 1. Выбор level и порядок pipeline

В конфиг добавить:

```json
{
  "scope": {
    "mode": "levels",
    "level_paths": [
      "/Game/Maps/Level_A",
      "/Game/Maps/Level_B"
    ]
  }
}
```

Оба интерфейса — Python GUI и Unity Editor — получают список найденных рекурсивно `.umap` с чекбоксами.

Новый порядок:

1. Проверить выбранные levels и конфликт отключённых обязательных типов ассетов.
2. Загрузить levels в Unreal через `LevelEditorSubsystem.load_level`.
3. Получить загруженные Actors через `EditorActorSubsystem.get_all_level_actors`; API работает с загруженными объектами, поэтому незагруженные World Partition элементы фиксируются в отчёте.
4. Собрать зависимости и убрать дубликаты по UE-пути.
5. Экспортировать ассеты существующим `ue_export.py`.
6. Записать JSON каждого level.
7. Выполнить `shader_gen.py`.
8. Выполнить `postprocess.py`.
9. Unity импортирует ресурсы в текущем порядке.
10. После материалов и моделей Unity создаёт Scenes.

Если отключены `static_meshes`, `materials` или `textures`, UI показывает подтверждение. В CLI запуск завершается с ошибкой и подсказкой; для явного разрешения добавляется `--allow-required-assets`.

### 2. Сбор объектов Unreal

В level переносятся:

- обычные `StaticMeshComponent`;
- `InstancedStaticMeshComponent` и `HierarchicalInstancedStaticMeshComponent`;
- `DirectionalLightComponent`;
- `PointLightComponent`;
- `SpotLightComponent`;
- `RectLightComponent`.

Для каждого mesh-компонента сохраняются:

- ссылка на Static Mesh;
- имя Actor и компонента;
- transform;
- visibility/hidden state;
- material overrides по индексам слотов;
- идентификатор исходного Actor/Component;
- индекс инстанса для ISM/HISM.

ISM/HISM разворачиваются в отдельные GameObject с общей ссылкой на mesh и материалами.

Не переносятся:

- Niagara и обычные particle-компоненты;
- Blueprint-логика;
- камеры;
- SkyLight;
- аудио;
- физические ассеты и коллизии;
- Landscape, Decal и прочие неподдерживаемые компоненты.

Все пропуски записываются с level-путём, Actor-путём, компонентом и причиной.

### 3. Дедупликация ассетов

Глобальный набор ассетов собирается сразу для всех выбранных levels.

Ключ дедупликации:

```text
UE asset path: /Game/Folder/Asset
```

Если mesh, material или texture используется несколькими Actor/level:

- экспортируется один раз;
- имеет одну запись в manifest;
- в Scene JSON остаются только ссылки;
- Unity использует один и тот же импортированный ресурс.

Зависимости собираются от:

- Static Mesh;
- material slots;
- material overrides;
- связанных материалов и текстур.

Ассеты, пришедшие только из particle/Niagara-компонентов, в замыкание переносимых ассетов не добавляются.

### 4. Формат файлов

Для каждого level создаётся:

```text
output/Levels/<UE-relative-path>/<LevelName>.json
```

В `unity_manifest.json` добавляется список этих файлов:

```json
{
  "levels": [
    {
      "uePath": "/Game/Maps/Level_A",
      "name": "Level_A",
      "file": "Levels/Maps/Level_A.json"
    }
  ]
}
```

Структура level JSON:

```json
{
  "schemaVersion": 1,
  "uePath": "/Game/Maps/Level_A",
  "name": "Level_A",
  "actors": [],
  "objects": [],
  "lights": [],
  "skipped": []
}
```

`actors` содержит:

- стабильный `id` из UE object path;
- label/name;
- `parentId`;
- transform;
- active/hidden state.

`objects` содержит:

- `id`;
- `parentId`;
- `mesh`;
- transform;
- `materialOverrides`;
- тип компонента;
- индекс ISM-инстанса.

`lights` содержит:

- `id`;
- `parentId`;
- тип Unreal и тип Unity;
- transform;
- цвет;
- intensity и исходные единицы;
- attenuation/range;
- spot cone angles;
- Rect source width/height;
- cast shadows;
- visibility;
- mobility;
- temperature;
- неподдержанные параметры в metadata.

Все структуры добавляются в C#-модель без словарей, чтобы формат оставался совместимым с `JsonUtility`.

### 5. Transform и координаты

Формат JSON хранит исходные UE-значения:

```json
{
  "locationCm": [x, y, z],
  "rotationQuat": [x, y, z, w],
  "scale": [x, y, z]
}
```

В Unity применяется единая конвертация:

```text
Unity position = (UE.Y, UE.Z, UE.X) / 100
Unity scale    = (UE.Y, UE.Z, UE.X)
```

Вращение преобразуется через basis matrix, а не через Euler angles:

```text
RUnity = C * RUnreal * inverse(C)
```

Это сохраняет UE origin, переводит сантиметры в метры и исключает ошибки порядка вращения.

Для attachments сохраняется иерархия Actor. Для каждого поддержанного компонента создаётся дочерний GameObject под Actor.

### 6. Создание Unity Scene

Добавить отдельный Editor-класс `LevelSceneBuilder`.

Scene сохраняется в:

```text
<targetRoot>/Scenes/<UE-relative-path>/<LevelName>.unity
```

Например:

```text
Assets/UassetConverted/Scenes/Maps/Level_A.unity
```

Алгоритм:

1. Открыть пустую additive Scene.
2. Создать корневой GameObject с именем level.
3. Создать Actor hierarchy.
4. Создать mesh-инстансы через импортированные FBX-модели.
5. Применить material overrides только к экземпляру в сцене, не изменяя prefab/FBX.
6. Создать Light GameObject и настроить `UnityEngine.Light`.
7. Сохранить Scene через `EditorSceneManager.SaveScene`.
8. Закрыть временно открытую Scene, не меняя рабочую сцену пользователя.

Если Scene с таким путём существует:

- показать предупреждение;
- дождаться подтверждения пользователя;
- при отказе пропустить только этот level;
- при подтверждении перезаписать generated Scene.

### 7. Свет

Соответствие типов:

| Unreal | Unity |
|---|---|
| Directional | Directional |
| Point | Point |
| Spot | Spot |
| Rect | Area |

Параметры:

- `attenuation_radius / 100` → `Light.range`;
- outer cone → `spotAngle`;
- inner cone → `innerSpotAngle`;
- Rect `source_width/source_height / 100` → `areaSize`;
- Unreal color → Unity color;
- `cast_shadows` → `Light.shadows`;
- `hidden_in_game/visible` → `Light.enabled`;
- Static → Baked;
- Stationary → Mixed;
- Movable → Realtime.

Для intensity:

- сохранить исходное значение и единицу;
- вычислить приблизительное Unity-значение;
- применить `unity.light_intensity_multiplier`, по умолчанию `1.0`;
- для lumens/candelas использовать light-type-aware conversion;
- для неизвестных и unitless значений использовать исходное число;
- неподдержанные параметры (`IES`, Light Function, volumetric и специальные UE shadow settings) записывать в отчёт.

Основной пакет не должен иметь обязательной зависимости от HDRP, поэтому используется общий `UnityEngine.Light` API.

## Изменяемые интерфейсы

- `scope.mode`: добавить значение `levels`.
- `scope.level_paths`: массив UE-путей.
- CLI: добавить `--allow-required-assets`.
- `UassetManifest`: добавить `levels`.
- Добавить сериализуемые типы `LevelFileEntry`, `LevelData`, `LevelActorData`, `LevelObjectData`, `LevelLightData`, `LevelTransformData`.
- `UassetImportWindow.Import`: после импорта анимаций вызывать `LevelSceneBuilder`.
- `postprocess.py`: переносить level entries в `unity_manifest.json` и проверять ссылки на отсутствующие ресурсы.
- `unsupported.md`: добавить секции по пропущенным Actor/Component/Level элементам.

Основные точки изменений:

- `C:\Users\StratoCat\Desktop\uasset_converter\ue_export.py`
- `C:\Users\StratoCat\Desktop\uasset_converter\convert.py`
- `C:\Users\StratoCat\Desktop\uasset_converter\postprocess.py`
- `C:\Users\StratoCat\Desktop\uasset_converter\unity\com.uasset.converter\Editor\UassetManifest.cs`
- `C:\Users\StratoCat\Desktop\uasset_converter\unity\com.uasset.converter\Editor\UassetImportWindow.cs`
- новый `LevelSceneBuilder.cs`.

## Тестирование и критерии готовности

Python-тесты:

- обнаружение нескольких `.umap`;
- корректное заполнение `scope.level_paths`;
- дедупликация общего mesh/material/texture;
- формирование JSON с Actor hierarchy;
- material overrides;
- ISM/HISM с несколькими инстансами;
- все четыре типа света;
- пропуск Niagara и неподдерживаемых компонентов;
- обнаружение ссылок на неэкспортированные ассеты;
- сохранение старых тестов asset-only pipeline.

Unity-проверки:

- из двух levels создаются две Scene;
- общий mesh импортирован один раз;
- Scene иерархия совпадает с Unreal attachments;
- UE `+X/+Y/+Z` корректно соответствуют Unity осям;
- сантиметры корректно переводятся в метры;
- material override меняет только конкретный экземпляр;
- ISM создаёт нужное количество объектов;
- свет сохраняет тип, transform, цвет, range, cone и mobility;
- unsupported-компоненты не создают GameObject;
- повторный импорт запрашивает подтверждение и корректно перезаписывает generated Scene;
- URP-проект компилируется без HDRP-зависимостей, HDRP-проект импортирует те же данные.

## Зафиксированные допущения

- В v1 поддерживаются только StaticMesh/ISM/HISM и четыре типа источников света.
- Обрабатываются все доступные загруженные Actors; незагруженные World Partition элементы не форсируются.
- Каждый `.umap` получает отдельный JSON и отдельную Unity Scene.
- Общие ассеты между levels экспортируются единожды.
- Сцены перезаписываются только после явного подтверждения.
- Скелетные меши и анимации остаются частью существующего asset pipeline, но не создают объекты в level Scene.
