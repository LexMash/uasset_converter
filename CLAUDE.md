# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working style (user preferences)

- Отвечай кратко и по существу — без лишних предисловий и повторов.
- Не делай, если не понимаешь задачу: не угадывай и не начинай наугад.
- Всегда уточняй намерение пользователя, прежде чем действовать при любой неоднозначности.

## What this is

An Unreal Engine → Unity asset converter. It exports art (textures, meshes, animations, materials) from a **source** Unreal project and imports it into Unity. The distinguishing feature: Unreal master materials are **transpiled into real Unity HLSL shaders**, not approximated by hand-tuned values. The target pipeline is **URP** (HDRP is not supported).

The codebase is bilingual, Russian-first: docstrings and comments are in Russian, user-facing strings are localized (see i18n below). `README.md` is Russian, `README.en.md` is the English translation. Match the surrounding language when editing comments.

## Commands

```bash
python -m pip install -r requirements.txt   # numpy, Pillow (runtime); pytest (dev)

python convert.py                            # launch the tkinter GUI
python convert.py --cli --step all           # run whole pipeline headless
python convert.py --cli --step export        # a single step: export|shaders|postprocess|all
python convert.py --cli --step all --scope test --lang en   # override config for this run

python -m pytest                             # all tests
python -m pytest tests/test_nodes.py         # one file
python -m pytest tests/test_shader_golden.py::test_texture_sampled_once_per_uv   # one test
UAC_REGEN=1 python -m pytest tests/test_shader_golden.py   # regenerate shader golden files after an intentional change

python tools/lint_locales.py                 # validate locale files (also used in CI)
python tools/lint_locales.py --new de        # scaffold a new language
python tools/build_locales.py                # rebuild the bundled en/ru locales from the source table
```

There is no build step for the Python side. The Unity side is a UPM package under `unity/com.uasset.converter/` (C#, compiled by Unity).

## Architecture

The pipeline is a chain of numbered layers. Data flows one direction; the `output/` folder is the **only** contract between the Unreal side and the Unity side.

```
Unreal ──► convert.py (3 steps) ──► output/ ──► Unity importer (C#) ──► Assets/
```

- **Layer 0 — [gui.py](gui.py)** — tkinter UI. Computes nothing itself: it edits `config.json` and runs the same steps as the CLI. Anything doable in the GUI is reproducible with `python convert.py --cli`.
- **Layer 1 — [ue_export.py](ue_export.py)** — runs **inside `UnrealEditor-Cmd`**, imports `unreal`. Exports textures/meshes/animations with the engine's native exporters and dumps material + master-graph descriptions to `output/manifest.json`. The project is opened **read-only**. Config is passed in via the `UASSET_CONV_CONFIG` env var. `convert.py` launches it with the full editor + `-RenderOffScreen` (not the `pythonscript` commandlet), because skeletal-mesh export needs a live RHI.
- **Layer 2 — shader transpiler**, split so it can emit two formats from one traversal:
  - [graph_ir.py](graph_ir.py) — traverses the Unreal material graph into a small (~30-op) neutral IR. Unknown nodes are never invented: they become a TODO plus a passthrough of the first input.
  - [backend_hlsl.py](backend_hlsl.py) — renders IR → `.shader` text, using [shader_template.py](shader_template.py) (a hand-written, self-contained URP template that does not `#include` LitInput.hlsl, so it survives URP package updates).
  - [shader_gen.py](shader_gen.py) — orchestration only: reads the manifest, de-dupes identical graphs by structural signature, writes `Shaders/*.shader` and `shaders.json`.
- **Layer 3 — [postprocess.py](postprocess.py)** — turns the raw Unreal manifest into a Unity-terms manifest so the C# side never has to guess. Resolves material-instance parameter chains to the master, picks transpiled-shader vs. URP/Lit fallback, repacks mask channels (Unreal roughness → Unity smoothness), flips the normal green channel, and applies the animation-loop heuristic. Produces `unity_manifest.json` and `unsupported.md`.

`convert.py` also exposes machine commands (`--config-dump`, `--config-set`, `--scan-folders`, `--list-languages`, `--porcelain`) so the Unity window can drive the pipeline without parsing `config.json` in C#.

### Unity side (`unity/com.uasset.converter/Editor/`)

C# editor package, menu **Tools → Uasset Converter**. `ProcessRunner.cs` runs `convert.py --porcelain` as a background process (never `WaitForExit` — exports take tens of minutes) and reads stdout on worker threads, parsing it in `EditorApplication.update`. It then reads `output/unity_manifest.json` to create Unity assets. The `RenderCheck` sample (URP-only) renders a scene of converted meshes to PNG for visual verification.

**Level → Scene** is a **separate step**, not part of the main Import: menu **Tools → Uasset Converter → Import Levels** ([LevelImportWindow.cs](unity/com.uasset.converter/Editor/LevelImportWindow.cs)). It is deliberately not folded into Import because building a scene replaces editor content — the user triggers it explicitly, after meshes/materials are already imported. [SceneBuilder.cs](unity/com.uasset.converter/Editor/SceneBuilder.cs) reads each `output/Levels/*.json` (schema written by [level_export.py](level_export.py), `schemaVersion` checked), builds **one `.unity` scene per level** in an *additive* temp scene so the user's working scene is untouched, saves it to `<targetRoot>/Scenes/<UE-rel>/<Name>.unity`, then closes it (prompts before overwriting an existing generated scene). Meshes are placed as **prefab instances** of the imported models (`PrefabUtility.InstantiatePrefab`), with material overrides applied per-instance via `MaterialBuilder.LoadExisting`. Lights map Unreal → `UnityEngine.Light`. The level-JSON models (`LevelEntry`/`LevelFile`/`LevelActor`/`LevelObject`/`LevelLight`/`LevelTransform`/`MaterialOverride`) live in [UassetManifest.cs](unity/com.uasset.converter/Editor/UassetManifest.cs).

**Coordinate conversion is C#'s job alone** ([level_export.py](level_export.py) stores raw UE transforms — cm + UE-axis quaternion — precisely so the axis/metric conversion lives in exactly one place and can't desync). The formula (from `LEVEL_TO_SCENE_PLAN.md` §5, implemented in `SceneBuilder.UeToUnity*`): position `= (UE.Y, UE.Z, UE.X) / 100`, scale `= (UE.Y, UE.Z, UE.X)`, rotation quat `= (qy, qz, qx, qw)` — a cyclic axis permutation with no sign flips (basis matrix has det +1, both spaces left-handed; equivalent to the plan's `C·R·C⁻¹`). **These quaternion signs have not yet been visually verified in Unity** — if generated scenes come out mirrored or rotated, `SceneBuilder.UeToUnity*` is the single place to fix it; check with the `RenderCheck` sample.

## Cross-cutting conventions

- **The `UAC|` protocol.** All machine-readable output uses one line format across the whole system so C# only parses one thing: `UAC|LOG|text`, `UAC|PROGRESS|done/total|label`, `UAC|DONE|code`. `ue_export.py` (inside the engine), `convert.py --porcelain`, and `ProcessRunner.cs` all speak it.
- **Config.** `config.json` is git-ignored (holds absolute per-user paths). On first run it is copied from [config.default.json](config.default.json); missing keys are back-filled from the default on every load, so adding a new setting means adding it to `config.default.json`. `set_config_value` preserves the existing value's type rather than guessing from the text.
- **i18n ([i18n.py](i18n.py)).** Locale files are **shared** between Python and the Unity C# package and live in `unity/com.uasset.converter/Editor/Locales/`. Format is a flat array of pairs (not a dict) because Unity's `JsonUtility` can't deserialize dicts. Use `t("key", arg=...)`; an unknown key falls back rather than raising, so a broken community translation never costs a user a two-hour export. Any user-facing string needs a key in the locale files — `tests/test_locale_keys.py` verifies keys used from C# exist and that every `{placeholder}` has an argument.

## Testing notes

- Tests fix the language to `en` ([tests/conftest.py](tests/conftest.py)) because golden shaders contain localized comments.
- **Golden shader tests** ([tests/test_shader_golden.py](tests/test_shader_golden.py)) assert byte-for-byte stability against `tests/golden/*.shader`. A refactor must leave output identical; an intentional change is committed by regenerating with `UAC_REGEN=1` so the review diff shows exactly what changed.
- **Smoke test** ([tests/test_pipeline_smoke.py](tests/test_pipeline_smoke.py)) runs `shader_gen.py` then `postprocess.py` end-to-end on a synthetic manifest — this is where inter-layer format mismatches are caught, which the single-generator golden tests can't see.
- Test graphs are built with the helpers in `tests/graphbuilder.py` / `tests/fixtures*.py`.

## Environment expectations

Unreal Engine 5.8 (native exporters, so `.uasset` is never parsed), Python 3.13 (`tkinter` is stdlib), Unity 6000.x or 2022.3 LTS with **URP**. HDRP is not supported: its Lit passes are pipeline-internal and cannot be reproduced by a hand-written HLSL shader, so the converter targets URP only.
