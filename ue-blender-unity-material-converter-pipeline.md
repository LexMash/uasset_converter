# План: пайплайн «UE → Blender → Miku Material Converter → Unity» — конвертер материалов в редактируемые Shader Graph

**Статус:** план (исследования завершены, реализация не начата).
**Дата:** 2026-09.

---

## 0. TL;DR

- **Цель:** получать из материалов Unreal Engine *редактируемые* шейдерные графы Unity Shader Graph (настоящие ноды, не «одна нода-обёртка») без ручного авторства `.shadergraph`-JSON.
- **Целевая цепочка:** `UE (ваш ue_export.py) → Blender 5.x (наш аддон-мост) → Miku Material Converter (headless) → Unity 6000.x URP/SG 17 (редактируемый .shadergraph) → материалы (MaterialBuilder)`.
- **Готового конвертера UE→Blender 5.x не существует** — мост UE→Blender делаем сами: Blender-аддон, читающий `manifest.json` (полные графы мастер-материалов уже там).
- **Miku Material Converter** (MIT, Blender 5.0–5.2 → Unity 6000.x/SG 17.0–17.5) — единственный живой бесплатный конвертер «Blender→редактируемый Shader Graph»; headless-режим подтверждён (публичная функция `export_selected_materials`).
- MaterialX-путь (`.mtlx` → Unity) **закрыт**: официального импортёра не существует.

---

## 1. Итоги исследований (сводно; полные отчёты — раздел «Источники»)

### 1.1. Публичного API Shader Graph нет
- Страница `api/index.html` пакета 17.6 — пустая заглушка («This is the documentation for the scripting APIs…»).
- Полный список задокументированных типов — в `api/toc.html` (docfx); из «строительных» типов публичны только данные: `JsonObject` (база сериализации), `GroupData`, `RedirectNode`, свойства (`ShaderInput`, `AbstractShaderProperty`, `*ShaderProperty`), `GraphCode`, `OutputMetadata`, `ShaderGraphRequirements` и GUI-классы. Страницы `GraphData`/`BlockNode`/`AbstractMaterialNode`/`CustomFunctionNode` — **404**.
- В исходниках: `sealed partial class GraphData : JsonObject` и `abstract class AbstractMaterialNode : JsonObject` — **без модификатора доступа → internal** (сборка `Unity.ShaderGraph.Editor`).

### 1.2. «Доверенные сборки»: свою сборку добавить нельзя
- `Editor/AssemblyInfo.cs` пакета объявляет `InternalsVisibleTo` для фиксированного списка из 13 имён (все внутренние Unity): `Unity.ShaderGraph.Editor.Tests`, `Unity.RenderPipelines.Universal.Editor`, `Unity.ShaderGraph.GraphicsTests`, `Unity.ShaderGraph.Editor.GraphicsTests`, `Unity.RenderPipelines.HighDefinition.Editor`, `Unity.VisualEffectGraph.Editor`, `Unity.Industrial.Materials.AVRD.Editor`, `Unity.VisualEffectGraph.EditorTests`, `Unity.XR.Quantum.Editor.ShaderGraph`, `Unity.ShaderGraphTool.Editor`, `Unity.ShaderFoundry.Editor`, `Unity.Environment.Editor.ShaderGraph`, `Unity.AI.Assistant.Bridge.Editor`.
- Добавить свою сборку без пересборки пакета невозможно (форк ограничен Unity Companion License). Рабочий путь — **рефлексия по internal-типам**, либо форк пакета (план Б).

### 1.3. `.shadergraph` — это MultiJson
- Файл — последовательность JSON-объектов (первый — `GraphData` с `m_SGVersion`/`m_Type`/`m_ObjectId`, ссылки по 32-hex `m_Id`; затем по объекту на ноду/слот/свойство/категорию/таргет), `m_Script` в файле нет — импортирует `ScriptedImporter` (`ShaderGraphImporter`, extension `shadergraph`). Легаси-формат (SG ≤ 12): один top-level объект с `m_SerializableNodes`/`m_SerializableEdges` + вложенными JSON-строками.

### 1.4. Механика «собрать граф кодом» (подтверждена тремя независимыми эталонами)
Сам Unity строит графы так (тесты пакета, `NewGraphAction` в `GraphUtil.cs`):
```csharp
var graph = new GraphData();
graph.AddContexts();
graph.InitializeOutputs(null, null);            // или targets
graph.AddCategory(CategoryData.DefaultCategory());
graph.path = "Shader Graphs";
FileUtilities.WriteShaderGraphToDisk(path, graph);  // MultiJson.Serialize + write
AssetDatabase.Refresh();
```
Рефлексивный рецепт (E1a): типы по именам из сборки `Unity.ShaderGraph.Editor`; граф + URP-таргет (`UnityEditor.Rendering.Universal.ShaderGraph.UniversalTarget` из `Unity.RenderPipelines.Universal.Editor`, `TrySetActiveSubTarget(UniversalLitSubTarget)`) → `AddContexts` → `InitializeOutputs` → `AddRemoveBlocksFromActiveList(GetActiveBlocksForAllActiveTargets())` → `AddNode`/`Connect(SlotReference, SlotReference)` → `ValidateGraph()` → `MultiJson.Serialize` → запись → `AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport)`.

Эталоны, где это работает в проде:
- **unity-mcp-plugin** `MCPShaderGraphApi.cs` — рефлексивные обёртки, документированные подводные камни (перегрузки `AddNode` 17.3 vs 17.6; несвязанный `PropertyNode` → NRE при импорте; цикл `OnEnable()`+`ValidateGraph()`).
- **Miku** `MikuShaderGraph17RuntimeBackend.cs` (4818 строк) — промышленная реализация: версионные адаптеры `ShaderGraph17_0Adapter…17_5Adapter` с `Preflight()`, `Activator.CreateInstance` нод/слотов, детерминированная генерация MultiJson (`StabilizeMultiJson`), шаблон верхнего враппера, запись + `ForceSynchronousImport`.

### 1.5. Unity CLI
- Бинарник `unity` (пакет `unity-cli`), экспериментальный; **команд для шейдеров/Shader Graph нет**.
- C#-код редактора: `unity run --command <name>` (Unity Pipeline package + `[CliCommand]`), `unity build --execute-method <Method>`, `unity eval '<expr>'` (запущенный редактор), классика `Unity.exe -batchmode -nographics -quit -executeMethod`. Полный отчёт — `UNITY_CLI_REPORT_ru.md`.

### 1.6. Бесплатные нодовые редакторы с импортом в Shader Graph
| Инструмент | Направление | Результат | Вердикт |
|---|---|---|---|
| **Miku Material Converter** ([repo](https://github.com/GenshinmasterJinHang/Miku-Material-Converter-Blender-to-Unity-), 166★, MIT, v3.0.0 13.08.2026) | Blender 5.0–5.2 (EEVEE) → Unity 6000.x URP/SG 17.0–17.5 | **редактируемый** `.shadergraph`-враппер + `.generated.shadersubgraph` с настоящими нодами SG (SampleTexture2D/Multiply/Lerp/Branch/NormalUnpack…), Master Stack автоматически; Custom Function HLSL для нод без аналога; запекание части процедурных текстур; статус Experimental | ✅ выбор |
| blender-nodes-subgraph (Warwlock, заброшен 01.2023) | Blender 3.x–4.0 → SG | весь граф Blender сворачивается в **одну** Custom Function ноду (не редактируется по частям); не поддерживает BSDF; поломки на новых SG | ❌ |
| blender-nodes-for-unity3d (GPL-3.0) | — | библиотека «нод Blender» для ручной сборки в SG, **не конвертер** | ❌ |
| hlsl-to-shadergraph-bridge | HLSL → `.shadersubgraph` | субграф из одной функции | ⚠️ вспомогательный |
| Amplify Shader Editor ($80) | — | конвертера ASE → SG нет | ❌ |
| MaterialX (`.mtlx` → Unity) | — | официального/зрелого импортёра **нет вообще** (реестр, доки 0.1–2.3, релиз-ноты 6000.0–6000.4, GitHub Unity — всё 404/0); USD-импорт даёт стандартный материал без графа | ❌ закрыт |

### 1.7. Конвертеры «UE → Blender» материалов
| Кандидат | Направление | Статус |
|---|---|---|
| [Waffle1434/Blender-UE4-Importer](https://github.com/Waffle1434/Blender-UE4-Importer) (91★) | UE4.16–4.27 → Blender, «pixel-perfect material recreation» из `.uasset` | единственный реальный материал-импортёр, но парсит бинарные `.uasset` (хрупко), UE5 не подтверждён, заброшен 11.2023, под Blender 3.x — **под Blender 5.x не работает** |
| [matyalatte/Blender-Uasset-Addon](https://github.com/matyalatte/Blender-Uasset-Addon) (78★) | UE → Blender | только меши + текстуры, графов материалов нет |
| [KiKoZl1/uefn-blender-bridge](https://github.com/KiKoZl1/uefn-blender-bridge) (16★, 2026) | UEFN ↔ Blender live-sync | только Fortnite/UEFN |
| SpectralVectors/TransMat, angjminer/blueman | Blender → UE | обратное направление |
| EdBoucher/Blender_UE5_MatPack | атласы для interop | не конвертер |

**Вывод: готового живущего конвертера UE→Blender 5.x нет → мост делаем сами** (см. раздел 4). Вход для моста уже есть: `ue_export.py` дампит в `manifest.json`:
- `material_graphs[]` — ноды (`type` = `MaterialExpression*`, `props`, `inputs` с `from`/`from_output`, `outputs`), выходы по `MaterialProperty` (`outputs`), дефолты параметров (`parameters`), `blend_mode`, `shading_model`, `two_sided`, opacity-mask;
- `material_functions[]` — графы функций материалов для инлайна.

---

## 2. Требования Miku к Blender-стороне (проверено по исходникам)

- Материал = обычное нодовое дерево; Miku снимает **семантический снапшот**: сокеты `Base Color / Metallic / Roughness / Alpha / Emission Color / Normal` (+ passthrough-цепочки `Color.Ramp`, `Math`, `Mix`, `RGBToBW`, `Reroute`; ORM-упаковка; различение Bump vs Normal Map; вывод AlphaMode; Roughness→Smoothness flag).
- Текстуры — обычные `Image Texture` (импортируются по путям из вашего `output/`).
- Экспорт: панель «Standard PBR» или публичная функция `export_selected_materials` (headless, `blender --background` — подтверждено CI самого Miku).
- Параметры: у Miku есть система runtime inputs (экспонируемые параметры) — способ вывода параметров UE в свойства SG проверяется в шпике (константы в нодах vs входы групп).

---

## 3. Архитектура пайплайна

```
Unreal (5.8, read-only)                  Blender 5.x (headless)           Unity 6000.x (URP / SG 17)
┌────────────────────────────┐   ┌───────────────────────────────────┐   ┌──────────────────────────┐
│ convert.py --step export   │   │ аддон «UE→Blender» (наш, python)  │   │ com.miku.shaderconverter │
│  ue_export.py:             │   │ читает manifest.json:             │   │ импортёр .mikubundle:    │
│  • текстуры → output/      │──►│  material_graphs → нодовое дерево │──►│ создаёт РЕДАКТИРУЕМЫЙ    │
│  • material_graphs[] →     │   │  (Principled BSDF + ноды)         │   │ .shadergraph (+субграф с │
│    manifest.json           │   │ Miku export_selected_materials    │   │ нативными нодами SG)     │
└────────────────────────────┘   │ → .mikubundle в output/blender/   │   │ материалы — MaterialBuilder│
                                 └───────────────────────────────────┘   └──────────────────────────┘
```

Шаги:
1. **UE:** существующий шаг `export` (текстуры + `manifest.json` с полными графами).
2. **Blender:** наш аддон строит материалы (Principled-деревья) по `manifest.json`; Miku (headless) экспортирует `.mikubundle`.
3. **Unity:** пакет Miku импортирует бандлы → `.shadergraph`; `MaterialBuilder` создаёт материалы на SG-шейдерах (маппинг свойств — в шпике).

---

## 4. Компоненты для реализации

### 4.1. Blender-аддон «UE→Blender» (`tools/blender/ue_material_bridge.py`, новый, Python)
- Вход: путь к `manifest.json` и каталогу текстур (режим чтения конфига как у остальных шагов; для headless — аргументы CLI).
- Для каждого `material_graphs[]`:
  1. материал + `blend_method` по `blend_mode` (Opaque/Cutout/Translucent), `two_sided`;
  2. выходы `outputs`: BaseColor→Principled «Base Color», Metallic, Roughness, Normal→ через `Normal Map`, Emissive, Opacity→Alpha;
  3. маппинг UE `MaterialExpression*` → Blender-ноды (≈40 операций; по образцу IR в `graph_ir.py`): TextureSample→Image Texture+Separate; Multiply/Add/Subtract/Divide→Math; Lerp→Mix; Clamp/Saturate/OneMinus→Math; TextureCoordinate→Texture Coordinate+Mapping (Tiling/Offset); Panner/Rotator→группы math; Normal→Normal Map; Constant/VectorParameter→Value/RGB (или Group Input для экспозиции); Fresnel/SphereMask/Desaturation→группы math (код уже есть как `call`-функции); `MaterialFunctionCall`→инлайн по `material_functions[]`; неизвестные узлы→константа+лог (`UAC|LOG|`), как TODO-политика `graph_ir.py`;
  4. текстуры из `output/`; нормальные карты — коррекция каналов при построении.
- Режимы: GUI-панель (ручная проверка) и `--background --python` (конвейер).

### 4.2. Конвейерный вызов Miku (headless)
- Установка расширения Miku в Blender (zip/Extensions), Blender **5.0–5.2** (пин).
- `blender --background --python <скрипт>`: `export_selected_materials` по каждому материалу → `.mikubundle` в `output/blender_bundles/<material>/`.

### 4.3. Unity-сторона
- Пакет `com.miku.shaderconverter` в проекте; бандлы копируются в `Assets/` → импортёр генерирует `.shadergraph`.
- `MaterialBuilder.cs`: `Shader.Find("<имя SG-шейдера>")`; свойства материала — через сгенерированные SG-свойства (имена сверяются в шпике).
- Итоговый `.shadergraph` открывается в редакторе Shader Graph и редактируется (настоящие ноды).

### 4.4. Порядок работ и критерии приёмки
1. **Шпик (1 материал):** UE-export → аддон (Blender headless) → Miku → Unity. Критерии: `.shadergraph` создан и импортирован без ошибок; внутри настоящие ноды (SampleTexture2D/Multiply/Lerp…); материал не розовый; визуально сопоставим с текущим HLSL-результатом (прогон RenderCheck-образца).
2. **Полный конвейер:** все материалы манифеста; идемпотентность; повторные запуски не портят user-owned враппер.
3. **Документация:** этот план — базовый рецепт; полные отчёты исследований — в `docs/` (см. Источники).

---

## 5. Риски и допущения (честно)

- **Двойная трансляция** (UE→Blender→Miku→SG) теряет точность: Miku-матрица поддержки (Exact/Equivalent/Approximate/Baked/Unsupported) режет часть семантики; неподдерживаемые UE-узлы ловим на нашем шаге (лог).
- **Параметры материалов:** способ экспозиции в свойства SG (runtime inputs) — проверить в шпике; при неудаче параметры становятся константами (возможно приемлемо).
- **Версии:** Blender 5.0–5.2; Unity 6000.x + URP + SG 17.0–17.5; Miku — Experimental; внутренний API SG — версионно-хрупкий (но в данной цепочке мы его не трогаем напрямую — за нас это делает Miku).
- **Лицензии:** Miku MIT (частично GPL «Blender Bake Worker» — только при использовании запекания); наш аддон — собственный код.
- **Запасной путь:** существующий HLSL-транспайлер (`backend_hlsl.py` → `.shader`, fallback в `postprocess.py`) остаётся: если Miku-цепочка не справилась с материалом — падаем на него. Прямой UE→SG через рефлексию (без Blender) — исследованный запасной вариант, механизм полностью описан в `.research/`.

---

## 6. Источники

**Полные отчёты исследований (в репозитории):**
- `.research/shadergraph_scripting_report.md` — внутренний API SG 17.6, формат `.shadergraph`, рецепты E1a/E1b/E2/E3, batch mode.
- `.research/blender_sg_converters_report.md` — все найденные конвертеры «нодовый редактор → SG», разбор Miku и Warwlock-проектов.
- `.research/materialx_unity_report.md` — MaterialX-путь (закрыт).
- `UNITY_CLI_REPORT_ru.md` — Unity CLI (команды, запуск C#-кода, CI).

**Ключевые внешние источники (проверены HTTP в сессии):**
- [Miku Material Converter](https://github.com/GenshinmasterJinHang/Miku-Material-Converter-Blender-to-Unity-) (+ `docs/architecture/blender-exporter.md`, `miku_blender` — batch export, `Editor/MikuShaderGraph17RuntimeBackend.cs`).
- [MCPShaderGraphApi.cs (эталон рефлексии)](https://github.com/AnkleBreaker-Studio/unity-mcp-plugin/blob/main/Editor/MCPShaderGraphApi.cs).
- [SG 17.6 api/toc.html](https://docs.unity3d.com/Packages/com.unity.shadergraph@17.6/api/toc.html) · [AssemblyInfo.cs пакета](https://github.com/Unity-Technologies/Graphics/blob/master/Packages/com.unity.shadergraph/Editor/AssemblyInfo.cs) · [GraphData.cs](https://github.com/Unity-Technologies/Graphics/blob/master/Packages/com.unity.shadergraph/Editor/Data/Graphs/GraphData.cs).
- [Waffle1434/Blender-UE4-Importer](https://github.com/Waffle1434/Blender-UE4-Importer) · [matyalatte/Blender-Uasset-Addon](https://github.com/matyalatte/Blender-Uasset-Addon) · [KiKoZl1/uefn-blender-bridge](https://github.com/KiKoZl1/uefn-blender-bridge).
- [Unity CLI reference](https://docs.unity.com/en-us/unity-cli/unity-cli-reference) · [EditorCommandLineArguments](https://docs.unity3d.com/6000.0/Documentation/Manual/EditorCommandLineArguments.html).