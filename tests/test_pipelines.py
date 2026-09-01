# -*- coding: utf-8 -*-
"""
Сборка карты масок в постобработке (URP).

Unreal держит металличность, шероховатость и AO по отдельности или в своей
упаковке; URP/Lit ждёт их в _MetallicGlossMap (RGB = metallic, A = smoothness)
и отдельной _OcclusionMap — перепутать нельзя молча: материал соберётся, но
будет выглядеть неверно.
"""
import io
import json
import os

import numpy as np
from PIL import Image

import postprocess


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _config(out_dir):
    with io.open(os.path.join(ROOT, "config.default.json"), encoding="utf-8") as fh:
        config = json.load(fh)
    config["paths"]["output_dir"] = out_dir
    return config


def _write_texture(out_dir, relative, colour):
    path = os.path.join(out_dir, relative.replace("/", os.sep))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (4, 4), colour).save(path)
    return relative


def _library(out_dir):
    manifest = {"textures": [
        {"ue_path": "/Game/T_Metal", "file": _write_texture(out_dir, "Textures/T_Metal.png",
                                                            (255, 255, 255)),
         "srgb": False, "is_normal_map": False},
        {"ue_path": "/Game/T_Rough", "file": _write_texture(out_dir, "Textures/T_Rough.png",
                                                            (64, 64, 64)),
         "srgb": False, "is_normal_map": False},
        {"ue_path": "/Game/T_AO", "file": _write_texture(out_dir, "Textures/T_AO.png",
                                                         (128, 128, 128)),
         "srgb": False, "is_normal_map": False},
    ]}
    return postprocess.TextureLibrary(manifest, out_dir)


SLOTS = {"metallic": "/Game/T_Metal", "roughness": "/Game/T_Rough",
         "occlusion": "/Game/T_AO"}


def _mask(out_dir):
    library = _library(out_dir)
    mask, ao, ao_generated = postprocess.build_metallic_smoothness(
        library, dict(SLOTS), [], {}, "M_Test")
    assert mask, "маска не собрана"
    data = np.asarray(Image.open(os.path.join(out_dir, mask.replace("/", os.sep))))
    return data, ao, ao_generated


def test_urp_mask_is_metallic_gloss(tmp_path):
    """URP/Lit ждёт металличность в RGB и гладкость в альфе."""
    data, ao, _generated = _mask(str(tmp_path))
    red, green, blue, alpha = data[0, 0]
    assert red == green == blue, "в URP металличность лежит во всех трёх каналах"
    assert red > 250, "металличность из белой карты обязана остаться единицей"
    # roughness 64/255 -> smoothness 1 - 0.25 = 0.75
    assert 185 <= alpha <= 195, "альфа обязана быть инверсией шероховатости"
    assert ao == "/Game/T_AO", "в URP окклюзия остаётся отдельной картой"


def test_fallback_material_slot_names(tmp_path):
    """Карта масок садится в _MetallicGlossMap, материал — на URP/Lit."""
    out_dir = str(tmp_path)
    library = _library(out_dir)
    config = _config(out_dir)
    values = {"textures": {"Metallic": "/Game/T_Metal", "Roughness": "/Game/T_Rough"},
              "scalars": {}, "vectors": {}, "switches": {}}

    built = postprocess.build_fallback_material(
        {"ue_path": "/Game/M_Test", "class": "Material"}, values,
        {"blend_mode": "BLEND_OPAQUE"}, config["material_mapping"], library, config)

    assert "_MetallicGlossMap" in built["textures"]
    assert built["shader"] == "Universal Render Pipeline/Lit"
