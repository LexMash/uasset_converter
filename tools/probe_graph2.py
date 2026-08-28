"""Пробник 2: читаем граф через MaterialEditingLibrary (правильная дверь)."""
import os, traceback
import unreal

OUT = r"C:\Users\StratoCat\Desktop\uasset_converter\logs\probe_report2.txt"
TARGETS = [
    "/Game/ModSciInteriors/Materials/M_Base_Solid",
    "/Game/ModSciInteriors/Materials/M_Base_RegUV1",
    "/Game/ModSciInteriors/Materials/M_Base_Trim",
]
MEL = unreal.MaterialEditingLibrary
lines = []


def emit(s=""):
    lines.append(str(s))
    unreal.log("PROBE2| " + str(s))


def safe(label, fn):
    try:
        return fn()
    except Exception as e:
        emit("      !! {} -> {}: {}".format(label, type(e).__name__, e))
        return None


def probe(path):
    emit("=" * 76)
    emit("MATERIAL " + path)
    mat = unreal.load_asset(path)

    exprs = safe("get_material_expressions", lambda: MEL.get_material_expressions(mat))
    emit("  get_material_expressions -> {}".format(
        "None" if exprs is None else "{} нод".format(len(exprs))))
    if not exprs:
        return

    # индексируем ноды, чтобы связи печатать по номеру
    idx = {}
    for i, e in enumerate(exprs):
        idx[e] = i

    emit("  --- ноды и их входы ---")
    for i, e in enumerate(exprs[:25]):
        tname = type(e).__name__
        pname = safe("", lambda ex=e: ex.get_editor_property("parameter_name"))
        head = "  [{:>2}] {}{}".format(i, tname, "  param='{}'".format(pname) if pname else "")
        emit(head)

        in_names = safe("input_names", lambda ex=e: MEL.get_material_expression_input_names(ex))
        in_nodes = safe("inputs", lambda ex=e: MEL.get_inputs_for_material_expression(mat, ex))
        in_types = safe("input_types", lambda ex=e: MEL.get_material_expression_input_types(ex))
        emit("        input_names={} input_types={}".format(in_names, in_types))
        if in_nodes is not None:
            for j, node in enumerate(in_nodes):
                nm = in_names[j] if in_names and j < len(in_names) else "?"
                if node is None:
                    emit("        in '{}' = <не подключён>".format(nm))
                else:
                    oname = safe("out_name", lambda ex=e, n=nm: MEL.get_input_node_output_name_for_material_expression(ex, n))
                    emit("        in '{}' <- [{}] {} (out='{}')".format(
                        nm, idx.get(node, "?"), type(node).__name__, oname))
        outs = safe("out_names", lambda ex=e: MEL.get_material_expression_output_names(ex))
        if outs:
            emit("        outputs={}".format(outs))

    emit("  --- выходы материала ---")
    for pname in ["MP_BASE_COLOR", "MP_METALLIC", "MP_SPECULAR", "MP_ROUGHNESS",
                  "MP_EMISSIVE_COLOR", "MP_OPACITY", "MP_OPACITY_MASK", "MP_NORMAL",
                  "MP_AMBIENT_OCCLUSION"]:
        mp = getattr(unreal.MaterialProperty, pname, None)
        if mp is None:
            emit("    {} — нет такого в enum".format(pname))
            continue
        node = safe(pname, lambda m=mp: MEL.get_material_property_input_node(mat, m))
        oname = safe(pname + "_out", lambda m=mp: MEL.get_material_property_input_node_output_name(mat, m))
        emit("    {:<22} <- [{}] {} (out='{}')".format(
            pname, idx.get(node, "-") if node else "-",
            type(node).__name__ if node else "None", oname))

    emit("  --- параметры и дефолты ---")
    for kind, getter, default in [
        ("scalar", MEL.get_scalar_parameter_names, MEL.get_material_default_scalar_parameter_value),
        ("vector", MEL.get_vector_parameter_names, MEL.get_material_default_vector_parameter_value),
        ("texture", MEL.get_texture_parameter_names, MEL.get_material_default_texture_parameter_value),
        ("switch", MEL.get_static_switch_parameter_names, MEL.get_material_default_static_switch_parameter_value),
    ]:
        names = safe(kind + "_names", lambda g=getter: g(mat))
        emit("    {}: {}".format(kind, names))
        for n in (names or [])[:6]:
            v = safe("default", lambda d=default, nn=n: d(mat, nn))
            emit("        {!r} = {}".format(str(n), v))

    emit("  --- свойства материала ---")
    for p in ("blend_mode", "shading_model", "two_sided", "opacity_mask_clip_value",
              "material_domain", "dither_opacity_mask"):
        emit("    {:<24} = {}".format(p, safe(p, lambda pp=p: mat.get_editor_property(pp))))


for t in TARGETS:
    try:
        probe(t)
    except Exception:
        emit("UNCAUGHT " + t)
        emit(traceback.format_exc())

os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding="utf-8").write("\n".join(lines))
unreal.log("PROBE2| отчёт: " + OUT)
