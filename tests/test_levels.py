# -*- coding: utf-8 -*-
"""
Тесты чистой логики конвертации уровней (level_export.py).

Всё здесь работает без движка: маппинг света, пересчёт интенсивности,
дедупликация ассетов, сборка level-JSON и проверка ссылок. Именно эта логика
не запускается вне редактора внутри ue_export, поэтому вынесена и покрыта.
"""
import math
import os

import pytest

import convert
import level_export as lx


# ----------------------------------------------------------------------------
# Обнаружение .umap на диске (без запуска Unreal)
# ----------------------------------------------------------------------------

def test_list_umap_files_finds_all_levels(tmp_path):
    content = tmp_path / "Content"
    (content / "Maps").mkdir(parents=True)
    (content / "Sub" / "Deep").mkdir(parents=True)
    for rel in ("Maps/Level_A.umap", "Maps/Level_B.umap", "Sub/Deep/Level_C.umap",
                "Maps/NotALevel.uasset"):
        (content / rel).write_text("x", encoding="utf-8")
    # Служебные папки Content в список не попадают.
    (content / "__ExternalActors__").mkdir()
    (content / "__ExternalActors__" / "Hidden.umap").write_text("x", encoding="utf-8")

    config = {"paths": {"ue_project": str(tmp_path / "Project.uproject")}}
    levels = convert.list_umap_files(config)
    assert levels == ["/Game/Maps/Level_A", "/Game/Maps/Level_B", "/Game/Sub/Deep/Level_C"]


# ----------------------------------------------------------------------------
# Трансформы и мелкие преобразования
# ----------------------------------------------------------------------------

def test_make_transform_keeps_raw_ue_values():
    tr = lx.make_transform([100, 200, 300], [0, 0, 0, 1], [1, 2, 3])
    # Координаты НЕ конвертируются здесь — это делает C#. Значения как есть.
    assert tr["locationCm"] == [100.0, 200.0, 300.0]
    assert tr["rotationQuat"] == [0.0, 0.0, 0.0, 1.0]
    assert tr["scale"] == [1.0, 2.0, 3.0]


def test_cm_to_m():
    assert lx.cm_to_m(250) == 2.5
    assert lx.cm_to_m(None) is None


def test_rgb_pads_and_trims_alpha():
    assert lx.rgb([0.2, 0.4, 0.6, 1.0]) == [0.2, 0.4, 0.6]
    assert lx.rgb(None) == [1.0, 1.0, 1.0]


# ----------------------------------------------------------------------------
# Пересчёт интенсивности света
# ----------------------------------------------------------------------------

def test_directional_intensity_is_lux_passthrough():
    value, note = lx.convert_light_intensity("Directional", 3.14, "Lux", multiplier=1.0)
    assert value == pytest.approx(3.14)
    assert note is None


def test_lumens_to_candela_point_full_sphere():
    value, note = lx.convert_light_intensity("Point", 1256.6370614, "Lumens")
    # 1256.637 / (4*pi) ~= 100 кандел -> * CANDELA_TO_UNITY_INTENSITY.
    assert value == pytest.approx(100.0 * lx.CANDELA_TO_UNITY_INTENSITY, rel=1e-4)
    assert note == "post.level.light_lumens"


def test_lumens_to_candela_spot_uses_cone_solid_angle():
    # Прожектор концентрирует тот же поток в конус => кандел больше, чем по сфере.
    outer = 30.0
    value, _ = lx.convert_light_intensity("Spot", 1000.0, "Lumens", outer_cone_deg=outer)
    half = math.radians(outer)
    solid = 2.0 * math.pi * (1.0 - math.cos(half))
    assert value == pytest.approx((1000.0 / solid) * lx.CANDELA_TO_UNITY_INTENSITY)


def test_candelas_scaled_to_unity():
    value, note = lx.convert_light_intensity("Point", 850.0, "Candelas")
    assert value == pytest.approx(850.0 * lx.CANDELA_TO_UNITY_INTENSITY)
    assert note is None


def test_unknown_unit_passes_through_with_note():
    value, note = lx.convert_light_intensity("Point", 5.0, "EV")
    assert value == pytest.approx(5.0 * lx.CANDELA_TO_UNITY_INTENSITY)
    assert note == "post.level.light_unit_unknown"


def test_unitless_large_intensity_is_not_blown_out():
    # Регресс на выбеливание: сырые unitless-канделы (сотни тысяч) должны
    # сжиматься до потолка, а не уходить в кадр как есть.
    value, note = lx.convert_light_intensity("Point", 100000.0, "UNITLESS")
    assert value == pytest.approx(lx.UNITY_INTENSITY_CLAMP)
    assert note is None


def test_multiplier_applies():
    value, _ = lx.convert_light_intensity("Point", 100.0, "Candelas", multiplier=2.5)
    assert value == pytest.approx(100.0 * lx.CANDELA_TO_UNITY_INTENSITY * 2.5)


def test_missing_intensity_reports_note():
    value, note = lx.convert_light_intensity("Point", None, "Candelas")
    assert value is None
    assert note == "post.level.light_no_intensity"


# ----------------------------------------------------------------------------
# Сборка записи света
# ----------------------------------------------------------------------------

def _light_raw(**over):
    raw = {
        "id": "L1", "parentId": "A1",
        "componentType": "PointLightComponent",
        "transform": lx.make_transform([0, 0, 0], [0, 0, 0, 1], [1, 1, 1]),
        "color": [1.0, 0.9, 0.8, 1.0],
        "intensity": 100.0, "intensityUnit": "Candelas",
        "attenuationRadiusCm": 500.0,
        "castShadows": True, "visible": True, "mobility": "Movable",
        "temperature": 6500.0,
    }
    raw.update(over)
    return raw


def test_build_light_point():
    light = lx.build_light(_light_raw())
    assert light["unityType"] == "Point"
    assert light["range"] == 5.0                 # 500 см -> 5 м
    assert light["unityIntensity"] == pytest.approx(100.0 * lx.CANDELA_TO_UNITY_INTENSITY)
    assert light["lightmapMode"] == "Realtime"   # Movable
    assert light["color"] == [1.0, 0.9, 0.8]


def test_build_light_directional():
    light = lx.build_light(_light_raw(componentType="DirectionalLightComponent",
                                      intensity=3.0, intensityUnit="Lux",
                                      mobility="Stationary"))
    assert light["unityType"] == "Directional"
    assert light["unityIntensity"] == pytest.approx(3.0)
    assert light["lightmapMode"] == "Mixed"      # Stationary


def test_build_light_spot_doubles_cone_angles():
    # UE хранит половинные углы, Unity ждёт полные.
    light = lx.build_light(_light_raw(componentType="SpotLightComponent",
                                      outerConeDeg=22.0, innerConeDeg=10.0,
                                      mobility="Static"))
    assert light["unityType"] == "Spot"
    assert light["spotAngle"] == 44.0
    assert light["innerSpotAngle"] == 20.0
    assert light["lightmapMode"] == "Baked"      # Static


def test_build_light_area_size_cm_to_m():
    light = lx.build_light(_light_raw(componentType="RectLightComponent",
                                      sourceWidthCm=200.0, sourceHeightCm=100.0,
                                      intensityUnit="Lumens", intensity=1000.0))
    assert light["unityType"] == "Area"
    assert light["areaSize"] == [2.0, 1.0]


def test_build_light_records_conversion_note():
    light = lx.build_light(_light_raw(intensityUnit="Lumens", intensity=1256.6370614))
    keys = [n["reasonKey"] for n in light["notes"]]
    assert "post.level.light_lumens" in keys


# ----------------------------------------------------------------------------
# Сбор ассетов и дедупликация
# ----------------------------------------------------------------------------

def test_collect_seed_assets_dedupes_shared_mesh_and_overrides():
    objects = [
        {"mesh": "/Game/M/SM_Crate",
         "materialOverrides": [{"index": 0, "material": "/Game/M/MI_Red"}]},
        {"mesh": "/Game/M/SM_Crate",     # тот же меш у другого Actor
         "materialOverrides": [{"index": 0, "material": "/Game/M/MI_Blue"}]},
        {"mesh": "/Game/M/SM_Barrel", "materialOverrides": []},
    ]
    seeds = lx.collect_seed_assets(objects)
    assert seeds == {"/Game/M/SM_Crate", "/Game/M/SM_Barrel",
                     "/Game/M/MI_Red", "/Game/M/MI_Blue"}


def test_collect_seed_assets_ignores_empty():
    seeds = lx.collect_seed_assets([{"mesh": None, "materialOverrides": []}])
    assert seeds == set()


# ----------------------------------------------------------------------------
# Сборка уровня и проверка ссылок
# ----------------------------------------------------------------------------

def test_make_level_shape():
    level = lx.make_level("/Game/Maps/Level_A", "Level_A",
                          actors=[{"id": "A1"}], objects=[], lights=[], skipped=[])
    assert level["schemaVersion"] == lx.SCHEMA_VERSION
    assert level["uePath"] == "/Game/Maps/Level_A"
    assert level["name"] == "Level_A"
    assert level["actors"] == [{"id": "A1"}]


def test_validate_references_flags_missing_and_dedupes():
    level = {"objects": [
        {"mesh": "/Game/M/SM_Present", "materialOverrides": [
            {"index": 0, "material": "/Game/M/MI_Missing"}]},
        {"mesh": "/Game/M/SM_Absent", "materialOverrides": [
            {"index": 0, "material": "/Game/M/MI_Missing"}]},   # тот же пропуск
    ]}
    exported = {"/Game/M/SM_Present"}
    missing = lx.validate_references(level, exported)
    assert ("mesh", "/Game/M/SM_Absent") in missing
    assert ("material", "/Game/M/MI_Missing") in missing
    assert ("mesh", "/Game/M/SM_Present") not in missing
    # Дедуп: отсутствующий материал упомянут один раз, а не дважды.
    assert missing.count(("material", "/Game/M/MI_Missing")) == 1


def test_level_output_path():
    assert lx.level_output_path("/Game/Maps/Level_A", "Level_A") == "Levels/Maps/Level_A.json"
    assert lx.level_output_path("/Game/Level_Root", "Level_Root") == "Levels/Level_Root.json"
