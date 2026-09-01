# -*- coding: utf-8 -*-
"""
Чистая логика конвертации Unreal Level -> Unity Scene.

Модуль сознательно НЕ импортирует `unreal`: всё, что здесь есть, работает на
простых dict и покрыто тестами. ue_export.py извлекает сырые данные из объектов
движка в такие dict и кормит ими эти функции, а сам остаётся тонкой обёрткой
над `unreal`, которую вне редактора не запустить.

Что здесь считается:
  * маппинг типов и mobility источников света Unreal -> Unity;
  * приблизительный пересчёт интенсивности света (light-type-aware);
  * упаковка трансформов в сырых значениях UE (конвертация координат — в C#);
  * сбор набора ассетов, на которые ссылается уровень (для дедупликации);
  * сборка level-JSON и проверка ссылок на неэкспортированные ассеты.

Координаты и вращения НЕ конвертируются здесь: level-JSON хранит исходные
значения UE, а перевод в оси и метры Unity делает C#-сторона единой формулой —
так конвертация живёт в одном месте и её нельзя рассинхронизировать.
"""
import math

# Версия формата level-JSON. C#-сторона сверяет её и понимает, что читает.
SCHEMA_VERSION = 1

# Соответствие типов компонентов света Unreal и типов Light в Unity.
UNREAL_TO_UNITY_LIGHT = {
    "DirectionalLightComponent": "Directional",
    "PointLightComponent": "Point",
    "SpotLightComponent": "Spot",
    "RectLightComponent": "Area",
}

# Mobility Unreal -> режим запекания Unity. Static печётся целиком, Stationary
# смешанный (прямой свет реального времени + запечённые отражённые), Movable
# полностью реального времени.
MOBILITY_TO_LIGHTMAP = {
    "Static": "Baked",
    "Stationary": "Mixed",
    "Movable": "Realtime",
}

# Приблизительный перевод физической силы света (кандела) Unreal в условную
# интенсивность встроенной Unity-лампы. У URP/встроенного Light нет авто-
# экспозиции, поэтому сырые канделы Unreal (тысячи–сотни тысяч) выбеливают весь
# кадр. Коэффициент подобран так, чтобы типичная лампа сцены попадала в район
# 1–5; тонкую настройку оставляем за unity.light_intensity_multiplier. Значения
# ошибкоёмки — сверять визуально (RenderCheck) и при нужде править здесь/множителем.
CANDELA_TO_UNITY_INTENSITY = 0.001
# Потолок, чтобы самые яркие источники (десятки–сотни тысяч кандел) не выжигали
# сцену целиком при отсутствии экспозиции.
UNITY_INTENSITY_CLAMP = 25.0


# ----------------------------------------------------------------------------
# Мелкие преобразования величин
# ----------------------------------------------------------------------------

def cm_to_m(value):
    """Сантиметры UE -> метры Unity. None остаётся None."""
    if value is None:
        return None
    return float(value) / 100.0


def rgb(color):
    """Цвет в [r, g, b]. Принимает список/кортеж любой длины; альфу отбрасываем."""
    if not color:
        return [1.0, 1.0, 1.0]
    values = [float(c) for c in color[:3]]
    while len(values) < 3:
        values.append(0.0)
    return values


def make_transform(location_cm, rotation_quat, scale):
    """
    Трансформ в сырых значениях UE. location в сантиметрах, вращение —
    кватернионом [x, y, z, w], масштаб безразмерный. Всё как есть: перевод в
    оси Unity и в метры делает C#.
    """
    return {
        "locationCm": [float(v) for v in location_cm],
        "rotationQuat": [float(v) for v in rotation_quat],
        "scale": [float(v) for v in scale],
    }


# ----------------------------------------------------------------------------
# Свет
# ----------------------------------------------------------------------------

def convert_light_intensity(unity_type, intensity, unit, multiplier=1.0,
                            outer_cone_deg=None):
    """
    Приблизительный пересчёт интенсивности источника из единиц UE в число Unity.

    Возвращает (значение, ключ_заметки | None). Заметка — это ключ локализации,
    которым постобработка объяснит в отчёте, что и как было пересчитано: точного
    соответствия между фотометрией UE и условными единицами Unity нет.

    Логика зависит от типа и единиц:
      * направленный свет UE задаётся в люксах и берётся Unity напрямую;
      * остальные типы приводятся к канделам (люмены — по телесному углу: полная
        сфера для точечного/площадного, конус — для прожектора; канделы и
        unitless — как есть), затем к условной интенсивности Unity через
        CANDELA_TO_UNITY_INTENSITY с потолком UNITY_INTENSITY_CLAMP.
    Общий множитель unity.light_intensity_multiplier применяется поверх всегда.
    """
    if intensity is None:
        return None, "post.level.light_no_intensity"

    unit_l = (unit or "").strip().lower()

    # Направленный свет UE в люксах близок к интенсивности Unity — берём напрямую,
    # candela-масштаб к нему не применяем.
    if unity_type == "Directional":
        return intensity * multiplier, None

    # Прочие типы: сначала приводим к канделам.
    note = None
    if unit_l == "lumens":
        if unity_type == "Spot" and outer_cone_deg:
            half = math.radians(min(max(float(outer_cone_deg), 0.0), 180.0))
            solid = 2.0 * math.pi * (1.0 - math.cos(half))
            candela = intensity / solid if solid > 1e-6 else intensity / (4.0 * math.pi)
        else:
            # Точечный и площадной — по полной сфере.
            candela = intensity / (4.0 * math.pi)
        note = "post.level.light_lumens"
    elif unit_l == "candelas":
        candela = intensity
    elif unit_l in ("", "unitless"):
        candela = intensity
    else:
        # EV или незнакомая единица: честно берём число как есть и предупреждаем.
        candela = intensity
        note = "post.level.light_unit_unknown"

    # Канделы -> условная интенсивность Unity, с потолком против выбеливания.
    value = candela * CANDELA_TO_UNITY_INTENSITY * multiplier
    if value > UNITY_INTENSITY_CLAMP:
        value = UNITY_INTENSITY_CLAMP
    return value, note


def build_light(raw, multiplier=1.0):
    """
    Собирает запись света для level-JSON из сырых данных компонента.

    raw — dict с полями, извлечёнными из объекта unreal (см. ue_export):
        id, parentId, componentType, transform, color,
        intensity, intensityUnit, attenuationRadiusCm,
        outerConeDeg, innerConeDeg, sourceWidthCm, sourceHeightCm,
        castShadows, visible, mobility, temperature,
        notes (список {reasonKey, reasonArgs} — уже собранные предупреждения).

    Половинные углы конуса UE разворачиваются в полные углы Unity (× 2).
    Информационные поля (unrealType, intensity, intensityUnit, notes) C# просто
    игнорирует — JsonUtility читает лишь объявленные им поля.
    """
    comp = raw.get("componentType")
    unity_type = UNREAL_TO_UNITY_LIGHT.get(comp, "Point")
    notes = list(raw.get("notes") or [])

    value, note_key = convert_light_intensity(
        unity_type, raw.get("intensity"), raw.get("intensityUnit"),
        multiplier, raw.get("outerConeDeg"))
    if note_key:
        notes.append({"reasonKey": note_key, "reasonArgs": {}})

    is_spot = unity_type == "Spot"
    is_area = unity_type == "Area"
    return {
        "id": raw.get("id"),
        "parentId": raw.get("parentId"),
        "unrealType": comp,
        "unityType": unity_type,
        "transform": raw.get("transform"),
        "color": rgb(raw.get("color")),
        "intensity": raw.get("intensity"),
        "intensityUnit": raw.get("intensityUnit"),
        "intensityMultiplier": float(multiplier),
        "unityIntensity": value,
        "range": cm_to_m(raw.get("attenuationRadiusCm")) or 0.0,
        "spotAngle": float(raw.get("outerConeDeg") or 0.0) * 2.0 if is_spot else 0.0,
        "innerSpotAngle": float(raw.get("innerConeDeg") or 0.0) * 2.0 if is_spot else 0.0,
        "areaSize": [cm_to_m(raw.get("sourceWidthCm")) or 0.0,
                     cm_to_m(raw.get("sourceHeightCm")) or 0.0] if is_area else [0.0, 0.0],
        "castShadows": bool(raw.get("castShadows", True)),
        "visible": bool(raw.get("visible", True)),
        "mobility": raw.get("mobility"),
        "lightmapMode": MOBILITY_TO_LIGHTMAP.get(raw.get("mobility"), "Realtime"),
        "temperature": raw.get("temperature"),
        "notes": notes,
    }


# ----------------------------------------------------------------------------
# Ассеты уровня и сборка
# ----------------------------------------------------------------------------

def collect_seed_assets(objects):
    """
    Набор UE-путей ассетов, на которые прямо ссылается уровень: меши и
    материалы-переопределения. Дефолтные материалы и текстуры добираются потом
    замыканием зависимостей в самом ue_export — их знает только Asset Registry.

    Возвращает set (дедупликация по пути — сама суть: один и тот же меш из
    десятка Actor попадёт в экспорт единожды).
    """
    seeds = set()
    for obj in objects:
        mesh = obj.get("mesh")
        if mesh:
            seeds.add(mesh)
        for override in obj.get("materialOverrides") or []:
            material = override.get("material") if isinstance(override, dict) else override
            if material:
                seeds.add(material)
    return seeds


def make_level(ue_path, name, actors, objects, lights, skipped):
    """Собирает структуру level-JSON. Плоская, без словарей — дружит с C#."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "uePath": ue_path,
        "name": name,
        "actors": list(actors),
        "objects": list(objects),
        "lights": list(lights),
        "skipped": list(skipped),
    }


def validate_references(level, exported_paths):
    """
    Ссылки уровня на ассеты, которых нет среди экспортированных.

    Возвращает список (kind, path): kind — "mesh" или "material". Дубликаты
    схлопываются: одна и та же отсутствующая текстура из ста объектов — одна
    строка в отчёте, а не сто.
    """
    exported = set(exported_paths)
    missing = []
    seen = set()

    def check(kind, path):
        if path and path not in exported and (kind, path) not in seen:
            seen.add((kind, path))
            missing.append((kind, path))

    for obj in level.get("objects") or []:
        check("mesh", obj.get("mesh"))
        for override in obj.get("materialOverrides") or []:
            material = override.get("material") if isinstance(override, dict) else override
            check("material", material)
    return missing


def level_output_path(ue_path, name):
    """
    Относительный путь level-JSON внутри output: Levels/<UE-путь>/<Имя>.json.
    /Game/Maps/Level_A -> Levels/Maps/Level_A.json.
    """
    rel = ue_path[len("/Game/"):] if ue_path.startswith("/Game/") else ue_path.lstrip("/")
    parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
    if parent:
        return "Levels/%s/%s.json" % (parent, name)
    return "Levels/%s.json" % name
