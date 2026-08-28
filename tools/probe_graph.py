"""
Probe: can we read a UMaterial's node graph through the UE Python API?

Runs INSIDE UnrealEditor-Cmd. Read-only. Writes findings to a text report so we
don't have to fish them out of a noisy editor log.
"""
import os
import traceback

import unreal

OUT = os.environ.get("PROBE_OUT", r"C:\Users\StratoCat\Desktop\uasset_converter\logs\probe_report.txt")
TARGETS = [
    "/Game/ModSciInteriors/Materials/M_Base_Solid",
    "/Game/ModSciInteriors/Materials/M_Base_RegUV1",
    "/Game/ModSciInteriors/Materials/M_Base_Trim",
]

lines = []


def emit(s=""):
    lines.append(str(s))
    unreal.log("PROBE| " + str(s))


def try_call(label, fn):
    """Run fn, report what happened, return (ok, value)."""
    try:
        return True, fn()
    except Exception as exc:
        emit("    {} -> FAILED: {}: {}".format(label, type(exc).__name__, exc))
        return False, None


def describe_members(obj, keep):
    """Names on obj matching any substring in keep."""
    found = []
    for name in dir(obj):
        low = name.lower()
        if any(k in low for k in keep):
            found.append(name)
    return found


def probe_material(path):
    emit("=" * 78)
    emit("MATERIAL: " + path)
    emit("=" * 78)

    mat = unreal.load_asset(path)
    if mat is None:
        emit("  load_asset returned None")
        return
    emit("  class: {}".format(type(mat).__name__))

    # --- 1. How do we reach the expression list? -------------------------
    emit("  -- candidate members for reaching expressions --")
    emit("     " + ", ".join(describe_members(mat, ["expression", "editor_only", "material_graph"])))

    expressions = None
    for prop in ("expression_collection", "expressions"):
        ok, val = try_call("get_editor_property('{}')".format(prop),
                           lambda p=prop: mat.get_editor_property(p))
        if ok and val is not None:
            emit("    get_editor_property('{}') -> {}".format(prop, type(val).__name__))
            if prop == "expression_collection":
                ok2, inner = try_call("collection.expressions",
                                      lambda v=val: v.get_editor_property("expressions"))
                if ok2 and inner:
                    expressions = list(inner)
            else:
                expressions = list(val)
            if expressions:
                break

    if expressions is None:
        ok, val = try_call("MaterialEditingLibrary.get_...",
                           lambda: unreal.MaterialEditingLibrary)
        if ok:
            emit("  -- MaterialEditingLibrary members --")
            emit("     " + ", ".join(describe_members(val, ["get_", "expression"])))

    if not expressions:
        emit("  !! COULD NOT REACH EXPRESSION LIST")
        return

    emit("  expression count: {}".format(len(expressions)))

    # --- 2. Can we read each node's type, params and INPUTS? -------------
    by_type = {}
    for ex in expressions:
        by_type.setdefault(type(ex).__name__, 0)
        by_type[type(ex).__name__] += 1
    emit("  node types:")
    for k in sorted(by_type):
        emit("    {:<44} x{}".format(k, by_type[k]))

    emit("  -- per-node input inspection (first 12 nodes) --")
    for ex in expressions[:12]:
        tname = type(ex).__name__
        emit("    [{}]".format(tname))
        # inputs are ExpressionInput-typed editor properties
        input_props = []
        for name in dir(ex):
            if name.startswith("_") or name[0].isupper():
                continue
            try:
                val = ex.get_editor_property(name)
            except Exception:
                continue
            if isinstance(val, unreal.ExpressionInput) or type(val).__name__.endswith("ExpressionInput"):
                input_props.append((name, val))
        if not input_props:
            emit("       (no ExpressionInput properties found)")
        for name, inp in input_props:
            ok, linked = try_call("{}.expression".format(name),
                                  lambda i=inp: i.get_editor_property("expression"))
            oi = None
            try:
                oi = inp.get_editor_property("output_index")
            except Exception:
                pass
            emit("       input '{}' -> {} (output_index={})".format(
                name,
                type(linked).__name__ if linked is not None else "None",
                oi))
        # parameter name, if any
        for pname in ("parameter_name", "texture", "const_a", "const_b", "default_value", "u_tiling", "v_tiling"):
            try:
                v = ex.get_editor_property(pname)
            except Exception:
                continue
            emit("       prop '{}' = {}".format(pname, v))

    # --- 3. Can we read what's connected to the MATERIAL OUTPUTS? --------
    emit("  -- material output connections --")
    emit("     members: " + ", ".join(describe_members(mat, ["base_color", "normal", "metallic", "roughness", "emissive"])))
    for prop in ("base_color", "normal", "metallic", "roughness", "emissive_color", "opacity_mask"):
        ok, val = try_call("mat.{}".format(prop), lambda p=prop: mat.get_editor_property(p))
        if ok:
            linked = None
            try:
                linked = val.get_editor_property("expression")
            except Exception:
                pass
            emit("     {:<16} -> {} (linked={})".format(
                prop, type(val).__name__, type(linked).__name__ if linked else None))

    ok, lib = try_call("MaterialEditingLibrary probe", lambda: unreal.MaterialEditingLibrary)
    if ok:
        names = describe_members(lib, ["property_input", "get_material"])
        emit("     MaterialEditingLibrary relevant: " + ", ".join(names))

    # --- 4. Base property overrides --------------------------------------
    for prop in ("blend_mode", "shading_model", "two_sided", "opacity_mask_clip_value"):
        ok, val = try_call("mat.{}".format(prop), lambda p=prop: mat.get_editor_property(p))
        if ok:
            emit("     {:<24} = {}".format(prop, val))


def main():
    emit("UE version: {}".format(unreal.SystemLibrary.get_engine_version()))
    for path in TARGETS:
        try:
            probe_material(path)
        except Exception:
            emit("UNCAUGHT while probing {}:".format(path))
            emit(traceback.format_exc())

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    unreal.log("PROBE| report written to {}".format(OUT))


main()
