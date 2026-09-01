# Uasset Converter — Unity edition (Editor Asset)

A self-contained build of the Unreal → Unity converter: the **entire tool** (Python pipeline + C# editor window) lives in a single `UassetConverter/` folder. Drop it into your Unity project and run export/conversion straight from the editor — no separate repository, no typing paths to the scripts by hand.

This is a copy of the main tool. The **Python code and pipeline logic are identical** to the original — only how the C# window locates `convert.py` (now next to itself) and the output path changed.

## Requirements

- **Unity** 6000.x or 2022.3 LTS with **URP** (HDRP is not supported).
- **Python 3** installed on the machine, with `numpy` and `Pillow`. The interpreter is not bundled — the system Python is used.
- **Unreal Engine 5.8** — for the export step (the engine's native exporters are used).

## Install

1. Copy the `UassetConverter/` folder into your Unity project's `Assets/`:

   ```
   <YourProject>/Assets/UassetConverter/
   ```

2. Let Unity compile the C#. The menu **Tools → Uasset Converter → Convert** appears.

3. Open the window. The converter path (`convert.py`) is filled in **automatically** — it sits in the same folder. Click **Autodetect** next to the Python field: the window finds the system Python 3. If `numpy`/`Pillow` are missing, it shows the exact `pip install …` command — run it once.

## Usage

1. Point to the Unreal project (`.uproject`), the engine folder, and a scope (`test` for a trial run).
2. **Export** — export assets from Unreal (progress shown in the window).
3. **Convert & Import** — transpile shaders, post-process, and import the result into the project.

Individual steps (Export / Shaders / Postprocess) and "Import only" are available as buttons in the same window.

## Where files go

- **Output** (`output/`) goes to `<YourProject>/UassetConverterOutput/`, **outside** `Assets/`, so intermediate data doesn't clutter the project. Configurable in the window.
- **Final assets** go to `Assets/UassetConverted/` (configurable).
- **config.json** lives next to the scripts inside `Assets/UassetConverter/`. When you change settings in the window, Unity may briefly reimport that file — this is expected.

## Shader output — HLSL only

Materials are transpiled into ready HLSL `.shader` files for **URP**. Shader Graph output is **not supported** — its C# and Python parts have been removed entirely. A hand-written Lit shader for HDRP is impossible (its passes are pipeline-internal), so in an HDRP project materials get no shaders: this build targets URP/HLSL.

## UI language

Locales are shared between C# and Python (`unity/com.uasset.converter/Editor/Locales/`). Switching language in the window is saved to the config, so reports (`unsupported.md`) are generated in the same language.
