*[Русская версия](README.md) · English*

# Unreal → Unity asset converter

Moves art from an Unreal Engine project into Unity: textures, meshes,
animations and materials. Unreal master materials are transpiled into real
shaders — HLSL or Shader Graph — rather than approximated with hand-picked
values.

It runs two ways: from its own window, or straight from Unity via
**Tools → Uasset Converter**. The interface switches between languages, and
adding one is a single JSON file — see
[docs/LOCALIZATION.md](docs/LOCALIZATION.md).

## Requirements

- **Unreal Engine 5.8** — the converter drives the engine's own exporters, so
  geometry and textures come out at original quality instead of being
  reconstructed by a `.uasset` parser.
- **Python 3.9 or newer** — `tkinter` for the GUI ships with the standard install.
  `pillow` and `numpy` are installed separately (see step 0).
- **Unity 6000.x or 2022.3 LTS**, URP or HDRP. The differences between the two
  pipelines are in [URP and HDRP](#urp-and-hdrp).

The Unreal project must be a **source project**, not a packaged game: the
converter opens it with the editor.

## How to run it

Two halves. First the converter pulls everything out of Unreal into an `output`
folder; then the importer inside Unity pulls `output` into your project. That
folder is the only thing connecting the two.

```
Unreal ──► convert.py ──► output/ ──► the importer in Unity ──► Assets/
           (3 steps)                   (one button)
```

Run the converter from wherever suits you — its own window or from inside
Unity. They do the same work and edit the same `config.json`.

### Step 0. Once

```bash
python -m pip install -r requirements.txt
```

Your target Unity project needs the **URP package** and — this is the part
people miss — an **assigned URP asset** in Project Settings → Graphics and in
Quality. Installing the package does not switch the pipeline on: without an
assigned asset the materials will be created but will render incorrectly.

### Step 1. Conversion (the Unreal side)

```bash
python convert.py
```

A window opens. Work top to bottom:

1. **Paths** — point it at your `.uproject`. The engine folder fills
   itself in from the Windows registry.
2. **Conversion scope** — start with **Test subset**. A full
   `Content` run takes a long time, and a mistake in the material mapping is far
   cheaper to catch on a dozen assets. For **Selected folders**,
   tick what you need in the tree below.
3. **Scan** — a quick file-level count that does not launch
   Unreal. It exists only to tell you the scale of the job.
4. **Everything** — runs all three steps back to back.

The three steps individually — the buttons sit in this same order, left to
right:

| Button | What it does | What it feeds on |
|---|---|---|
| **Export from Unreal** | launches the headless editor, exports textures/meshes/animations, captures material graphs | the Unreal project |
| **Generate shaders** | transpiles master materials into shaders | `output/manifest.json` |
| **Post-process** | repacks masks, assembles the Unity manifest | `manifest.json` + `shaders.json` |

The order is mandatory — each step reads the previous one's output. The separate
buttons matter when you are tuning `config.json`: the material mapping
re-runs through **Post-process** in seconds, with no second trip through Unreal.

**The first run takes 10–20 minutes** — the engine warms its DDC and compiles
shaders. After that it is a matter of minutes. Progress and the log show up in
the window itself, and **Cancel** kills the editor process.

The same thing from a terminal:

```bash
python convert.py --cli --step all --scope test
```

`--step`: `export`, `shaders`, `postprocess`, `all`.
`--scope`: `test` (the subset listed in `config.json`), `selected` (folders from
`scope.include_paths`), `all` (the entire `Content`).

### Step 2. Installing the package into Unity

Once per project. In the converter window press **Add package to Unity…** and
pick your Unity project folder. The converter registers itself in
`Packages/manifest.json`:

```json
"com.uasset.converter": "file:C:/.../uasset_converter/unity/com.uasset.converter"
```

By hand it is the same thing: Package Manager → **Add package from disk** →
`unity/com.uasset.converter/package.json`.

A package rather than files copied into `Assets`: it does not litter the
project, does not end up in somebody else's commits, and updates together with
the converter.

### Running it from Unity

**Tools → Uasset Converter**. The window mirrors the standalone one — paths,
checkboxes, folder selection, the same step buttons — plus one extra,
**Convert and import**, which pulls the result into the project as soon as the
conversion finishes, with no second window and no second button.

The export step goes through the Python embedded in Unreal itself, so there is
nothing to install for it. Shader generation and post-processing, however, are
ordinary scripts and need a system Python 3 with `pillow` and `numpy`. The
window looks for one itself (`PATH`, the `py -3` launcher, the usual install
locations) and, if something is missing, shows the exact install command instead
of failing halfway through.

Log lines and progress arrive in the window as they happen without freezing the
editor: the process is read on background threads and drained in
`EditorApplication.update`. Cancel kills the process.

### Step 3. Import (the Unity side)

Menu **Tools → Uasset Converter → Import**. In the window:

| Field | What to set |
|---|---|
| **Output folder** | the converter's `output` folder — use **Browse…** |
| **Where in the project** | `Assets/UassetConverted` by default |
| **Skeletal avatar** | `Generic` always works; pick `Humanoid` only if you need retargeting |
| **Overwrite files** | leave it on for repeat runs |

Press **Import**. You do not copy anything by hand — the
importer moves the files from `output` into `Assets` itself, preserving the
Unreal folder structure.

From there it works in a strict order, and the order is not cosmetic:

1. copies files and shaders — the materials need something to sit on;
2. configures textures — the materials need correct normal maps and sRGB flags;
3. creates the materials;
4. configures the models and binds the materials to their slots;
5. imports skeletal meshes — this is where the avatars are created;
6. imports the clips — they need the avatars from step 5.

The result: meshes under `Assets/UassetConverted/Meshes/…` already have their
materials on the right slots, with nothing left to fix up in the Inspector.

### Step 4. What to look at afterwards

Open **`output/unsupported.md`**. It is rebuilt on every run and lists
everything that did not come across, or came across only approximately.

Two things need your eyes, because the converter cannot know them: the
**direction of the surface relief** from normal maps (the
`textures.flip_normal_green` flag) and **animation looping** (guessed from the
clip name).

## Interface language

Switched in the header of either window and remembered in `config.json`
(`language`; `auto` means detect from the system). The language affects more
than the UI: `output/unsupported.md` and the comments in generated shaders come
out in it too.

Skip reasons and TODO notes are stored in the manifest as **keys**, not as
finished text. So the report is always in the language selected right now, not
the one the export happened to run in.

## How it works

Layers; each one can be run and fixed on its own.

| File | What it does |
|---|---|
| `convert.py` | Orchestrator and CLI. Also serves the Unity window: `--config-dump`, `--config-set`, `--scan-folders`, `--porcelain`. |
| `gui.py` | The standalone window. Computes nothing — edits the config and launches steps. |
| `i18n.py` | Localisation. The locale files are shared with the Unity package. |
| `ue_export.py` | Runs **inside** Unreal. Exports files, captures material and function graphs. |
| `graph_ir.py` | Unreal graph → a neutral intermediate representation. |
| `backend_hlsl.py` | IR → `.shader`. |
| `shader_gen.py` | Orchestration: manifest → shaders plus `graph_ir.json`. |
| `postprocess.py` | Parameter resolution, channel repacking, the Unity manifest. |
| `unity/com.uasset.converter/` | The UPM package: converter window, importer, Shader Graph builder. |

Why graph traversal and text generation are separated: you cannot get a Shader
Graph out of HLSL strings. The intermediate representation is a list of
operations with known dimensions, and both `.shader` and `.shadergraph` are
built from it. A new node is added once, in `graph_ir.py`, and shows up in both
formats.

### Why it launches the full editor instead of a commandlet

The export originally ran through `-run=pythonscript`. On skeletal meshes that
crashes the engine:

```
Assertion failed: MeshObject [SkinnedMeshComponent.cpp:4987]
```

The skeletal mesh exporter needs a live rendering context, and a commandlet does
not bring one up. So the converter uses `-ExecutePythonScript` with the full
editor plus `-RenderOffScreen`: the RHI is alive, no window appears on screen.

The first run takes 10–20 minutes while the DDC warms up and shaders compile.
That cost is paid once.

### Material transpilation

Unreal master materials are node graphs. What comes across:

- **maths and utilities** — Multiply, Add, Subtract, Divide, Lerp, Clamp, Power,
  Min, Max, Fmod, Step, SmoothStep, If, Dot, Cross, Distance, Length, Normalize,
  Abs, Saturate, Frac, Floor, Ceil, Round, Sign, Sqrt, Sine, Cosine, Tangent,
  Arcsine, Arccosine, Arctangent, Arctangent2, DDX, DDY, exponentials and
  logarithms, ComponentMask, AppendVector, OneMinus, Desaturation,
  ConstantBiasScale, DeriveNormalZ;
- **coordinates** — TextureCoordinate (up to four UV sets), Panner, Rotator,
  CustomRotator, BumpOffset;
- **surface inputs** — VertexColor, normal, world position, object position and
  scale, camera vector, screen position, Time, TwoSidedSign, Fresnel, SphereMask;
- **parameters** — Scalar, Vector, Texture, StaticSwitch (which becomes a
  `shader_feature` rather than a runtime branch);
- **any material function** — its graph is exported from Unreal and inlined.
  There is no hard-coded list of functions; `CheapContrast` and
  `BlendAngleCorrectedNormals` keep hand-written implementations only because
  they are shorter and cheaper than the expanded graph;
- **the Custom node** — its HLSL is carried over as-is rather than discarded;
- **wires** — Reroute and named reroutes pass straight through.

An unknown node is never invented: it passes its first input through, leaves a
`// TODO` in the code right there, and a line in `output/unsupported.md`.

### Shader output format

`shader_gen.output` in the config, and a dropdown in both windows:

| Value | What you get |
|---|---|
| `hlsl` | `output/Shaders/*.shader` — self-contained HLSL. |
| `shadergraph` | `output/Shaders/*.shadergraph` — a graph you can open and edit. |
| `both` | Both. |

`output/graph_ir.json` is always written regardless of the choice: it costs
nothing, and it lets you switch format later without re-running the slow Unreal
export.

The `.shadergraph` itself is built by the C# side **inside Unity**, not by
Python. The reason is that the format is internal and tied to the Shader Graph
package version: the only reliable way to get a file that opens is to ask the
package to write it. Operations that have a ready-made Shader Graph node become
that node; the rest become a Custom Function carrying the same HLSL that would
have gone into the `.shader`. Coverage is therefore identical between the two
formats — there is no material that ports to HLSL but not to a graph.

If the Shader Graph package is not installed, graph building is simply skipped
and the HLSL shaders import as usual.

The graph is read through `unreal.MaterialEditingLibrary` —
`get_material_expressions`, `get_inputs_for_material_expression`,
`get_material_property_input_node`. It is *not* reachable through
`get_editor_property`: the `Expressions` property is protected.

What this buys you: `MaterialInstanceConstant` parameters land on their own
shader **by name, with no heuristics**, and the things the masters were written
for survive — detail normals, roughness contrast, per-instance tiling.

For example, `MI_Floor_A` carries the same parameters under the same names in
Unreal and in Unity after conversion:

```
_Diffuse, _Normal, _Roughness            (textures)
"Base Rough Min" = 0.15                  (0.3 in the master)
"Base Rough Max" = 0.6
"Metal" = 1.0,  "Color" = (0.36, 0.36, 0.36)
```

Materials whose master was not transpiled are built on the stock
`Universal Render Pipeline/Lit` using the name-matching table in `config.json`.

### Channel repacking

Unreal stores **roughness**; Unity expects **smoothness**. That is an inversion,
not a rename. URP/Lit additionally wants a `_MetallicGlossMap` with metallic in
RGB and smoothness in alpha. No such map exists in Unreal, so `postprocess.py`
builds it — from separate Rough/Metal maps, from a packed MRA/ORM map, or from
scalar values.

Packed-map layouts live in `PACKED_LAYOUTS`: `MRA` is Metallic / Roughness / AO,
`ORM` is Occlusion / Roughness / Metallic, and so on.

The transpiled shaders do not need any of this: they read roughness directly,
exactly as Unreal does.

### Normal maps

Unreal stores normal maps in the DirectX convention (green channel down), Unity
expects OpenGL (green up), so the green channel is inverted. Turn it off with
`textures.flip_normal_green` in the config — and **check the relief direction on
the first lit object you look at**.

Normal maps are identified by the `TC_NORMALMAP` compression setting coming from
Unreal itself, not by filename: names like `T_Manny_01_BN` match no sensible
pattern.

### Why model materials are imported rather than disabled

The importer asks for `materialImportMode = ImportViaMaterialDescription` and
`materialLocation = InPrefab` — in the Inspector these read as "Import via
MaterialDescription" and "Use Embedded Materials".

That looks backwards: our materials are already built from Unreal data, so why
let the importer touch the FBX materials at all? Because with
`materialImportMode = None` Unity does not look at material names in the file,
and **the remap table stops working** — the entries are there in the `.meta`,
but the mesh arrives with the default grey material.

The failure is easy to miss: materials are created, shaders compile, `AddRemap`
did its job, every check is green. That is why `UassetBatchVerify` has a
dedicated `CheckMeshBindings` step — it loads the imported model and confirms
that its renderers carry our materials specifically.

## Configuration

Everything lives in `config.json`. It is not in the repository — it holds
absolute paths for one machine; on first run it is created by copying
`config.default.json`. Keys added in newer versions are filled into an existing
config automatically, so updating does not break your settings.

- `scope.test_subset` — assets for a test run. Dependencies are pulled in
  automatically, so a material arrives with its textures. **Animations must be
  listed explicitly** — dependencies point downward, and a clip references a
  skeleton rather than the other way round.
- `language` — interface language code, or `auto`.
- `pipeline` — `urp` or `hdrp`.
- `paths.python` — the interpreter for the Unity window. Empty means find one.
- `shader_gen.output` — `hlsl`, `shadergraph` or `both`.
- `shader_gen.roots` — which paths to transpile into shaders.
- `shader_gen.skip` — masters deliberately left out (decals, particles, UI),
  each with a reason in `skip_reason`.
- `material_mapping` — the name-matching table for the fallback path.
- `animations.loop_patterns` — the clip-looping heuristic.

## Moving it to another Unreal project

The code itself is not tied to any project: it walks the Asset Registry and
dispatches on asset class, with no hardcoded names in the logic. Everything
project-specific lives in `config.json`, and there are four things to change:

| Key | What to put there |
|---|---|
| `paths.ue_project` | path to the new `.uproject` (GUI: **Browse…**) |
| `paths.output_dir` | where to write results |
| `scope.include_paths` | folders of the new project (GUI: the tree with checkboxes) |
| `shader_gen.roots` | which paths to transpile into shaders |

`scope.test_subset` and `shader_gen.skip` also point at ModSci assets — for
another project either rewrite them or simply skip the test-subset mode.
`material_mapping` does not have to change: its rules key off parameter names,
not off the project.

### What happens to somebody else's master materials

The transpiler understands this set of nodes:

```
Add Subtract Multiply Divide LinearInterpolate Clamp Saturate Abs OneMinus Power
Desaturation AppendVector ComponentMask Constant Constant2/3/4Vector
ScalarParameter VectorParameter StaticSwitchParameter StaticBoolParameter
TextureSample TextureSampleParameter2D TextureObjectParameter
TextureCoordinate Panner MaterialFunctionCall
```

Two engine functions are ported: `CheapContrast_RGB` and
`BlendAngleCorrectedNormals`.

A node outside that list is **not invented**. The transpiler passes through the
node's first connected input, leaves a `// TODO` beside it, and records the
material and node index in `output/unsupported.md`. The shader still compiles,
but the result is approximate — nothing is silently substituted, and nothing is
quietly assumed to be fine either.

Nodes that are missing and that do turn up in other people's packs: `Fresnel`,
`VertexColor`, `WorldPosition`, `Time`, `If`, `Min`/`Max`, `Sine`/`Cosine`,
`Dot`, `Normalize`, `CustomExpression`, plus any `MaterialFunctionCall` other
than the two known ones. Each is added to `shader_gen.py` as a single
`node_<Name>` method in `graph_ir.py` — widening coverage is cheap, and it
lands in both output formats at once.

### URP and HDRP

| | URP | HDRP |
|---|---|---|
| Transpiled shaders | `.shader` and/or Shader Graph | Shader Graph only |
| Fallback materials | `Universal Render Pipeline/Lit` | `HDRP/Lit` |
| Mask map | `_MetallicGlossMap`: RGB = metallic, A = smoothness | `_MaskMap`: R = metallic, G = occlusion, B = detail, A = smoothness |
| Occlusion | its own `_OcclusionMap` | inside `_MaskMap`; there is no separate slot |

There is no hand-written `.shader` for HDRP and there will not be one: the set
and order of its passes are internal to the pipeline, and Unity offers no
supported way to write such a shader by hand. So with `pipeline: hdrp` the
shader step writes only the IR and says so in the log, and the graph is built in
Unity against `HDTarget`.

The `.shader` files themselves are self-contained: apart from the URP package
they depend on nothing, and they move into any URP project by plain copying
alongside the materials.

## What does NOT come across

Read `output/unsupported.md` after every run — it is regenerated each time and
lists everything that did not port, or ported only approximately.

Fundamentally not portable:

- **Blueprints and Anim Blueprints** — that is logic; it has to be rewritten.
- **Niagara** — Unity has no direct equivalent for the particle system.
- **Levels** — the art they are built from ports, the layout does not.
- **Decals** — in URP these are a separate mechanism (Decal Projector).
- **Physics Assets** — colliders are set up on the Unity side.

Needs checking by eye:

- **Animation looping** — Unreal does not store this flag; it is guessed from
  the clip name.
- **Normal map direction** — see `flip_normal_green` above.
- **Humanoid avatars** — `Generic` is the default and always works. `Humanoid`
  can be switched on in the GUI, but Unity may fail to auto-map UE5 bones.
- **The Specular parameter** — Unity's metallic workflow has no such input, so
  the Unreal value is dropped.

## Verification

### Python-side tests

```bash
python -m pytest tests/
```

What they catch. `tests/golden/*.shader` are reference copies of generated
shaders: the point is not that they are "correct" but that they **do not
change**. A deliberate change is recorded by regenerating them:

```bash
UAC_REGEN=1 python -m pytest tests/test_shader_golden.py
```

and the review diff then shows exactly what changed in the shaders. The rest:
`test_nodes.py` — every new node really does port and leaves no TODO;
`test_pipelines.py` — the URP and HDRP mask layouts are not mixed up;
`test_pipeline_smoke.py` — the steps fit together, in both languages;
`test_locale_keys.py` — every `Loc.T` key used by the C# side exists and every
placeholder in it is given an argument.

Locales are checked separately:

```bash
python tools/lint_locales.py
```

### Checks inside Unity

Two different checks, and the first one alone is not enough.

**1. Batch import** — copying, texture settings, materials, models, clips:

```bash
"C:\Program Files\Unity\Hub\Editor\6000.4.1f1\Editor\Unity.exe" -batchmode -quit -projectPath "<project>" -executeMethod UassetImporter.UassetBatchVerify.Run -uassetOutput "<...>\output" -uassetLang en -logFile verify.log -nographics
```

**2. Render check** — switches URP on, lines up spheres carrying every material
and renders a frame to PNG:

```bash
"C:\Program Files\Unity\Hub\Editor\6000.4.1f1\Editor\Unity.exe" -batchmode -quit -projectPath "<project>" -executeMethod UassetImporter.Verify.UassetRenderCheck.Run -uassetShot shot.png -uassetLang en -logFile render.log
```

Its files live in the package sample (`Samples~/RenderCheck`) and are
deliberately not part of the importer: they are hard-wired to URP, whereas the
importer has to work in an HDRP project too. Install it via Package Manager →
the package → Samples → Import.

The explanatory text of both checks is localised; the machine-readable markers
are not. The `RESULT: OK` / `RESULT: PROBLEMS FOUND` line, the
`[UASSET-VERIFY]` prefix and the exit code stay identical in every language, so
that CI can still grep the log.

The language is set with `-uassetLang ru`. Without it the one selected in the
converter window is used; on a clean CI machine, the system language, falling
back to English.

### Why the first check is not enough on its own

Unity compiles HLSL **lazily** — not when the shader is imported, but when a
particular variant is first needed. So right after import,
`ShaderUtil.GetShaderMessages` returns an empty list even for a shader that is
definitely broken, and the first check honestly reports "no errors".

That is exactly what happened during development: the batch check reported 0
errors across all five shaders, and then half the spheres rendered magenta. The
cause only showed up in the full Unity log — `float3(x)` from a single scalar.
That is a GLSL rule; in HLSL a vector constructor needs exactly as many
arguments as it has components, so it has to be `float3(x, x, x)`.

In both logs, search for `[UASSET-VERIFY]`. The last line is either
`RESULT: OK` or `RESULT: PROBLEMS FOUND` (result: problems
found); the exit code is non-zero when something broke. If a sphere is magenta
but the result says `OK`, search the Unity log for `Shader error in`.

## Logs and artifacts

- `logs/ue_export.log` — full output of the Unreal editor.
- `output/manifest.json` — raw data from Unreal.
- `output/shaders.json` — which master became which shader.
- `output/unity_manifest.json` — what the C# importer reads.
- `output/unsupported.md` — the report on what did not port.
