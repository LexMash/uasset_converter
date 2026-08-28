# -*- coding: utf-8 -*-
"""
Сквозной прогон shader_gen + postprocess на синтетическом манифесте.

Тест не про качество шейдеров — про то, что шаги стыкуются: манифест
экспорта -> shaders.json -> unity_manifest.json -> unsupported.md. Именно
здесь ловятся расхождения формата между слоями, которых golden-тест
одного генератора не видит.
"""
import io
import json
import os
import subprocess
import sys

import pytest

import fixtures

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _manifest(out_dir):
    """Манифест экспорта: два материала (мастер + инстанс) и один пропуск."""
    return {
        "textures": [],
        "static_meshes": [],
        "skeletal_meshes": [],
        "animations": [{
            "ue_path": "/Game/Test/Anims/MF_Walk_Fwd",
            "file": "Animations/Test/Anims/MF_Walk_Fwd.fbx",
            "skeleton": "/Game/Test/SK_Test",
            "duration": 1.0, "frame_rate": "30", "number_of_frames": 30,
            "root_motion": False,
        }],
        "materials": [
            {
                "ue_path": "/Game/Test/M_PbrBasic",
                "class": "Material",
                "parent": None,
                "defaults": {"scalars": {"Roughness": 0.6, "Metallic": 0.0},
                             "vectors": {"Tint": [1.0, 0.5, 0.25, 1.0]},
                             "textures": {"BaseColor": "/Game/Test/T_Base",
                                          "Normal": "/Game/Test/T_Normal"},
                             "switches": {}},
                "textures": {}, "scalars": {}, "vectors": {}, "switches": {},
                "base_property_overrides": {},
                "used_textures": ["/Game/Test/T_Base"],
            },
            {
                "ue_path": "/Game/Test/MI_PbrBasic_Red",
                "class": "MaterialInstanceConstant",
                "parent": "/Game/Test/M_PbrBasic",
                "defaults": {},
                "textures": {}, "scalars": {"Roughness": 0.2},
                "vectors": {"Tint": [1.0, 0.0, 0.0, 1.0]},
                "switches": {},
                "base_property_overrides": {"two_sided": True},
                "used_textures": [],
            },
        ],
        "material_graphs": [fixtures.pbr_basic()],
        "skipped": [{"ue_path": "/Game/Test/BP_Thing",
                     "reason_key": "export.skip.blueprint",
                     "reason_args": {},
                     "reason": "Blueprints are logic, not art"}],
        "errors": [],
    }


def _config(out_dir, language):
    with io.open(os.path.join(ROOT, "config.default.json"), encoding="utf-8") as fh:
        config = json.load(fh)
    config["paths"]["output_dir"] = out_dir
    config["language"] = language
    config["shader_gen"]["roots"] = ["/Game"]
    return config


def _run(script, config_path):
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, script), "--config", config_path],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"))


@pytest.mark.parametrize("language", ["en", "ru"])
def test_shaders_then_postprocess(tmp_path, language):
    out_dir = str(tmp_path)
    with io.open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(_manifest(out_dir), fh, ensure_ascii=False)

    config_path = os.path.join(out_dir, "config.json")
    with io.open(config_path, "w", encoding="utf-8") as fh:
        json.dump(_config(out_dir, language), fh, ensure_ascii=False)

    result = _run("shader_gen.py", config_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert os.path.isfile(os.path.join(out_dir, "shaders.json"))
    assert os.path.isfile(os.path.join(out_dir, "Shaders", "M_PbrBasic.shader"))

    result = _run("postprocess.py", config_path)
    assert result.returncode == 0, result.stdout + result.stderr

    with io.open(os.path.join(out_dir, "unity_manifest.json"), encoding="utf-8") as fh:
        unity = json.load(fh)

    # Инстанс садится на транспилированный шейдер мастера, а не на Lit.
    modes = {m["uePath"]: m["mode"] for m in unity["materials"]}
    assert modes["/Game/Test/MI_PbrBasic_Red"] == "shader"
    instance = next(m for m in unity["materials"]
                    if m["uePath"] == "/Game/Test/MI_PbrBasic_Red")
    assert instance["twoSided"] is True
    # Переопределение инстанса победило дефолт мастера.
    tint = next(c for c in instance["colors"] if c["name"] == "_Tint")
    assert (tint["r"], tint["g"], tint["b"]) == (1.0, 0.0, 0.0)

    report = io.open(os.path.join(out_dir, "unsupported.md"), encoding="utf-8").read()
    assert report.strip(), "отчёт пустой"
    # Причина пропуска отрендерена на языке постобработки, а не на языке экспорта.
    expected = ("Blueprints are logic" if language == "en" else "Блюпринты")
    assert expected in report
