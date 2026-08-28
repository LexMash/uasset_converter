"""
Слой 1 конвертера: выполняется ВНУТРИ UnrealEditor-Cmd.

Экспортирует текстуры / меши / анимации родными экспортёрами движка и
складывает описание материалов и графов мастеров в JSON.

Проект только читается: ничего не создаётся, не сохраняется, не помечается
грязным. Конфиг приходит через переменную окружения UASSET_CONV_CONFIG.
"""
import json
import os
import re
import sys
import traceback

import unreal

# Скрипт запускает UnrealEditor-Cmd, и своей папки в sys.path может не оказаться.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import level_export
from i18n import t, set_language


# ----------------------------------------------------------------------------
# Инфраструктура
# ----------------------------------------------------------------------------

def load_config():
    path = os.environ.get("UASSET_CONV_CONFIG")
    if not path or not os.path.isfile(path):
        raise RuntimeError("UASSET_CONV_CONFIG does not point at an existing file: %r" % path)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


CONFIG = load_config()
set_language(CONFIG.get("language") or "auto")
OUT_DIR = CONFIG["paths"]["output_dir"].replace("\\", "/")

_report = {
    "textures": [],
    "static_meshes": [],
    "skeletal_meshes": [],
    "animations": [],
    "materials": [],
    "material_graphs": [],
    "material_functions": [],
    "levels": [],
    "skipped": [],
    "errors": [],
}

# Собранные уровни: [(level_dict, относительный путь файла), ...]. Пишутся на
# диск отдельными файлами в конце, а в манифест уходит только их список.
_levels = []

_progress_total = 0
_progress_done = 0

# Одна и та же функция вызывается из десятков мастеров — выгружаем её один раз.
_dumped_functions = set()


def log(msg):
    # Display-уровень не всегда доходит до stdout в headless-режиме, а сводку и
    # прогресс терять нельзя — поэтому Warning.
    unreal.log_warning("UAC| %s" % msg)


def progress(label):
    global _progress_done
    _progress_done += 1
    unreal.log_warning("UAC|PROGRESS|%d/%d|%s" % (_progress_done, _progress_total, label))


def skip(ue_path, reason_key, **args):
    """
    Причина пропуска кладётся в манифест КЛЮЧОМ, а не готовой строкой.

    Отчёт unsupported.md собирает постобработка, и собирает его тем языком,
    который выбран сейчас, — а не тем, на котором когда-то шёл экспорт.
    Готовый текст пишем рядом только для читаемости самого манифеста.
    """
    _report["skipped"].append({
        "ue_path": ue_path,
        "reason_key": reason_key,
        "reason_args": args,
        "reason": t(reason_key, **args),
    })


def error(ue_path, exc):
    _report["errors"].append({
        "ue_path": ue_path,
        "error": "%s: %s" % (type(exc).__name__, exc),
        "traceback": traceback.format_exc(),
    })
    unreal.log_error("UAC| %s" % t("export.error_on", path=ue_path, error=exc))


def prop(obj, name, default=None):
    """get_editor_property, который не роняет скрипт на отсутствующем свойстве."""
    try:
        return obj.get_editor_property(name)
    except Exception:
        return default


MEL = unreal.MaterialEditingLibrary


def jsonify(value):
    """UE-типы -> то, что переживёт json.dump."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, unreal.LinearColor):
        return [value.r, value.g, value.b, value.a]
    if isinstance(value, unreal.Vector):
        return [value.x, value.y, value.z]
    if isinstance(value, unreal.Vector2D):
        return [value.x, value.y]
    if isinstance(value, unreal.Name):
        return str(value)
    if isinstance(value, unreal.Object):
        return asset_path_of(value)
    if isinstance(value, (list, tuple, unreal.Array)):
        return [jsonify(v) for v in value]
    if hasattr(value, "__int__") and "." in str(value):
        return enum_name(value)          # enum
    return str(value)


def enum_name(value):
    """
    Короткое имя элемента перечисления.
    str() у enum в UE выглядит по-разному между версиями — то
    'TextureCompressionSettings.TC_DEFAULT', то '<TextureCompressionSettings.TC_DEFAULT: 0>',
    поэтому вытаскиваем имя регуляркой, а не split('.').
    """
    if value is None:
        return None
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*-?\d+)?\s*>?\s*$", str(value))
    return match.group(1) if match else str(value)


def out_path(ue_path, ext, subdir):
    """/Game/Foo/Bar/T_Baz -> <output>/<subdir>/Foo/Bar/T_Baz.<ext>"""
    rel = ue_path[len("/Game/"):] if ue_path.startswith("/Game/") else ue_path.lstrip("/")
    full = "%s/%s/%s.%s" % (OUT_DIR, subdir, rel, ext)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    return full


def rel_to_output(abs_path):
    return os.path.relpath(abs_path, OUT_DIR).replace("\\", "/")


def run_export_task(asset, filename, exporter, options=None):
    """Общая обвязка вокруг UAssetExportTask. True, если файл реально появился."""
    task = unreal.AssetExportTask()
    task.set_editor_property("object", asset)
    task.set_editor_property("filename", filename)
    task.set_editor_property("exporter", exporter)
    task.set_editor_property("automated", True)
    task.set_editor_property("prompt", False)
    task.set_editor_property("replace_identical", True)
    task.set_editor_property("write_empty_files", False)
    if options is not None:
        task.set_editor_property("options", options)
    unreal.Exporter.run_asset_export_task(task)
    return os.path.isfile(filename) and os.path.getsize(filename) > 0


# ----------------------------------------------------------------------------
# Текстуры
# ----------------------------------------------------------------------------

# Форматы, которые в 8 бит на канал не влезут — их гоним в EXR, а не в PNG.
_HDR_COMPRESSIONS = {"TC_HDR", "TC_HDR_COMPRESSED", "TC_HALF_FLOAT", "TC_SINGLE_FLOAT", "TC_HDR_F32"}


def export_texture(asset, ue_path):
    compression = enum_name(prop(asset, "compression_settings"))
    is_hdr = compression in _HDR_COMPRESSIONS

    if is_hdr:
        ext = CONFIG["textures"].get("hdr_format", "exr")
        exporter = unreal.TextureExporterEXR()
    else:
        ext = CONFIG["textures"].get("format", "png")
        exporter = unreal.TextureExporterPNG() if ext == "png" else unreal.TextureExporterTGA()

    filename = out_path(ue_path, ext, "Textures")
    ok = run_export_task(asset, filename, exporter)

    # PNG-экспортёр отказывается от части форматов. Пробуем TGA, он всеяден.
    if not ok and not is_hdr and ext == "png":
        filename = out_path(ue_path, "tga", "Textures")
        ok = run_export_task(asset, filename, unreal.TextureExporterTGA())
        ext = "tga"

    if not ok:
        skip(ue_path, "export.skip.texture_exporter", format=compression)
        return

    # Source art мог быть вырезан из ассета — тогда экспорт идёт из
    # платформенных данных и качество хуже оригинала. Об этом надо сказать.
    source = prop(asset, "source")
    has_source = True
    if source is not None:
        valid = prop(source, "is_valid")
        if valid is False:
            has_source = False

    _report["textures"].append({
        "ue_path": ue_path,
        "file": rel_to_output(filename),
        "format": ext,
        "compression": compression,
        "srgb": bool(prop(asset, "srgb", False)),
        "address_x": enum_name(prop(asset, "address_x")),
        "address_y": enum_name(prop(asset, "address_y")),
        # Ориентировочный размер. Точный берётся постобработкой из самого файла:
        # blueprint_get_size_* отдаёт размер загруженного мипа, а не оригинала.
        "width_hint": int(asset.blueprint_get_size_x()),
        "height_hint": int(asset.blueprint_get_size_y()),
        "is_normal_map": compression == "TC_NORMALMAP",
        "is_hdr": is_hdr,
        "flip_green": compression == "TC_NORMALMAP" and CONFIG["textures"].get("flip_normal_green", True),
        "has_source_art": has_source,
    })
    if not has_source:
        skip(ue_path, "export.skip.no_source_art")


# ----------------------------------------------------------------------------
# Меши
# ----------------------------------------------------------------------------

def make_fbx_options(preview_mesh=False):
    opt = unreal.FbxExportOption()
    mesh_cfg = CONFIG["meshes"]
    opt.set_editor_property("ascii", mesh_cfg.get("ascii_fbx", False))
    opt.set_editor_property("vertex_color", mesh_cfg.get("export_vertex_color", True))
    opt.set_editor_property("level_of_detail", mesh_cfg.get("export_lods", False))
    opt.set_editor_property("collision", mesh_cfg.get("export_collision", False))
    opt.set_editor_property("export_morph_targets", mesh_cfg.get("export_morph_targets", True))
    opt.set_editor_property("export_preview_mesh", preview_mesh)
    opt.set_editor_property("force_front_x_axis", False)
    try:
        opt.set_editor_property("bake_material_inputs", unreal.FbxMaterialBakeMode.DISABLED)
    except Exception:
        pass  # имя enum'а плавало между версиями; запекание и так выключено по умолчанию
    return opt


def material_slots(asset):
    """Слоты в порядке индексов — по ним Unity ремапит материалы на модель."""
    slots = []
    materials = prop(asset, "materials", None) or prop(asset, "static_materials", None) or []
    for index, entry in enumerate(materials):
        iface = prop(entry, "material_interface")
        slot_name = prop(entry, "material_slot_name")
        slots.append({
            "index": index,
            "slot": str(slot_name) if slot_name else "Material_%d" % index,
            "material": iface.get_path_name().split(".")[0] if iface else None,
        })
    return slots


def export_static_mesh(asset, ue_path):
    filename = out_path(ue_path, "fbx", "Meshes")
    if not run_export_task(asset, filename, unreal.StaticMeshExporterFBX(), make_fbx_options()):
        skip(ue_path, "export.skip.static_mesh_exporter")
        return
    _report["static_meshes"].append({
        "ue_path": ue_path,
        "file": rel_to_output(filename),
        "material_slots": material_slots(asset),
        "lod_count": prop(asset, "lod_count", None),
    })


def export_skeletal_mesh(asset, ue_path):
    filename = out_path(ue_path, "fbx", "SkeletalMeshes")
    if not run_export_task(asset, filename, unreal.SkeletalMeshExporterFBX(), make_fbx_options()):
        skip(ue_path, "export.skip.skeletal_mesh_exporter")
        return
    skeleton = prop(asset, "skeleton")
    _report["skeletal_meshes"].append({
        "ue_path": ue_path,
        "file": rel_to_output(filename),
        "skeleton": skeleton.get_path_name().split(".")[0] if skeleton else None,
        "material_slots": material_slots(asset),
    })


def export_animation(asset, ue_path):
    filename = out_path(ue_path, "fbx", "Animations")

    # Меш в клип не кладём — скелетные меши экспортируются отдельно.
    fbx_opt = make_fbx_options(preview_mesh=False)

    task = unreal.AssetExportTask()
    task.set_editor_property("object", asset)
    task.set_editor_property("filename", filename)
    task.set_editor_property("exporter", unreal.AnimSequenceExporterFBX())
    task.set_editor_property("automated", True)
    task.set_editor_property("prompt", False)
    task.set_editor_property("replace_identical", True)
    task.set_editor_property("write_empty_files", False)
    task.set_editor_property("options", fbx_opt)
    unreal.Exporter.run_asset_export_task(task)

    if not (os.path.isfile(filename) and os.path.getsize(filename) > 0):
        skip(ue_path, "export.skip.anim_exporter")
        return

    skeleton = prop(asset, "skeleton")
    _report["animations"].append({
        "ue_path": ue_path,
        "file": rel_to_output(filename),
        "skeleton": skeleton.get_path_name().split(".")[0] if skeleton else None,
        "duration": prop(asset, "sequence_length", None),
        "frame_rate": str(prop(asset, "target_frame_rate", "")) or None,
        "number_of_frames": prop(asset, "number_of_sampled_frames", None),
        "root_motion": bool(prop(asset, "enable_root_motion", False)),
    })


# ----------------------------------------------------------------------------
# Материалы: инстансы
# ----------------------------------------------------------------------------

def linear_color_to_list(color):
    if color is None:
        return None
    return [color.r, color.g, color.b, color.a]


def asset_path_of(obj):
    return obj.get_path_name().split(".")[0] if obj else None


def collect_instance_overrides(mi):
    """Только то, что реально переопределено в этом инстансе."""
    textures, scalars, vectors, switches = {}, {}, {}, {}

    for entry in (prop(mi, "texture_parameter_values", None) or []):
        info = prop(entry, "parameter_info")
        name = str(prop(info, "name", "")) if info else ""
        if name:
            textures[name] = asset_path_of(prop(entry, "parameter_value"))

    for entry in (prop(mi, "scalar_parameter_values", None) or []):
        info = prop(entry, "parameter_info")
        name = str(prop(info, "name", "")) if info else ""
        if name:
            scalars[name] = prop(entry, "parameter_value")

    for entry in (prop(mi, "vector_parameter_values", None) or []):
        info = prop(entry, "parameter_info")
        name = str(prop(info, "name", "")) if info else ""
        if name:
            vectors[name] = linear_color_to_list(prop(entry, "parameter_value"))

    static_params = prop(mi, "static_parameters", None)
    if static_params is not None:
        for entry in (prop(static_params, "static_switch_parameters", None) or []):
            info = prop(entry, "parameter_info")
            name = str(prop(info, "name", "")) if info else ""
            if name:
                switches[name] = bool(prop(entry, "value", False))

    return textures, scalars, vectors, switches


def collect_material_defaults(mat):
    """Дефолты параметров мастер-материала: имя параметра -> значение."""
    result = {"scalars": {}, "vectors": {}, "textures": {}, "switches": {}}
    for key, names_fn, value_fn in (
        ("scalars", MEL.get_scalar_parameter_names, MEL.get_material_default_scalar_parameter_value),
        ("vectors", MEL.get_vector_parameter_names, MEL.get_material_default_vector_parameter_value),
        ("textures", MEL.get_texture_parameter_names, MEL.get_material_default_texture_parameter_value),
        ("switches", MEL.get_static_switch_parameter_names,
         MEL.get_material_default_static_switch_parameter_value),
    ):
        try:
            names = names_fn(mat) or []
        except Exception:
            continue
        for name in names:
            try:
                result[key][str(name)] = jsonify(value_fn(mat, name))
            except Exception:
                result[key][str(name)] = None
    return result


def export_material_instance(mi, ue_path):
    parent = prop(mi, "parent")
    textures, scalars, vectors, switches = collect_instance_overrides(mi)

    overrides = prop(mi, "base_property_overrides")
    base_over = {}
    if overrides is not None:
        for flag, value in (("override_blend_mode", "blend_mode"),
                            ("override_shading_model", "shading_model"),
                            ("override_two_sided", "two_sided"),
                            ("override_opacity_mask_clip_value", "opacity_mask_clip_value")):
            if prop(overrides, flag, False):
                raw = prop(overrides, value)
                base_over[value] = enum_name(raw) if value in ("blend_mode", "shading_model") else raw

    # У мастер-материала нет массивов *_parameter_values — его значения живут
    # в графе. Забираем их всегда, а не только для транспилируемых паков:
    # без них fallback-путь остаётся без дефолтов.
    defaults = {}
    if isinstance(mi, unreal.Material):
        defaults = collect_material_defaults(mi)

    _report["materials"].append({
        "ue_path": ue_path,
        "class": type(mi).__name__,
        "parent": asset_path_of(parent),
        "defaults": defaults,
        "textures": textures,
        "scalars": scalars,
        "vectors": vectors,
        "switches": switches,
        "base_property_overrides": base_over,
        # GetUsedTextures объявлена на UMaterial — на инстансе она падает.
        "used_textures": ([asset_path_of(t) for t in (
            unreal.MaterialEditingLibrary.get_used_textures(mi) or [])]
            if isinstance(mi, unreal.Material) else []),
    })


# ----------------------------------------------------------------------------
# Материалы: граф мастера
# ----------------------------------------------------------------------------

# Свойства нод, которые нужны транспилятору. Опрашиваем весь список на каждой
# ноде и запоминаем те, что реально есть — так дампер не привязан к конкретным
# классам нод и не сломается на незнакомом типе.
NODE_PROPS = [
    "parameter_name", "default_value", "group",
    "texture", "sampler_type", "const_coordinate",
    "coordinate_index", "u_tiling", "v_tiling", "un_mirror_u", "un_mirror_v",
    "const_a", "const_b", "const_alpha", "const_exponent",
    "min_default", "max_default", "clamp_mode",
    "speed_x", "speed_y", "fractional_part",
    "r", "g", "b", "a",
    "constant", "luminance_factors",
    "material_function", "value", "desc",
    # Пришли вместе с расширенным покрытием нод.
    "code", "output_type", "description",
    "input_name", "input_type", "output_name", "sort_priority",
    "center_x", "center_y", "speed",
    "bias", "scale", "exponent", "base_reflect_fraction",
    "attenuation_radius", "hardness_percent", "height_ratio",
    "const_min", "const_max", "const_value",
    "const_a_greater_than_b", "const_a_equals_b", "const_a_less_than_b",
    "equals_threshold",
]

# Выходы материала, которые вообще имеют смысл для Unity Lit.
MATERIAL_OUTPUTS = [
    "MP_BASE_COLOR", "MP_METALLIC", "MP_SPECULAR", "MP_ROUGHNESS",
    "MP_ANISOTROPY", "MP_EMISSIVE_COLOR", "MP_OPACITY", "MP_OPACITY_MASK",
    "MP_NORMAL", "MP_TANGENT", "MP_AMBIENT_OCCLUSION", "MP_REFRACTION",
]


def output_name_for_input(expr, input_node, input_index):
    """
    Какой именно выход входной ноды подключён (RGB / R / A / ...).
    Сигнатура функции движка менялась, поэтому пробуем варианты и честно
    возвращаем None, если ни один не подошёл — врать про канал нельзя.
    """
    for args in ((expr, input_node), (input_node, expr), (expr, input_index)):
        try:
            name = MEL.get_input_node_output_name_for_material_expression(*args)
            if name is not None:
                return str(name)
        except Exception:
            continue
    return None


def function_expressions(function):
    """
    Выражения внутри UMaterialFunction.

    Свойство, в котором они лежат, переезжало между версиями движка: в новых
    это expression_collection.expressions, в старых — просто expressions.
    Пробуем оба и честно возвращаем пустой список, если не вышло ни одного.
    """
    collection = prop(function, "expression_collection")
    if collection is not None:
        found = prop(collection, "expressions")
        if found:
            return list(found)
    found = prop(function, "expressions")
    return list(found) if found else []


def dump_expressions(expressions, owner=None):
    """
    Ноды и связи по списку выражений.

    owner — материал, которому они принадлежат; для функции его нет, и связи
    приходится читать напрямую из входов выражения: API, работающее по
    материалу, для функций не годится.
    """
    index_of = dict((expr, i) for i, expr in enumerate(expressions))

    nodes = []
    for i, expr in enumerate(expressions):
        type_name = type(expr).__name__
        short = type_name.replace("MaterialExpression", "", 1)

        props = {}
        for name in NODE_PROPS:
            try:
                value = expr.get_editor_property(name)
            except Exception:
                continue
            props[name] = jsonify(value)

        input_names = [str(n) for n in (MEL.get_material_expression_input_names(expr) or [])]
        if owner is not None:
            input_nodes = list(MEL.get_inputs_for_material_expression(owner, expr) or [])
        else:
            input_nodes = function_input_nodes(expr, len(input_names))

        inputs = []
        for j, name in enumerate(input_names):
            node = input_nodes[j] if j < len(input_nodes) else None
            inputs.append({
                "name": name if name and name != "None" else "",
                "index": j,
                "from": index_of.get(node) if node is not None else None,
                "from_output": output_name_for_input(expr, node, j) if node is not None else None,
            })

        nodes.append({
            "index": i,
            "type": short,
            "props": props,
            "inputs": inputs,
            "outputs": [str(o) for o in (MEL.get_material_expression_output_names(expr) or [])],
        })

    return nodes, index_of


def function_input_nodes(expr, count):
    """
    Источники входов выражения внутри функции.

    Внутри UMaterialFunction нет материала, по которому движок умеет отдать
    связи, поэтому лезем в сами структуры входов. Имена полей у разных нод
    разные, поэтому перебираем те, что реально встречаются.
    """
    candidates = ("input", "a", "b", "alpha", "coordinates", "coordinate",
                  "base", "exponent", "min", "max", "value", "in_",
                  "base_normal", "additional_normal", "fraction",
                  "a_greater_than_b", "a_equals_b", "a_less_than_b",
                  "true", "false", "normal", "radius", "hardness", "height")
    found = []
    for name in candidates:
        entry = prop(expr, name)
        if entry is None:
            continue
        source = prop(entry, "expression")
        if source is not None:
            found.append(source)
    while len(found) < count:
        found.append(None)
    return found[:count]


def dump_material_function(function, ue_path, seen):
    """
    Граф функции материала — чтобы транспилятор мог развернуть её инлайном,
    а не оставлять TODO. Рекурсия по вложенным вызовам с защитой от циклов:
    функция, вызывающая саму себя, встречается редко, но повесить экспорт
    она не должна.
    """
    if ue_path in seen:
        return
    seen.add(ue_path)

    expressions = function_expressions(function)
    if not expressions:
        skip(ue_path, "export.skip.function_unreadable")
        return

    nodes, _index_of = dump_expressions(expressions, owner=None)
    _report["material_functions"].append({
        "ue_path": ue_path,
        "node_count": len(nodes),
        "nodes": nodes,
    })

    for expr in expressions:
        nested = prop(expr, "material_function")
        if nested is not None:
            dump_material_function(nested, asset_path_of(nested), seen)


def collect_called_functions(expressions, seen):
    """Все функции, вызванные из этого набора выражений, включая вложенные."""
    for expr in expressions:
        function = prop(expr, "material_function")
        if function is not None:
            dump_material_function(function, asset_path_of(function), seen)


def dump_material_graph(mat, ue_path):
    """Полный дамп графа мастер-материала: ноды, связи, выходы, дефолты."""
    expressions = list(MEL.get_material_expressions(mat) or [])
    nodes, index_of = dump_expressions(expressions, owner=mat)
    collect_called_functions(expressions, _dumped_functions)

    outputs = {}
    for prop_name in MATERIAL_OUTPUTS:
        enum_value = getattr(unreal.MaterialProperty, prop_name, None)
        if enum_value is None:
            continue
        try:
            node = MEL.get_material_property_input_node(mat, enum_value)
        except Exception:
            continue
        if node is None:
            continue
        try:
            out_name = MEL.get_material_property_input_node_output_name(mat, enum_value)
        except Exception:
            out_name = None
        outputs[prop_name] = {
            "from": index_of.get(node),
            "from_output": str(out_name) if out_name else None,
        }

    defaults = {"scalars": {}, "vectors": {}, "textures": {}, "switches": {}}
    for key, names_fn, value_fn in (
        ("scalars", MEL.get_scalar_parameter_names, MEL.get_material_default_scalar_parameter_value),
        ("vectors", MEL.get_vector_parameter_names, MEL.get_material_default_vector_parameter_value),
        ("textures", MEL.get_texture_parameter_names, MEL.get_material_default_texture_parameter_value),
        ("switches", MEL.get_static_switch_parameter_names, MEL.get_material_default_static_switch_parameter_value),
    ):
        try:
            names = names_fn(mat) or []
        except Exception:
            continue
        for name in names:
            try:
                defaults[key][str(name)] = jsonify(value_fn(mat, name))
            except Exception:
                defaults[key][str(name)] = None

    _report["material_graphs"].append({
        "ue_path": ue_path,
        "node_count": len(nodes),
        "nodes": nodes,
        "outputs": outputs,
        "parameters": defaults,
        "blend_mode": enum_name(prop(mat, "blend_mode")),
        "shading_model": enum_name(prop(mat, "shading_model")),
        "material_domain": enum_name(prop(mat, "material_domain")),
        "two_sided": bool(prop(mat, "two_sided", False)),
        "opacity_mask_clip_value": prop(mat, "opacity_mask_clip_value", 0.333),
        "dither_opacity_mask": bool(prop(mat, "dither_opacity_mask", False)),
    })


# ----------------------------------------------------------------------------
# Обход ассетов
# ----------------------------------------------------------------------------

def matches_any(ue_path, prefixes):
    return any(ue_path == p or ue_path.startswith(p.rstrip("/") + "/") for p in prefixes)


def expand_dependencies(registry, seeds):
    """
    Тестовая выборка бесполезна без зависимостей: материал без своих текстур
    не проверишь. Рекурсивно тянем всё, на что ссылаются seed-ассеты.
    """
    options = unreal.AssetRegistryDependencyOptions()
    options.set_editor_property("include_soft_package_references", True)
    options.set_editor_property("include_hard_package_references", True)

    seen = set(seeds)
    queue = list(seeds)
    while queue:
        current = queue.pop()
        try:
            deps = registry.get_dependencies(current, options) or []
        except Exception:
            continue
        for dep in deps:
            dep = str(dep)
            if dep.startswith("/Game/") and dep not in seen:
                seen.add(dep)
                queue.append(dep)
    return seen


# ----------------------------------------------------------------------------
# Уровни (.umap) -> сцены Unity
# ----------------------------------------------------------------------------
# Вся интерпретация (маппинг света, сборка level-JSON, дедуп) живёт в чистом
# level_export.py и покрыта тестами. Здесь только извлечение сырых данных из
# объектов unreal — то, что вне редактора не запустить.

# Компоненты, которые сознательно не переносим; ключ — причина для отчёта.
_UNSUPPORTED_COMPONENTS = [
    ("NiagaraComponent", "export.level.skip.niagara"),
    ("ParticleSystemComponent", "export.level.skip.particles"),
    ("CameraComponent", "export.level.skip.camera"),
    ("SkyLightComponent", "export.level.skip.skylight"),
    ("AudioComponent", "export.level.skip.audio"),
    ("LandscapeComponent", "export.level.skip.landscape"),
    ("DecalComponent", "export.level.skip.decal"),
]


def _transform_of(transform):
    """unreal.Transform -> сырой dict level_export (сантиметры + кватернион)."""
    loc = transform.translation
    quat = transform.rotation
    scale = transform.scale3d
    return level_export.make_transform(
        [loc.x, loc.y, loc.z],
        [quat.x, quat.y, quat.z, quat.w],
        [scale.x, scale.y, scale.z])


def _mesh_of(component):
    mesh = prop(component, "static_mesh")
    return asset_path_of(mesh) if mesh else None


def _material_overrides(component):
    """Переопределения материалов компонента по индексам слотов."""
    overrides = []
    for index, material in enumerate(prop(component, "override_materials", None) or []):
        if material is not None:
            overrides.append({"index": index, "material": asset_path_of(material)})
    return overrides


def _instance_transform(component, index):
    """
    Трансформ инстанса ISM/HISM в мировых координатах.
    Сигнатура get_instance_transform плавала между версиями: где-то возвращает
    сам Transform, где-то пару (успех, Transform). Разбираем оба варианта.
    """
    result = component.get_instance_transform(index, True)
    if isinstance(result, tuple):
        # (bool, Transform) — берём трансформ, если операция удалась.
        return result[1] if len(result) > 1 and result[0] else None
    return result


def _light_raw(component, actor_id, light_id):
    """Сырые поля источника света для level_export.build_light."""
    color = prop(component, "light_color")     # unreal.Color, байты 0..255
    rgb = [color.r / 255.0, color.g / 255.0, color.b / 255.0] if color else None
    return {
        "id": light_id,
        "parentId": actor_id,
        "componentType": type(component).__name__,
        "transform": _transform_of(component.get_world_transform()),
        "color": rgb,
        "intensity": prop(component, "intensity"),
        "intensityUnit": enum_name(prop(component, "intensity_units")),
        "attenuationRadiusCm": prop(component, "attenuation_radius"),
        "outerConeDeg": prop(component, "outer_cone_angle"),
        "innerConeDeg": prop(component, "inner_cone_angle"),
        "sourceWidthCm": prop(component, "source_width"),
        "sourceHeightCm": prop(component, "source_height"),
        "castShadows": bool(prop(component, "cast_shadows", True)),
        "visible": bool(prop(component, "visible", True)),
        "mobility": enum_name(prop(component, "mobility")),
        "temperature": prop(component, "temperature") if prop(component, "use_temperature", False) else None,
    }


def _collect_actor(actor, objects, lights, skipped):
    """Разбирает один Actor: меши, ISM/HISM, свет; неподдержанное — в skipped."""
    actor_id = actor.get_path_name()
    handled = False

    for component in (actor.get_components_by_class(unreal.StaticMeshComponent) or []):
        comp_id = component.get_path_name()
        comp_type = type(component).__name__
        mesh = _mesh_of(component)
        if not mesh:
            continue
        overrides = _material_overrides(component)

        if isinstance(component, unreal.InstancedStaticMeshComponent):
            # ISM/HISM разворачиваем в отдельные объекты с общей ссылкой на меш.
            count = component.get_instance_count()
            for i in range(count):
                transform = _instance_transform(component, i)
                if transform is None:
                    continue
                objects.append({
                    "id": "%s#%d" % (comp_id, i),
                    "parentId": actor_id,
                    "mesh": mesh,
                    "transform": _transform_of(transform),
                    "materialOverrides": overrides,
                    "componentType": comp_type,
                    "ismIndex": i,
                })
            handled = handled or count > 0
        else:
            objects.append({
                "id": comp_id,
                "parentId": actor_id,
                "mesh": mesh,
                "transform": _transform_of(component.get_world_transform()),
                "materialOverrides": overrides,
                "componentType": comp_type,
                "ismIndex": -1,
            })
            handled = True

    # SpotLightComponent — подкласс PointLightComponent, а Rect — подкласс
    # LocalLightComponent: один запрос вернёт свет чужого типа, поэтому
    # собираем все источники в словарь по пути и снимаем дубликаты, а точный
    # тип определяем уже по имени класса компонента.
    seen_lights = {}
    for cls in (unreal.DirectionalLightComponent, unreal.PointLightComponent,
                unreal.SpotLightComponent, unreal.RectLightComponent):
        for component in (actor.get_components_by_class(cls) or []):
            seen_lights[component.get_path_name()] = component
    for light_id, component in seen_lights.items():
        lights.append(_light_raw(component, actor_id, light_id))
        handled = True

    if not handled:
        # Ничего переносимого не нашлось — отметим, если это узнаваемый
        # неподдержанный тип, чтобы отчёт объяснил отсутствие объекта.
        for comp_name, reason_key in _UNSUPPORTED_COMPONENTS:
            cls = getattr(unreal, comp_name, None)
            if cls is not None and actor.get_components_by_class(cls):
                skipped.append({
                    "actor": actor_id,
                    "component": comp_name,
                    "reason_key": reason_key,
                    "reason_args": {},
                    "reason": t(reason_key),
                })
                break


def _actor_record(actor):
    """Запись Actor для иерархии сцены."""
    parent = actor.get_attach_parent_actor()
    hidden = bool(prop(actor, "hidden", False))
    return {
        "id": actor.get_path_name(),
        "name": actor.get_actor_label(),
        "parentId": parent.get_path_name() if parent else None,
        "transform": _transform_of(actor.get_actor_transform()),
        "active": not hidden,
        "hidden": hidden,
    }


def collect_level_data(registry):
    """
    Загружает выбранные .umap, разбирает Actors и собирает level-JSON в память.
    Возвращает набор seed-ассетов (меши + переопределения материалов) для
    последующего замыкания зависимостей. Сами файлы пишутся позже write_levels.
    """
    level_subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    multiplier = float(CONFIG.get("unity", {}).get("light_intensity_multiplier", 1.0) or 1.0)

    seeds = set()
    for level_path in CONFIG["scope"].get("level_paths", []):
        name = level_path.rsplit("/", 1)[-1]
        try:
            loaded = level_subsystem.load_level(level_path)
        except Exception as exc:
            error(level_path, exc)
            continue
        if not loaded:
            skip(level_path, "export.level.skip.load_failed")
            continue

        actors, objects, lights, skipped = [], [], [], []
        for actor in (actor_subsystem.get_all_level_actors() or []):
            try:
                actors.append(_actor_record(actor))
                _collect_actor(actor, objects, lights, skipped)
            except Exception as exc:
                error(actor.get_path_name(), exc)

        built_lights = [level_export.build_light(raw, multiplier) for raw in lights]
        level = level_export.make_level(level_path, name, actors, objects, built_lights, skipped)
        rel_file = level_export.level_output_path(level_path, name)
        _levels.append((level, rel_file))
        seeds |= level_export.collect_seed_assets(objects)
        log(t("export.level_collected", name=name, objects=len(objects), lights=len(built_lights)))

    return seeds


def write_levels():
    """Пишет собранные level-JSON на диск и заполняет _report['levels']."""
    for level, rel_file in _levels:
        full = os.path.join(OUT_DIR, rel_file.replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            json.dump(level, fh, indent=2, ensure_ascii=False)
        _report["levels"].append({
            "uePath": level["uePath"],
            "name": level["name"],
            "file": rel_file,
        })


def collect_assets():
    """Список (asset_data, ue_path, class_name) с учётом фильтров конфига."""
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    log(t("export.scanning_registry"))
    registry.scan_paths_synchronous(["/Game"], force_rescan=False)

    scope = CONFIG["scope"]
    mode = scope.get("mode", "test")
    subset = set(scope.get("test_subset", []))
    if mode == "test":
        subset = expand_dependencies(registry, subset)
        log(t("export.test_subset", count=len(subset)))
    elif mode == "levels":
        seeds = collect_level_data(registry)
        subset = expand_dependencies(registry, seeds)
        log(t("export.level_subset", levels=len(_levels), count=len(subset)))

    result = []
    for data in registry.get_assets_by_path("/Game", recursive=True):
        ue_path = str(data.package_name)
        class_name = str(data.asset_class_path.asset_name)

        if matches_any(ue_path, scope.get("exclude_paths", [])):
            continue
        if mode == "test":
            if ue_path not in subset:
                continue
        elif mode == "levels":
            if ue_path not in subset:
                continue
        elif mode == "selected":
            if not matches_any(ue_path, scope.get("include_paths", [])):
                continue
        # mode == "all" -> берём всё, что осталось после exclude

        result.append((data, ue_path, class_name))
    return result


# Классы, которые мы сознательно не конвертируем, с человеческой причиной.
# Значение — ключ локализации: текст причины доедет до отчёта и будет показан
# на том языке, который выбран в момент постобработки.
UNSUPPORTED_REASONS = {
    "Blueprint": "export.skip.blueprint",
    "World": "export.skip.world",
    "NiagaraSystem": "export.skip.niagara_system",
    "NiagaraEmitter": "export.skip.niagara_emitter",
    "SoundWave": "export.skip.sound",
    "AnimBlueprint": "export.skip.anim_blueprint",
    "Skeleton": "export.skip.skeleton",
    "PhysicsAsset": "export.skip.physics_asset",
}


def handle_asset(data, ue_path, class_name):
    export_cfg = CONFIG["export"]

    if class_name == "Texture2D":
        if not export_cfg.get("textures", True):
            return
        progress(ue_path)
        export_texture(data.get_asset(), ue_path)

    elif class_name == "StaticMesh":
        if not export_cfg.get("static_meshes", True):
            return
        progress(ue_path)
        export_static_mesh(data.get_asset(), ue_path)

    elif class_name == "SkeletalMesh":
        if not export_cfg.get("skeletal_meshes", True):
            return
        progress(ue_path)
        export_skeletal_mesh(data.get_asset(), ue_path)

    elif class_name == "AnimSequence":
        if not export_cfg.get("animations", True):
            return
        progress(ue_path)
        export_animation(data.get_asset(), ue_path)

    elif class_name in ("MaterialInstanceConstant", "MaterialInstance"):
        if not export_cfg.get("materials", True):
            return
        progress(ue_path)
        export_material_instance(data.get_asset(), ue_path)

    elif class_name == "Material":
        if not export_cfg.get("materials", True):
            return
        progress(ue_path)
        mat = data.get_asset()
        # Инстансы всё равно нужны — мастер тоже может висеть на меше напрямую.
        export_material_instance(mat, ue_path)
        if export_cfg.get("material_graphs", True) and \
                matches_any(ue_path, CONFIG["shader_gen"].get("roots", [])):
            name = ue_path.rsplit("/", 1)[-1]
            if name in CONFIG["shader_gen"].get("skip", []):
                # Причина из конфига — это осознанное решение пользователя про
                # конкретный материал, переводить её нам нечем и незачем.
                configured = CONFIG["shader_gen"].get("skip_reason", {}).get(name)
                if configured:
                    skip(ue_path, "export.skip.configured_reason", reason=configured)
                else:
                    skip(ue_path, "export.skip.configured")
            else:
                dump_material_graph(mat, ue_path)

    else:
        reason_key = UNSUPPORTED_REASONS.get(class_name)
        if reason_key:
            skip(ue_path, reason_key)
        else:
            skip(ue_path, "export.skip.unknown_class", cls=class_name)


# Порядок обработки. Скелетные меши и анимации идут последними осознанно:
# их экспортёр умеет уронить редактор ассертом, и всё, что успели собрать до
# них, должно остаться на диске.
CLASS_ORDER = {
    "Material": 0,
    "MaterialInstanceConstant": 1,
    "MaterialInstance": 1,
    "Texture2D": 2,
    "StaticMesh": 3,
    "SkeletalMesh": 8,
    "AnimSequence": 9,
}


def write_manifest():
    os.makedirs(OUT_DIR, exist_ok=True)
    manifest_path = os.path.join(OUT_DIR, "manifest.json")
    tmp_path = manifest_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(_report, fh, indent=2, ensure_ascii=False)
    os.replace(tmp_path, manifest_path)   # атомарно: битого манифеста не будет
    return manifest_path


def main():
    global _progress_total

    assets = collect_assets()
    assets.sort(key=lambda item: (CLASS_ORDER.get(item[2], 5), item[1]))
    _progress_total = len(assets)
    log(t("export.queued", count=_progress_total, mode=CONFIG["scope"].get("mode")))

    for number, (data, ue_path, class_name) in enumerate(assets, 1):
        try:
            handle_asset(data, ue_path, class_name)
        except Exception as exc:
            error(ue_path, exc)
        # Контрольная точка: если движок упадёт на следующем ассете, работа
        # предыдущих не пропадёт.
        if number % 25 == 0:
            write_manifest()

    write_levels()
    manifest_path = write_manifest()

    log("=" * 60)
    log(t("export.done", path=manifest_path))
    for key in ("textures", "static_meshes", "skeletal_meshes", "animations",
                "materials", "material_graphs", "material_functions",
                "levels", "skipped", "errors"):
        log("  %-22s %d" % (t("summary." + key), len(_report[key])))


main()
