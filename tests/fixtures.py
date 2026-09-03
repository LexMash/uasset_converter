# -*- coding: utf-8 -*-
"""
Набор графов, покрывающий каждую поддерживаемую ноду хотя бы одним тестом.

Это страховочная сетка для рефакторинга генератора: эталоны снимаются с
текущей реализации, и после перевода на IR вывод обязан совпасть побайтно.
"""
from graphbuilder import Graph


def pbr_basic():
    """Типовой PBR-мастер: цвет, нормаль, шероховатость, металл."""
    g = Graph("/Game/Test/M_PbrBasic")
    base = g.node("TextureSampleParameter2D", {"UVs": None},
                  parameter_name="BaseColor", sampler_type="SAMPLERTYPE_COLOR",
                  texture="/Game/Test/T_Base")
    normal = g.node("TextureSampleParameter2D", {"UVs": None},
                    parameter_name="Normal", sampler_type="SAMPLERTYPE_NORMAL",
                    texture="/Game/Test/T_Normal")
    tint = g.node("VectorParameter", parameter_name="Tint",
                  default_value=[1.0, 0.5, 0.25, 1.0])
    rough = g.node("ScalarParameter", parameter_name="Roughness", default_value=0.6)
    metal = g.node("ScalarParameter", parameter_name="Metallic", default_value=0.0)
    tinted = g.node("Multiply", {"A": (base, "RGB"), "B": (tint, "RGB")})
    g.parameter("textures", "BaseColor", "/Game/Test/T_Base")
    g.parameter("textures", "Normal", "/Game/Test/T_Normal")
    g.parameter("vectors", "Tint", [1.0, 0.5, 0.25, 1.0])
    g.parameter("scalars", "Roughness", 0.6)
    g.parameter("scalars", "Metallic", 0.0)
    return (g.connect("MP_BASE_COLOR", tinted)
             .connect("MP_NORMAL", normal, "RGB")
             .connect("MP_ROUGHNESS", rough)
             .connect("MP_METALLIC", metal)
             .build())


def math_ops():
    """Вся арифметика разом — проверяет приведение размерностей."""
    g = Graph("/Game/Test/M_MathOps")
    one = g.node("Constant", r=0.25)
    two = g.node("Constant2Vector", r=0.5, g=0.75)
    three = g.node("Constant3Vector", constant=[0.1, 0.2, 0.3, 1.0])
    four = g.node("Constant4Vector", constant=[0.1, 0.2, 0.3, 0.4])

    added = g.node("Add", {"A": three, "B": one})
    subbed = g.node("Subtract", {"A": added, "B": three})
    divided = g.node("Divide", {"A": subbed, "B": one})
    powed = g.node("Power", {"Base": divided, "Exp": None}, const_exponent=2.2)
    saturated = g.node("Saturate", {"": powed})
    absed = g.node("Abs", {"": saturated})
    inverted = g.node("OneMinus", {"": absed})
    clamped = g.node("Clamp", {"": inverted, "Min": None, "Max": None},
                     min_default=0.0, max_default=1.0)
    masked = g.node("ComponentMask", {"": clamped}, r=True, g=True, b=False, a=False)
    appended = g.node("AppendVector", {"A": masked, "B": one})
    lerped = g.node("LinearInterpolate", {"A": appended, "B": (four, "RGB"), "Alpha": None},
                    const_alpha=0.5)
    mixed = g.node("Multiply", {"A": lerped, "B": (two, "R")})
    return (g.connect("MP_BASE_COLOR", mixed)
             .connect("MP_ROUGHNESS", divided, "R")
             .build())


def static_switch():
    """StaticSwitch превращается в shader_feature, а не в рантайм-ветку."""
    g = Graph("/Game/Test/M_StaticSwitch")
    detail = g.node("TextureSampleParameter2D", {"UVs": None},
                    parameter_name="Detail Map", sampler_type="SAMPLERTYPE_COLOR",
                    texture="/Game/Test/T_Detail")
    plain = g.node("VectorParameter", parameter_name="Flat Color",
                   default_value=[0.8, 0.8, 0.8, 1.0])
    switch = g.node("StaticSwitchParameter",
                    {"True": (detail, "RGB"), "False": (plain, "RGB")},
                    parameter_name="Use Detail")
    g.parameter("switches", "Use Detail", False)
    g.parameter("textures", "Detail Map", "/Game/Test/T_Detail")
    g.parameter("vectors", "Flat Color", [0.8, 0.8, 0.8, 1.0])
    return g.connect("MP_BASE_COLOR", switch).build()


def masked_cutout():
    """BLEND_MASKED: альфа берётся из OpacityMask и режется clip()."""
    g = Graph("/Game/Test/M_Masked", blend_mode="BLEND_MASKED", two_sided=True,
              opacity_mask_clip_value=0.5)
    tex = g.node("TextureSampleParameter2D", {"UVs": None},
                 parameter_name="Albedo", sampler_type="SAMPLERTYPE_COLOR",
                 texture="/Game/Test/T_Leaf")
    g.parameter("textures", "Albedo", "/Game/Test/T_Leaf")
    return (g.connect("MP_BASE_COLOR", tex, "RGB")
             .connect("MP_OPACITY_MASK", tex, "A")
             .build())


def unlit_additive():
    """Unlit + аддитивный блендинг: весь цвет уходит в эмиссию."""
    g = Graph("/Game/Test/M_Glow", blend_mode="BLEND_ADDITIVE",
              shading_model="MSM_UNLIT")
    color = g.node("VectorParameter", parameter_name="Glow Color",
                   default_value=[4.0, 2.0, 0.5, 1.0])
    power = g.node("ScalarParameter", parameter_name="Opacity", default_value=0.8)
    g.parameter("vectors", "Glow Color", [4.0, 2.0, 0.5, 1.0])
    g.parameter("scalars", "Opacity", 0.8)
    return (g.connect("MP_EMISSIVE_COLOR", color, "RGB")
             .connect("MP_OPACITY", power)
             .build())


def engine_functions():
    """Перенесённые функции движка и нода Desaturation."""
    g = Graph("/Game/Test/M_EngineFuncs")
    base = g.node("TextureSampleParameter2D", {"UVs": None},
                  parameter_name="Base", sampler_type="SAMPLERTYPE_COLOR",
                  texture="/Game/Test/T_Base")
    n1 = g.node("TextureSampleParameter2D", {"UVs": None},
                parameter_name="NormalA", sampler_type="SAMPLERTYPE_NORMAL",
                texture="/Game/Test/T_NA")
    n2 = g.node("TextureSampleParameter2D", {"UVs": None},
                parameter_name="NormalB", sampler_type="SAMPLERTYPE_NORMAL",
                texture="/Game/Test/T_NB")
    contrast = g.node("MaterialFunctionCall", {"In": (base, "R"), "Contrast": None},
                      material_function="/Engine/Functions/Engine_MaterialFunctions01/"
                                        "ImageAdjustment/CheapContrast",
                      const_b=0.4)
    contrast_rgb = g.node("MaterialFunctionCall", {"In": (base, "RGB"), "Contrast": None},
                          material_function="/Engine/Functions/Engine_MaterialFunctions01/"
                                            "ImageAdjustment/CheapContrast_RGB",
                          const_b=0.2)
    blended = g.node("MaterialFunctionCall",
                     {"BaseNormal": (n1, "RGB"), "AdditionalNormal": (n2, "RGB")},
                     material_function="/Engine/Functions/Engine_MaterialFunctions02/"
                                       "Utility/BlendAngleCorrectedNormals")
    desat = g.node("Desaturation", {"": contrast_rgb, "Fraction": None},
                   const_b=0.3, luminance_factors=[0.3, 0.59, 0.11, 1.0])
    return (g.connect("MP_BASE_COLOR", desat)
             .connect("MP_NORMAL", blended)
             .connect("MP_ROUGHNESS", contrast)
             .build())


def uv_and_panner():
    """TextureCoordinate, Panner и выход за предел числа UV-наборов."""
    g = Graph("/Game/Test/M_Panner")
    uv0 = g.node("TextureCoordinate", coordinate_index=0, u_tiling=2.0, v_tiling=4.0)
    uv_far = g.node("TextureCoordinate", coordinate_index=3, u_tiling=1.0, v_tiling=1.0)
    panned = g.node("Panner", {"Coordinate": uv0, "Time": None},
                    speed_x=0.1, speed_y=-0.05)
    scroll = g.node("TextureSampleParameter2D", {"UVs": panned},
                    parameter_name="Scroll", sampler_type="SAMPLERTYPE_COLOR",
                    texture="/Game/Test/T_Scroll")
    far = g.node("TextureSampleParameter2D", {"UVs": uv_far},
                 parameter_name="Far", sampler_type="SAMPLERTYPE_COLOR",
                 texture="/Game/Test/T_Far")
    mixed = g.node("Add", {"A": (scroll, "RGB"), "B": (far, "RGB")})
    g.parameter("textures", "Scroll", "/Game/Test/T_Scroll")
    g.parameter("textures", "Far", "/Game/Test/T_Far")
    return g.connect("MP_BASE_COLOR", mixed).build()


def geometry_builtins():
    """BumpOffset, гео/камера-билтины и Round/Truncate — паритет SG-пути с HLSL."""
    g = Graph("/Game/Test/M_Builtins")
    uv0 = g.node("TextureCoordinate", coordinate_index=0, u_tiling=1.0, v_tiling=1.0)
    height = g.node("TextureSampleParameter2D", {"UVs": None},
                    parameter_name="Height", sampler_type="SAMPLERTYPE_GRAYSCALE",
                    texture="/Game/Test/T_Height")
    offset_uv = g.node("BumpOffset", {"Coordinate": uv0, "Height": (height, "R")},
                       height_ratio=0.08)
    albedo = g.node("TextureSampleParameter2D", {"UVs": offset_uv},
                    parameter_name="Albedo", sampler_type="SAMPLERTYPE_COLOR",
                    texture="/Game/Test/T_Albedo")
    g.parameter("textures", "Height", "/Game/Test/T_Height")
    g.parameter("textures", "Albedo", "/Game/Test/T_Albedo")

    # металл = round(TwoSidedSign * 0.5): Round + faceSign
    face = g.node("TwoSidedSign", {})
    face_half = g.node("Multiply", {"A": face, "B": None}, const_b=0.5)
    metal = g.node("Round", {"": face_half})

    # шероховатость = trunc(ObjectRadius): Truncate + objectRadius
    radius = g.node("ObjectRadius", {})
    rough = g.node("Truncate", {"": radius})

    # эмиссия сводит остальные билтины: camera/object position, scale, orientation, depth
    cam = g.node("CameraPositionWS", {})
    objp = g.node("ObjectPositionWS", {})
    scale = g.node("ObjectScale", {})
    orient = g.node("ObjectOrientation", {})
    depth = g.node("PixelDepth", {})
    e1 = g.node("Add", {"A": cam, "B": objp})
    e2 = g.node("Add", {"A": e1, "B": scale})
    e3 = g.node("Add", {"A": e2, "B": orient})
    emis = g.node("Multiply", {"A": e3, "B": depth})

    return (g.connect("MP_BASE_COLOR", albedo, "RGB")
             .connect("MP_METALLIC", metal)
             .connect("MP_ROUGHNESS", rough)
             .connect("MP_EMISSIVE_COLOR", emis)
             .build())


def unsupported_node():
    """Незнакомая нода: первый вход пробрасывается, в отчёт падает TODO."""
    g = Graph("/Game/Test/M_Unsupported")
    color = g.node("VectorParameter", parameter_name="Color",
                   default_value=[0.5, 0.5, 0.5, 1.0])
    weird = g.node("SomethingWeird", {"Input": (color, "RGB")})
    orphan = g.node("AlsoWeird", {})
    g.parameter("vectors", "Color", [0.5, 0.5, 0.5, 1.0])
    return (g.connect("MP_BASE_COLOR", weird)
             .connect("MP_AMBIENT_OCCLUSION", orphan)
             .build())


def dropped_parameters():
    """Параметр, который питает только Specular — в URP такого входа нет."""
    g = Graph("/Game/Test/M_Dropped")
    color = g.node("VectorParameter", parameter_name="Base Color",
                   default_value=[0.3, 0.3, 0.3, 1.0])
    spec = g.node("ScalarParameter", parameter_name="Specular Level", default_value=0.5)
    g.parameter("vectors", "Base Color", [0.3, 0.3, 0.3, 1.0])
    g.parameter("scalars", "Specular Level", 0.5)
    g.parameter("scalars", "Never Used", 1.0)
    return (g.connect("MP_BASE_COLOR", color, "RGB")
             .connect("MP_SPECULAR", spec)
             .build())


ALL = {
    "pbr_basic": pbr_basic,
    "math_ops": math_ops,
    "static_switch": static_switch,
    "masked_cutout": masked_cutout,
    "unlit_additive": unlit_additive,
    "engine_functions": engine_functions,
    "uv_and_panner": uv_and_panner,
    "geometry_builtins": geometry_builtins,
    "unsupported_node": unsupported_node,
    "dropped_parameters": dropped_parameters,
}
