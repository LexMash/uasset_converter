#!/usr/bin/env python3
"""
Слой 3: сырой манифест Unreal -> манифест в терминах Unity.

Здесь происходит вся интерпретация, чтобы C#-сторона ничего не додумывала:
  * разрешение параметров по цепочке инстансов до мастер-материала;
  * выбор пути — свой транспилированный шейдер или fallback на URP/Lit;
  * перепаковка каналов масок (Unreal отдаёт roughness, Unity ждёт smoothness);
  * переворот зелёного канала нормалей;
  * эвристика зацикливания анимаций.
"""
import argparse
import json
import os
import re
import sys

import numpy as np
from PIL import Image

import level_export
from i18n import t, set_language
from shader_gen import sanitize

# Технические параметры движка: у них нет и не должно быть аналога в Unity,
# в отчёт про «не нашлось соответствия» они попадать не должны.
INTERNAL_PARAMETERS = {
    "RefractionDepthBias", "Refraction Depth Bias",
}

HERE = os.path.dirname(os.path.abspath(__file__))


def log(message):
    print(message, flush=True)


# ----------------------------------------------------------------------------
# Разрешение параметров по цепочке материалов
# ----------------------------------------------------------------------------

def build_chain(ue_path, by_path):
    """От мастера к листу: [Material, MI_родитель, ..., MI_лист]."""
    chain, seen = [], set()
    current = ue_path
    while current and current in by_path and current not in seen:
        seen.add(current)
        chain.append(by_path[current])
        current = by_path[current].get("parent")
    chain.reverse()
    return chain


def resolve_parameters(ue_path, by_path):
    """
    Итоговые значения параметров материала.
    Начинаем с дефолтов мастера и накатываем переопределения каждого инстанса
    сверху вниз — ровно так, как их применяет сам Unreal.
    """
    chain = build_chain(ue_path, by_path)
    values = {"textures": {}, "scalars": {}, "vectors": {}, "switches": {}}
    master = None

    for record in chain:
        if record.get("class") == "Material":
            master = record.get("ue_path")
            defaults = record.get("defaults") or {}
            for key in values:
                values[key].update(defaults.get(key) or {})
        for key in values:
            values[key].update(record.get(key) or {})

    return master, values, chain


def resolve_render_state(chain):
    """Blend mode и двусторонность: побеждает самое нижнее переопределение."""
    state = {"blend_mode": "BLEND_OPAQUE", "two_sided": False,
             "shading_model": "MSM_DEFAULT_LIT", "opacity_mask_clip_value": 0.333}
    for record in chain:
        for key, value in (record.get("base_property_overrides") or {}).items():
            state[key] = value
    return state


# ----------------------------------------------------------------------------
# Работа с текстурами
# ----------------------------------------------------------------------------

class TextureLibrary:
    """Доступ к экспортированным текстурам плюс кэш производных карт."""

    def __init__(self, manifest, out_dir):
        self.out_dir = out_dir
        self.by_path = {t["ue_path"]: t for t in manifest.get("textures", [])}
        self.derived = {}
        self.generated = []
        self._sizes = {}

    def record(self, ue_path):
        return self.by_path.get(ue_path)

    def abs_path(self, record):
        return os.path.join(self.out_dir, record["file"].replace("/", os.sep))

    def load(self, ue_path, channels="RGBA"):
        record = self.record(ue_path)
        if record is None:
            return None
        path = self.abs_path(record)
        if not os.path.isfile(path):
            return None
        with Image.open(path) as image:
            return np.asarray(image.convert(channels), dtype=np.float32) / 255.0

    def size_of(self, ue_path):
        """
        Размер берём из самого файла: Unreal через blueprint_get_size_* отдаёт
        размер загруженного мипа, а не оригинала, и на этом легко ошибиться.
        """
        if ue_path in self._sizes:
            return self._sizes[ue_path]
        record = self.record(ue_path)
        size = None
        if record is not None:
            path = self.abs_path(record)
            if os.path.isfile(path):
                try:
                    with Image.open(path) as image:
                        size = image.size
                except OSError:
                    size = None
        self._sizes[ue_path] = size
        return size

    def _derived_path(self, name):
        folder = os.path.join(self.out_dir, "Textures", "_Generated")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, name)

    def save_derived(self, name, array, key, srgb=False, is_normal=False):
        """Сохраняет производную карту один раз и переиспользует её дальше."""
        if key in self.derived:
            return self.derived[key]
        path = self._derived_path(name)
        data = np.clip(array, 0.0, 1.0)
        image = Image.fromarray((data * 255.0 + 0.5).astype(np.uint8),
                                mode="RGBA" if data.shape[2] == 4 else "RGB")
        image.save(path)
        relative = os.path.relpath(path, self.out_dir).replace("\\", "/")
        self.derived[key] = relative
        self.generated.append({"file": relative, "srgb": srgb, "is_normal": is_normal})
        return relative


def resize_to(array, width, height):
    if array is None:
        return None
    if array.shape[1] == width and array.shape[0] == height:
        return array
    image = Image.fromarray((np.clip(array, 0, 1) * 255).astype(np.uint8))
    return np.asarray(image.resize((width, height), Image.BILINEAR), dtype=np.float32) / 255.0


# Куда какая карта садится в дефолтном URP/Lit-материале. URP держит
# металличность и гладкость в одной карте (_MetallicGlossMap: RGB = metallic,
# A = smoothness), окклюзию — отдельной картой (_OcclusionMap).
SLOTS = {
    "base": "_BaseMap", "normal": "_BumpMap", "mask": "_MetallicGlossMap",
    "occlusion": "_OcclusionMap", "emission": "_EmissionMap",
    "base_color": "_BaseColor", "emission_color": "_EmissionColor",
    "metallic": "_Metallic", "smoothness": "_Smoothness",
}


# Что означают каналы в упакованных текстурах. Ключ — суффикс имени.
PACKED_LAYOUTS = {
    "MRA": {"metallic": 0, "roughness": 1, "occlusion": 2},
    "ORM": {"occlusion": 0, "roughness": 1, "metallic": 2},
    "ARM": {"occlusion": 0, "roughness": 1, "metallic": 2},
    "RMA": {"roughness": 0, "metallic": 1, "occlusion": 2},
}


def classify_textures(textures, mapping, library):
    """
    Раскладывает текстуры материала по ролям.
    Сначала пробуем имя параметра, потом — имя самого файла: в паках часто
    параметр называется невнятно, зато файл честно оканчивается на _N или _MRA.
    """
    slots, packed = {}, []

    def try_rules(text, rules):
        for rule in rules:
            if re.search(rule["match"], text):
                return rule
        return None

    def rule_priority(rule):
        """Индекс правила в списке — чем раньше, тем точнее описана роль."""
        for index, candidate in enumerate(mapping["texture_params"]):
            if candidate is rule:
                return index
        return len(mapping["texture_params"])

    best = {}   # slot -> (приоритет, путь)

    for param_name, texture_path in textures.items():
        if not texture_path:
            continue
        rule = try_rules(param_name, mapping["texture_params"])
        priority = rule_priority(rule) if rule is not None else None
        if rule is None:
            # Самый надёжный признак — тип сжатия, проставленный в самом Unreal.
            # Имена вроде T_Manny_01_BN под шаблон "_N в конце" не попадают,
            # а TC_NORMALMAP не врёт.
            record = library.record(texture_path)
            if record is not None and record.get("is_normal_map"):
                rule = {"slot": "normal"}
        if rule is None:
            asset_name = texture_path.rsplit("/", 1)[-1]
            for slot, patterns in mapping.get("texture_name_hints", {}).items():
                if slot.startswith("_"):
                    continue
                if any(re.search(p, asset_name) for p in patterns):
                    rule = {"slot": slot}
                    if slot == "packed":
                        suffix = asset_name.rsplit("_", 1)[-1].upper()
                        rule["channels"] = suffix if suffix in PACKED_LAYOUTS else "MRA"
                    break
        if rule is None:
            continue
        if priority is None:
            # Роль определена не по имени параметра, а по данным Unreal или по
            # имени файла — это слабее прямого совпадения правила.
            priority = len(mapping["texture_params"]) + 1

        if rule["slot"] == "packed":
            packed.append((texture_path, rule.get("channels", "MRA")))
            continue

        # Побеждает не первый попавшийся параметр, а тот, чья роль описана
        # точнее: иначе результат зависит от порядка ключей в манифесте.
        current = best.get(rule["slot"])
        if current is None or priority < current[0]:
            best[rule["slot"]] = (priority, texture_path)

    slots = {slot: path for slot, (_, path) in best.items()}
    return slots, packed


def build_metallic_smoothness(library, slots, packed, scalars, material_name):
    """
    Собирает URP-карту _MetallicGlossMap (RGB = metallic, A = smoothness).

    Unreal не хранит ни ту, ни другую: у него отдельные Roughness, Metallic и
    AO либо своя упаковка MRA/ORM. Поэтому карту приходится собирать заново —
    из отдельных карт, из упакованной или из скалярных значений.

    Возвращает (маска или None, готовая occlusion-карта или None,
    сгенерированная occlusion-карта или None).
    """
    metallic_map = slots.get("metallic")
    roughness_map = slots.get("roughness")
    occlusion_map = slots.get("occlusion")
    metallic_const = float(scalars.get("metallic", 0.0) or 0.0)
    roughness_const = float(scalars.get("roughness", 0.5) or 0.5)

    packed_source = packed[0] if packed else None
    if not (metallic_map or roughness_map or packed_source):
        return None, occlusion_map, None   # всё скалярное — карта не нужна

    # Разрешение берём по самой крупной из участвующих текстур.
    width = height = 0
    for candidate in [metallic_map, roughness_map, packed_source[0] if packed_source else None]:
        if candidate:
            size = library.size_of(candidate)
            if size:
                width, height = max(width, size[0]), max(height, size[1])
    if width == 0 or height == 0:
        return None, occlusion_map, None

    metallic = np.full((height, width), metallic_const, dtype=np.float32)
    roughness = np.full((height, width), roughness_const, dtype=np.float32)
    occlusion = None

    if packed_source:
        path, layout_name = packed_source
        layout = PACKED_LAYOUTS.get(layout_name, PACKED_LAYOUTS["MRA"])
        data = resize_to(library.load(path, "RGB"), width, height)
        if data is not None:
            if "metallic" in layout:
                metallic = data[:, :, layout["metallic"]]
            if "roughness" in layout:
                roughness = data[:, :, layout["roughness"]]
            if "occlusion" in layout:
                occlusion = data[:, :, layout["occlusion"]]

    if metallic_map:
        data = resize_to(library.load(metallic_map, "RGB"), width, height)
        if data is not None:
            metallic = data[:, :, 0]
    if roughness_map:
        data = resize_to(library.load(roughness_map, "RGB"), width, height)
        if data is not None:
            roughness = data[:, :, 0]

    rgba = np.zeros((height, width, 4), dtype=np.float32)
    rgba[:, :, 0] = metallic
    rgba[:, :, 1] = metallic
    rgba[:, :, 2] = metallic
    rgba[:, :, 3] = 1.0 - roughness          # smoothness — это инверсия roughness

    key = "ms|%s|%s|%s|%g|%g" % (metallic_map, roughness_map,
                                 packed_source[0] if packed_source else None,
                                 metallic_const, roughness_const)
    ms_path = library.save_derived("%s_MetallicSmoothness.png" % material_name, rgba, key)

    ao_path = occlusion_map
    if occlusion is not None and occlusion_map is None:
        ao_rgb = np.repeat(occlusion[:, :, None], 3, axis=2)
        ao_key = "ao|%s" % (packed_source[0] if packed_source else material_name)
        ao_path = library.save_derived("%s_AO.png" % material_name, ao_rgb, ao_key)
        return ms_path, None, ao_path

    return ms_path, ao_path, None


def flip_normal_green(library, ue_path, material_name):
    """
    Unreal хранит нормали в DirectX-конвенции (Y вниз), Unity ждёт OpenGL
    (Y вверх) — зелёный канал надо инвертировать. Флаг в конфиге есть именно
    потому, что для конкретного пака это стоит проверить глазами.
    """
    data = library.load(ue_path, "RGB")
    if data is None:
        return None
    data = data.copy()
    data[:, :, 1] = 1.0 - data[:, :, 1]
    return library.save_derived("%s_N_flipped.png" % material_name, data,
                                "flipG|%s" % ue_path, is_normal=True)


def classify_by_rules(values, rules):
    """
    Раскладывает параметры по ролям, перебирая ПРАВИЛА в порядке приоритета.

    Наоборот нельзя: у материала с SurfaceColor, GridColor и SubGridColor
    победил бы тот, что раньше попался в словаре, а не тот, что точнее
    описывает роль — и результат зависел бы от порядка ключей в файле.
    """
    result = {}
    for rule in rules:
        if rule["slot"] in result:
            continue
        for name, value in values.items():
            if re.search(rule["match"], name):
                result[rule["slot"]] = value
                break
    return result


def classify_scalars(scalars, mapping):
    return classify_by_rules(scalars, mapping["scalar_params"])


def classify_vectors(vectors, mapping):
    return classify_by_rules(vectors, mapping["vector_params"])


def build_fallback_material(record, values, state, mapping, library, config):
    """Материал на дефолтном URP/Lit — когда для мастера нет своего шейдера."""
    name = record["ue_path"].rsplit("/", 1)[-1]
    target = SLOTS
    slots, packed = classify_textures(values["textures"], mapping, library)
    scalars = classify_scalars(values["scalars"], mapping)
    vectors = classify_vectors(values["vectors"], mapping)

    ms_map, ao_map, ao_generated = build_metallic_smoothness(
        library, slots, packed, scalars, name)

    normal_map = slots.get("normal")
    if normal_map and config["textures"].get("flip_normal_green", True):
        flipped = flip_normal_green(library, normal_map, name)
    else:
        flipped = None

    textures = {}
    if slots.get("baseMap"):
        textures[target["base"]] = {"ue_path": slots["baseMap"]}
    if normal_map:
        textures[target["normal"]] = ({"file": flipped} if flipped else {"ue_path": normal_map})
    if ms_map:
        textures[target["mask"]] = {"file": ms_map}
    if target["occlusion"]:
        if ao_generated:
            textures[target["occlusion"]] = {"file": ao_generated}
        elif ao_map:
            textures[target["occlusion"]] = {"ue_path": ao_map}
    if slots.get("emission"):
        textures[target["emission"]] = {"ue_path": slots["emission"]}

    floats = {}
    if ms_map is None:
        floats[target["metallic"]] = float(scalars.get("metallic", 0.0) or 0.0)
        floats[target["smoothness"]] = 1.0 - float(scalars.get("roughness", 0.5) or 0.5)
    else:
        # Карта задаёт значения покомпонентно, поэтому множители обязаны быть
        # единичными — иначе она домножится сама на себя.
        floats[target["metallic"]] = 1.0
        floats[target["smoothness"]] = 1.0

    colors = {}
    if vectors.get("baseColor"):
        colors[target["base_color"]] = vectors["baseColor"]
    emission_color = vectors.get("emissionColor")
    if emission_color or slots.get("emission"):
        colors[target["emission_color"]] = emission_color or [1.0, 1.0, 1.0, 1.0]

    return {
        "mode": "fallback",
        "shader": "Universal Render Pipeline/Lit",
        "textures": textures,
        "floats": floats,
        "colors": colors,
        "emission": bool(emission_color or slots.get("emission")),
    }


def build_shader_material(record, values, shader_name, shader_info, library, config):
    """
    Материал на транспилированном шейдере. Здесь эвристики не нужны: имена
    параметров совпадают один в один, потому что шейдер из них и родился.
    """
    known = set(shader_info.get("properties", {}))
    textures, floats, colors, keywords = {}, {}, {}, {}
    unmatched = []

    for ue_name, texture_path in values["textures"].items():
        prop = sanitize(ue_name)
        if prop in known and texture_path:
            textures[prop] = {"ue_path": texture_path}
        elif texture_path and ue_name not in INTERNAL_PARAMETERS:
            unmatched.append(ue_name)

    for ue_name, value in values["scalars"].items():
        prop = sanitize(ue_name)
        if prop in known:
            floats[prop] = float(value or 0.0)
        elif ue_name not in INTERNAL_PARAMETERS:
            unmatched.append(ue_name)

    for ue_name, value in values["vectors"].items():
        prop = sanitize(ue_name)
        if prop in known and isinstance(value, list):
            colors[prop] = value
        elif isinstance(value, list) and ue_name not in INTERNAL_PARAMETERS:
            unmatched.append(ue_name)

    for ue_name, enabled in values["switches"].items():
        for keyword, source in shader_info.get("keywords", {}).items():
            if source == ue_name:
                keywords[keyword] = bool(enabled)
                floats[keyword + "_Toggle"] = 1.0 if enabled else 0.0

    return {
        "mode": "shader",
        "shader": shader_name,
        "textures": textures,
        "floats": floats,
        "colors": colors,
        "keywords": keywords,
        "unmatched_parameters": sorted(set(unmatched)),
        "emission": False,
    }


def build_shadergraph_material(record, values, shadergraph_file, info):
    """
    Материал на транспилированном Unity Shader Graph (второй путь).

    Имена свойств совпадают с HLSL-путём (та же sanitize из общего IR), поэтому
    сопоставление параметров такое же. Отличие одно: StaticSwitch у SG-пути —
    это Boolean-свойство (рантайм-ветка), а не shader_feature, поэтому switch
    едет обычным float 0/1 с именем ключевого слова, без keywords.
    """
    known = set(info.get("properties", {}))
    textures, floats, colors = {}, {}, {}
    unmatched = []

    for ue_name, texture_path in values["textures"].items():
        prop = sanitize(ue_name)
        if prop in known and texture_path:
            textures[prop] = {"ue_path": texture_path}
        elif texture_path and ue_name not in INTERNAL_PARAMETERS:
            unmatched.append(ue_name)

    for ue_name, value in values["scalars"].items():
        prop = sanitize(ue_name)
        if prop in known:
            floats[prop] = float(value or 0.0)
        elif ue_name not in INTERNAL_PARAMETERS:
            unmatched.append(ue_name)

    for ue_name, value in values["vectors"].items():
        prop = sanitize(ue_name)
        if prop in known and isinstance(value, list):
            colors[prop] = value
        elif isinstance(value, list) and ue_name not in INTERNAL_PARAMETERS:
            unmatched.append(ue_name)

    for ue_name, enabled in values["switches"].items():
        for keyword, source in info.get("keywords", {}).items():
            if source == ue_name:
                floats[keyword] = 1.0 if enabled else 0.0   # Boolean-свойство

    return {
        "mode": "shadergraph",
        "shader": "",                       # имя не нужно: грузим по пути файла
        "shader_file": shadergraph_file,
        "textures": textures,
        "floats": floats,
        "colors": colors,
        "keywords": {},
        "unmatched_parameters": sorted(set(unmatched)),
        "emission": False,
    }


# ----------------------------------------------------------------------------
# Анимации
# ----------------------------------------------------------------------------

def guess_loop(name, animations_config):
    """
    Unreal не хранит признак «зациклен», а Unity его требует. Угадываем по
    имени клипа и помечаем результат как эвристику — это надо проверять.
    """
    for pattern in animations_config.get("noloop_patterns", []):
        if re.search(pattern, name):
            return False, "post.loop.matched_noloop", {"pattern": pattern}
    for pattern in animations_config.get("loop_patterns", []):
        if re.search(pattern, name):
            return True, "post.loop.matched_loop", {"pattern": pattern}
    return False, "post.loop.no_match", {}


# ----------------------------------------------------------------------------
# Уровни
# ----------------------------------------------------------------------------

def process_levels(out_dir, manifest, notes):
    """
    Переносит уровни в манифест Unity и проверяет их ссылки.

    Сами level-JSON пишет ue_export; здесь мы только собираем их список для C#
    и сверяем, что каждый упомянутый меш/материал действительно экспортирован —
    иначе в сцене окажется дыра, о которой лучше сказать заранее. Пропуски
    Actor/компонентов и заметки по свету из каждого уровня тоже уходят в отчёт.
    """
    exported = set()
    for key in ("static_meshes", "skeletal_meshes", "materials", "textures"):
        for entry in manifest.get(key, []):
            if entry.get("ue_path"):
                exported.add(entry["ue_path"])

    levels = []
    for record in manifest.get("levels", []):
        levels.append({"uePath": record["uePath"], "name": record["name"],
                       "file": record["file"]})

        path = os.path.join(out_dir, record["file"].replace("/", os.sep))
        if not os.path.isfile(path):
            notes.append(("section.levels", record["name"],
                          "post.level.file_missing", {"file": record["file"]}))
            continue
        with open(path, "r", encoding="utf-8") as fh:
            level = json.load(fh)

        for kind, missing in level_export.validate_references(level, exported):
            notes.append(("section.levels", record["name"],
                          "post.level.missing_ref", {"kind": kind, "path": missing}))

        for entry in level.get("skipped", []):
            reason = t(entry["reason_key"], **(entry.get("reason_args") or {})) \
                if entry.get("reason_key") else entry.get("reason", "")
            notes.append(("section.levels", "%s / %s" % (record["name"],
                          entry.get("actor", "?").rsplit(".", 1)[-1]),
                          "post.level.skipped_component",
                          {"component": entry.get("component", "?"), "reason": reason}))

        for light in level.get("lights", []):
            for note in light.get("notes", []):
                reason = t(note["reasonKey"], **(note.get("reasonArgs") or {}))
                notes.append(("section.levels", "%s / %s" % (record["name"],
                              light.get("unrealType", "light")),
                              "post.level.light_note", {"note": reason}))

    return levels


# ----------------------------------------------------------------------------
# Сборка
# ----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Turn the raw Unreal manifest into a Unity-side manifest")
    parser.add_argument("--config", default=os.path.join(HERE, "config.json"))
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as fh:
        config = json.load(fh)
    set_language(config.get("language") or "auto")

    out_dir = config["paths"]["output_dir"]
    manifest_path = os.path.join(out_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        log(t("post.no_manifest", path=manifest_path))
        return 1

    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    shaders_index = {}
    shaders_path = os.path.join(out_dir, "shaders.json")
    if os.path.isfile(shaders_path):
        with open(shaders_path, "r", encoding="utf-8") as fh:
            for entry in json.load(fh).get("shaders", []):
                shaders_index[entry["ue_path"]] = entry

    # Переиспользованные шейдеры не несут списка свойств — берём его у оригинала.
    by_shader_name = {e["shader"]: e for e in shaders_index.values() if not e.get("reused")}

    # Второй путь материалов: Unity Shader Graph. Если он включён и для мастера
    # есть .shadergraph — материал ссылается на него, а не на HLSL-шейдер.
    prefer_shadergraph = bool(config.get("shadergraph", {}).get("enabled"))
    shadergraphs_index = {}
    shadergraphs_path = os.path.join(out_dir, "shadergraphs.json")
    if os.path.isfile(shadergraphs_path):
        with open(shadergraphs_path, "r", encoding="utf-8") as fh:
            for entry in json.load(fh).get("shadergraphs", []):
                shadergraphs_index[entry["ue_path"]] = entry
    by_shadergraph_file = {e["shadergraph"]: e for e in shadergraphs_index.values()
                           if not e.get("reused")}

    materials_by_path = {m["ue_path"]: m for m in manifest.get("materials", [])}
    library = TextureLibrary(manifest, out_dir)
    mapping = config["material_mapping"]

    unity = {"textures": [], "meshes": [], "skeletal_meshes": [],
             "animations": [], "materials": [], "shaders": [], "levels": []}
    notes = []

    # --- текстуры ---------------------------------------------------------
    for texture in manifest.get("textures", []):
        unity["textures"].append({
            "ue_path": texture["ue_path"],
            "file": texture["file"],
            "srgb": texture["srgb"],
            "is_normal": texture["is_normal_map"],
            "wrap_u": texture.get("address_x"),
            "wrap_v": texture.get("address_y"),
        })
        if not texture.get("has_source_art", True):
            notes.append(("section.textures", texture["ue_path"],
                          "post.note.no_source_art", {}))

    # --- материалы --------------------------------------------------------
    shader_count = fallback_count = 0
    for record in manifest.get("materials", []):
        ue_path = record["ue_path"]
        master, values, chain = resolve_parameters(ue_path, materials_by_path)
        state = resolve_render_state(chain)

        shader_entry = shaders_index.get(master) if master else None
        sg_entry = shadergraphs_index.get(master) if (master and prefer_shadergraph) else None
        if sg_entry:
            sg_file = sg_entry["shadergraph"]
            info = sg_entry if not sg_entry.get("reused") else by_shadergraph_file.get(sg_file, {})
            built = build_shadergraph_material(record, values, sg_file, info)
            shader_count += 1
            dropped = set(info.get("dropped_parameters") or [])
            surprising = [p for p in built["unmatched_parameters"] if p not in dropped]
            if surprising:
                notes.append(("section.materials", ue_path,
                              "post.note.unmatched_parameters",
                              {"names": ", ".join(surprising)}))
        elif shader_entry:
            shader_name = shader_entry["shader"]
            info = shader_entry if not shader_entry.get("reused") else by_shader_name.get(shader_name, {})
            built = build_shader_material(record, values, shader_name, info, library, config)
            shader_count += 1
            # Параметры, которые транспилятор выкинул осознанно, не считаются
            # проблемой материала: про них сообщается один раз на шейдер ниже.
            dropped = set(info.get("dropped_parameters") or [])
            surprising = [p for p in built["unmatched_parameters"] if p not in dropped]
            if surprising:
                notes.append(("section.materials", ue_path,
                              "post.note.unmatched_parameters",
                              {"names": ", ".join(surprising)}))
        else:
            built = build_fallback_material(record, values, state, mapping, library, config)
            fallback_count += 1

            # Мастер тянет текстуры прямо в графе, а не через параметры, и ни
            # одна не досталась материалу — характерный признак процедурного
            # мастера (сетка, шум, тришкейл). В Unity приедет плоский цвет.
            master_record = materials_by_path.get(master) if master else None
            if (master_record and master_record.get("used_textures")
                    and not built["textures"]):
                notes.append(("section.materials", ue_path,
                              "post.note.procedural_master",
                              {"master": master.rsplit("/", 1)[-1]}))

            if record.get("class") != "Material" and master is None:
                notes.append(("section.materials", ue_path,
                              "post.note.no_master", {}))

        built.update({
            "ue_path": ue_path,
            "master": master,
            "two_sided": bool(state.get("two_sided")),
            "blend_mode": state.get("blend_mode"),
            "alpha_cutoff": state.get("opacity_mask_clip_value", 0.333),
        })
        unity["materials"].append(built)

    # --- меши -------------------------------------------------------------
    for mesh in manifest.get("static_meshes", []):
        unity["meshes"].append({
            "ue_path": mesh["ue_path"], "file": mesh["file"],
            "material_slots": mesh["material_slots"],
        })
    for mesh in manifest.get("skeletal_meshes", []):
        unity["skeletal_meshes"].append({
            "ue_path": mesh["ue_path"], "file": mesh["file"],
            "skeleton": mesh.get("skeleton"),
            "material_slots": mesh["material_slots"],
        })

    # --- анимации ---------------------------------------------------------
    for anim in manifest.get("animations", []):
        name = anim["ue_path"].rsplit("/", 1)[-1]
        loop, why_key, why_args = guess_loop(name, config["animations"])
        unity["animations"].append({
            "ue_path": anim["ue_path"], "file": anim["file"],
            "clip_name": name, "skeleton": anim.get("skeleton"),
            "loop_time": loop, "root_motion": anim.get("root_motion", False),
        })
        notes.append(("section.animations", anim["ue_path"],
                      "post.note.loop_guess", {"why": t(why_key, **why_args)}))

    # --- производные текстуры --------------------------------------------
    for generated in library.generated:
        unity["textures"].append({
            "ue_path": None, "file": generated["file"],
            "srgb": generated["srgb"], "is_normal": generated["is_normal"],
            "wrap_u": "TA_Wrap", "wrap_v": "TA_Wrap", "generated": True,
        })

    unity["shaders"] = [e for e in shaders_index.values() if not e.get("reused")]
    unity["shadergraphs"] = [e for e in shadergraphs_index.values() if not e.get("reused")]

    # --- уровни -----------------------------------------------------------
    unity["levels"] = process_levels(out_dir, manifest, notes)

    for entry in unity["shaders"]:
        if entry.get("dropped_parameters"):
            notes.append(("section.shaders", entry["shader"],
                          "post.note.dropped_parameters",
                          {"names": ", ".join(entry["dropped_parameters"])}))

    unity_path = os.path.join(out_dir, "unity_manifest.json")
    with open(unity_path, "w", encoding="utf-8") as fh:
        json.dump(to_unity_json(unity), fh, indent=2, ensure_ascii=False)

    write_report(out_dir, manifest, unity, notes, shader_count, fallback_count, library)

    log("")
    log(t("post.done"))
    log("  " + t("post.summary.textures",
                 count=len(manifest.get("textures", [])), generated=len(library.generated)))
    log("  " + t("post.summary.static_meshes", count=len(unity["meshes"])))
    log("  " + t("post.summary.skeletal_meshes", count=len(unity["skeletal_meshes"])))
    log("  " + t("post.summary.animations", count=len(unity["animations"])))
    log("  " + t("post.summary.materials", count=len(unity["materials"]),
                 shader=shader_count, fallback=fallback_count))
    if unity["levels"]:
        log("  " + t("post.summary.levels", count=len(unity["levels"])))
    log("  " + t("post.summary.manifest", path=unity_path))
    return 0


def write_report(out_dir, manifest, unity, notes, shader_count, fallback_count, library):
    """
    unsupported.md — честный список того, что НЕ перенеслось или перенеслось
    приблизительно. Без него конвертер выглядит успешнее, чем он есть.
    """
    path = os.path.join(out_dir, "unsupported.md")
    grouped = {}
    for section_key, subject, message_key, message_args in notes:
        grouped.setdefault(section_key, []).append((subject, t(message_key, **message_args)))

    lines = ["# " + t("report.title"), ""]
    lines.append(t("report.regenerated"))
    lines.append("")
    lines.append("## " + t("report.summary"))
    lines.append("")
    lines.append("| %s | %s |" % (t("report.column.category"), t("report.column.count")))
    lines.append("|---|---|")
    for label_key, value in (
        ("report.row.materials_shader", shader_count),
        ("report.row.materials_fallback", fallback_count),
        ("report.row.textures_exported", len(manifest.get("textures", []))),
        ("report.row.textures_generated", len(library.generated)),
        ("report.row.static_meshes", len(unity["meshes"])),
        ("report.row.skeletal_meshes", len(unity["skeletal_meshes"])),
        ("report.row.animations", len(unity["animations"])),
        ("report.row.export_errors", len(manifest.get("errors", []))),
    ):
        lines.append("| %s | %d |" % (t(label_key), value))
    lines.append("")

    errors = manifest.get("errors", [])
    if errors:
        lines.append("## " + t("report.export_errors"))
        lines.append("")
        for entry in errors:
            lines.append("- `%s` — %s" % (entry["ue_path"], entry["error"]))
        lines.append("")

    # Пропуски группируем по причине: 600 строк «блюпринт не конвертируется»
    # никому не нужны, нужна одна строка с числом.
    skipped = manifest.get("skipped", [])
    if skipped:
        # Группируем по КЛЮЧУ причины, а не по её тексту: иначе одна и та же
        # причина с разными подстановками разъехалась бы на несколько разделов.
        by_reason = {}
        for entry in skipped:
            key = entry.get("reason_key") or entry.get("reason") or "?"
            args = entry.get("reason_args") or {}
            by_reason.setdefault((key, tuple(sorted(args.items()))), []).append(entry["ue_path"])
        lines.append("## " + t("report.skipped"))
        lines.append("")
        for reason in sorted(by_reason, key=lambda r: -len(by_reason[r])):
            paths = by_reason[reason]
            text = t(reason[0], **dict(reason[1])) if reason[0].startswith("export.") else reason[0]
            lines.append("### %s — %s" % (text, t("report.count_of", count=len(paths))))
            lines.append("")
            for item in paths[:15]:
                lines.append("- `%s`" % item)
            if len(paths) > 15:
                lines.append("- " + t("report.and_more", count=len(paths) - 15))
            lines.append("")

    for section in sorted(grouped):
        lines.append("## " + t("report.needs_check", section=t(section)))
        lines.append("")
        for subject, message in grouped[section][:80]:
            lines.append("- `%s` — %s" % (subject, message))
        if len(grouped[section]) > 80:
            lines.append("- " + t("report.and_more", count=len(grouped[section]) - 80))
        lines.append("")

    todo_shaders = [s for s in unity["shaders"] if s.get("todos")]
    if todo_shaders:
        lines.append("## " + t("report.inexact_shaders"))
        lines.append("")
        for entry in todo_shaders:
            lines.append("### `%s`" % entry["shader"])
            lines.append("")
            for todo in entry["todos"]:
                lines.append("- " + t("report.todo_line", type=todo["type"],
                                      index=todo["node"],
                                      message=t(todo["message_key"], **todo.get("message_args", {}))
                                      if todo.get("message_key") else todo["message"]))
            lines.append("")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    log("  " + t("post.summary.report", path=path))



# ----------------------------------------------------------------------------
# Формат для Unity
# ----------------------------------------------------------------------------
# JsonUtility в Unity не умеет словари — только массивы и примитивы. Поэтому
# манифест для C# перекладывается в плоские списки: так на стороне Unity не
# нужен сторонний парсер JSON.

def _named_texture_list(textures):
    return [{"name": name,
             "uePath": entry.get("ue_path") or "",
             "file": entry.get("file") or ""}
            for name, entry in sorted(textures.items())]


def _named_float_list(floats):
    return [{"name": name, "value": float(value)} for name, value in sorted(floats.items())]


def _named_color_list(colors):
    result = []
    for name, value in sorted(colors.items()):
        rgba = list(value) + [1.0] * (4 - len(value))
        result.append({"name": name, "r": rgba[0], "g": rgba[1], "b": rgba[2], "a": rgba[3]})
    return result


def to_unity_json(unity):
    return {
        "textures": [{
            "uePath": t.get("ue_path") or "",
            "file": t["file"],
            "srgb": bool(t["srgb"]),
            "isNormal": bool(t["is_normal"]),
            "wrapU": t.get("wrap_u") or "TA_Wrap",
            "wrapV": t.get("wrap_v") or "TA_Wrap",
            "generated": bool(t.get("generated", False)),
        } for t in unity["textures"]],
        "meshes": [{
            "uePath": m["ue_path"], "file": m["file"],
            "materialSlots": [{"slot": s["slot"], "material": s.get("material") or ""}
                              for s in m["material_slots"]],
        } for m in unity["meshes"]],
        "skeletalMeshes": [{
            "uePath": m["ue_path"], "file": m["file"], "skeleton": m.get("skeleton") or "",
            "materialSlots": [{"slot": s["slot"], "material": s.get("material") or ""}
                              for s in m["material_slots"]],
        } for m in unity["skeletal_meshes"]],
        "animations": [{
            "uePath": a["ue_path"], "file": a["file"], "clipName": a["clip_name"],
            "skeleton": a.get("skeleton") or "",
            "loopTime": bool(a["loop_time"]), "rootMotion": bool(a["root_motion"]),
        } for a in unity["animations"]],
        "materials": [{
            "uePath": m["ue_path"],
            "mode": m["mode"],
            "materialSource": {"shadergraph": "shadergraph", "shader": "transpiled_hlsl",
                               "fallback": "urp_lit"}.get(m["mode"], "urp_lit"),
            "shader": m["shader"],
            "shaderFile": m.get("shader_file") or "",
            "twoSided": bool(m.get("two_sided")),
            "blendMode": m.get("blend_mode") or "BLEND_OPAQUE",
            "alphaCutoff": float(m.get("alpha_cutoff") or 0.333),
            "emission": bool(m.get("emission")),
            "textures": _named_texture_list(m.get("textures") or {}),
            "floats": _named_float_list(m.get("floats") or {}),
            "colors": _named_color_list(m.get("colors") or {}),
            "keywords": [{"name": k, "enabled": bool(v)}
                         for k, v in sorted((m.get("keywords") or {}).items())],
        } for m in unity["materials"]],
        "shaders": [{"uePath": s["ue_path"], "shader": s["shader"], "file": s.get("file", "")}
                    for s in unity["shaders"]],
        "shadergraphs": [{"uePath": s["ue_path"], "file": s["shadergraph"]}
                         for s in unity.get("shadergraphs", [])],
        "levels": [{"uePath": lvl["uePath"], "name": lvl["name"], "file": lvl["file"]}
                   for lvl in unity.get("levels", [])],
    }

if __name__ == "__main__":
    sys.exit(main())
