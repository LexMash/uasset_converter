# -*- coding: utf-8 -*-
"""
Графы на расширенное покрытие нод.

Отдельно от fixtures.py: там страховочная сетка для рефакторинга (её эталоны
менять нельзя без причины), здесь — проверка того, что новая нода вообще
переносится и во что именно.
"""
from graphbuilder import Graph


def math_extended():
    """Тригонометрия, округления, min/max, dot, step и прочая математика."""
    g = Graph("/Game/Test/M_MathExtended")
    uv = g.node("TextureCoordinate", coordinate_index=0, u_tiling=1.0, v_tiling=1.0)
    x = g.node("ComponentMask", {"": uv}, r=True, g=False, b=False, a=False)

    sine = g.node("Sine", {"": x})
    cosine = g.node("Cosine", {"": x})
    frac = g.node("Frac", {"": sine})
    floor = g.node("Floor", {"": cosine})
    sign = g.node("Sign", {"": floor})
    sqrt = g.node("SquareRoot", {"": frac})
    smallest = g.node("Min", {"A": sqrt, "B": sign})
    largest = g.node("Max", {"A": smallest, "B": None}, const_b=0.1)
    modulo = g.node("Fmod", {"A": largest, "B": None}, const_b=0.5)
    stepped = g.node("Step", {"Y": modulo, "X": None}, const_b=0.25)
    smooth = g.node("SmoothStep", {"Min": None, "Max": None, "Value": stepped},
                    const_min=0.0, const_max=1.0)

    colour = g.node("Constant3Vector", constant=[0.9, 0.4, 0.1, 1.0])
    product = g.node("Dot", {"A": colour, "B": colour})
    picked = g.node("If", {"A": smooth, "B": None, "A > B": colour,
                           "A < B": None, "A == B": None},
                    const_b=0.5, const_a_less_than_b=0.0)
    return (g.connect("MP_BASE_COLOR", picked)
             .connect("MP_ROUGHNESS", product)
             .connect("MP_METALLIC", smooth)
             .build())


def world_inputs():
    """Входы вершины и мира: цвет вершин, нормаль, позиция, время, Fresnel."""
    g = Graph("/Game/Test/M_WorldInputs")
    colour = g.node("VertexColor")
    position = g.node("WorldPosition")
    time = g.node("Time")
    fresnel = g.node("Fresnel", {"ExponentIn": None, "BaseReflectFractionIn": None,
                                 "Normal": None},
                     exponent=4.0, base_reflect_fraction=0.02)
    mask = g.node("SphereMask", {"A": position, "B": None, "Radius": None, "Hardness": None},
                  const_b=[0.0, 0.0, 0.0], attenuation_radius=500.0, hardness_percent=0.4)
    animated = g.node("Multiply", {"A": (colour, "RGB"), "B": time})
    lit = g.node("LinearInterpolate", {"A": animated, "B": None, "Alpha": fresnel},
                 const_b=[1.0, 1.0, 1.0])
    return (g.connect("MP_BASE_COLOR", lit)
             .connect("MP_OPACITY", mask)
             .build())


def custom_expression():
    """Нода Custom переносится куском HLSL как есть."""
    g = Graph("/Game/Test/M_Custom")
    uv = g.node("TextureCoordinate", coordinate_index=0, u_tiling=1.0, v_tiling=1.0)
    custom = g.node("CustomExpression", {"UV": uv},
                    code="return float3(UV.x, UV.y, UV.x * UV.y);",
                    output_type="CMOT_FLOAT3",
                    description="Checker")
    return g.connect("MP_BASE_COLOR", custom).build()


def reroute_chain():
    """Провода не должны попадать в отчёт как неподдержанные ноды."""
    g = Graph("/Game/Test/M_Reroute")
    colour = g.node("VectorParameter", parameter_name="Tint",
                    default_value=[0.2, 0.4, 0.6, 1.0])
    first = g.node("Reroute", {"": (colour, "RGB")})
    second = g.node("NamedRerouteUsage", {"": first})
    g.parameter("vectors", "Tint", [0.2, 0.4, 0.6, 1.0])
    return g.connect("MP_BASE_COLOR", second).build()


def uv_sets():
    """Четыре набора UV: раньше выше второго граф упирался в TODO."""
    g = Graph("/Game/Test/M_UvSets")
    total = None
    for index in range(4):
        uv = g.node("TextureCoordinate", coordinate_index=index,
                    u_tiling=1.0, v_tiling=1.0)
        sample = g.node("TextureSampleParameter2D", {"UVs": uv},
                        parameter_name="Layer%d" % index,
                        sampler_type="SAMPLERTYPE_COLOR",
                        texture="/Game/Test/T_Layer%d" % index)
        g.parameter("textures", "Layer%d" % index, "/Game/Test/T_Layer%d" % index)
        rgb = (sample, "RGB")
        total = rgb if total is None else g.node("Add", {"A": total, "B": rgb})
    return g.connect("MP_BASE_COLOR", total).build()


# Граф функции материала — как его дампит ue_export в раздел material_functions.
def _tint_function():
    f = Graph("/Game/Test/MF_Tint")
    colour = f.node("FunctionInput", input_name="Colour", input_type="FunctionInput_Vector3")
    amount = f.node("FunctionInput", input_name="Amount", input_type="FunctionInput_Scalar")
    scaled = f.node("Multiply", {"A": colour, "B": amount})
    f.node("FunctionOutput", {"": scaled}, output_name="Result")
    return f.build()


def two_output_function():
    """Функция с двумя выходами: прямой проброс и инверсия одного входа."""
    f = Graph("/Game/Test/MF_TwoOut")
    colour = f.node("FunctionInput", input_name="Colour", input_type="FunctionInput_Vector3")
    inverted = f.node("OneMinus", {"": colour})
    f.node("FunctionOutput", {"": colour}, output_name="Straight")
    f.node("FunctionOutput", {"": inverted}, output_name="Inverted")
    return f.build()


def material_function_inline():
    """Любая функция материала инлайнится, а не превращается в TODO."""
    g = Graph("/Game/Test/M_FunctionCall")
    base = g.node("VectorParameter", parameter_name="Base",
                  default_value=[1.0, 0.5, 0.25, 1.0])
    strength = g.node("ScalarParameter", parameter_name="Strength", default_value=0.75)
    call = g.node("MaterialFunctionCall", {"Colour": (base, "RGB"), "Amount": strength},
                  material_function="/Game/Test/MF_Tint")
    g.parameter("vectors", "Base", [1.0, 0.5, 0.25, 1.0])
    g.parameter("scalars", "Strength", 0.75)
    return g.connect("MP_BASE_COLOR", call).build()


FUNCTIONS = {"/Game/Test/MF_Tint": _tint_function(),
             "/Game/Test/MF_TwoOut": two_output_function()}

ALL = {
    "math_extended": math_extended,
    "world_inputs": world_inputs,
    "custom_expression": custom_expression,
    "reroute_chain": reroute_chain,
    "uv_sets": uv_sets,
    "material_function_inline": material_function_inline,
}
