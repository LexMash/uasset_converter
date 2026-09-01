# -*- coding: utf-8 -*-
"""
Проверка расширенного покрытия нод.

Здесь важно не совпадение с эталоном, а два утверждения: нода не оставляет
TODO (то есть действительно перенесена) и в коде появилось именно то, чего
от неё ждут.
"""
import pytest

import fixtures_nodes
import shader_gen


def build(name):
    graph = fixtures_nodes.ALL[name]()
    return shader_gen.generate_shader(graph, "UassetConverted/%s" % name,
                                      functions=fixtures_nodes.FUNCTIONS)


@pytest.mark.parametrize("name", sorted(fixtures_nodes.ALL))
def test_no_todos(name):
    """Ни одна нода из этого набора не должна считаться неподдержанной."""
    _text, ir = build(name)
    assert ir.todos == [], "%s оставил TODO: %s" % (name, ir.todos)


def test_math_functions_present():
    text, _ir = build("math_extended")
    for fragment in ("sin(", "cos(", "frac(", "floor(", "sign(", "sqrt(",
                     "min(", "max(", "fmod(", "step(", "smoothstep(", "dot("):
        assert fragment in text, "нет вызова %s" % fragment


def test_if_node_is_a_ternary():
    """If — это выбор из трёх веток, а не lerp: ветки не должны смешиваться."""
    text, _ir = build("math_extended")
    assert " ? " in text and " : " in text


def test_world_inputs_reach_the_struct():
    """
    Запрошенные входы обязаны появиться и в структуре, и в её заполнении,
    иначе шейдер не соберётся: поле есть в коде, но его никто не заполнил.
    """
    text, ir = build("world_inputs")
    assert "vertexColor" in ir.builtins
    assert "positionWS" in ir.builtins
    assert "time" in ir.builtins

    for field in ir.builtins:
        assert "            float" in text
        assert "%s;" % field in text
        assert "surfaceInputs.%s =" % field in text


def test_unused_inputs_do_not_appear():
    """Обратное тоже важно: неиспользованный вход не должен тащиться в шейдер."""
    text, ir = build("reroute_chain")
    assert ir.builtins == set()
    assert "positionWS;" not in text.split("struct SurfaceInputs")[1].split("};")[0]


def test_custom_expression_becomes_a_function():
    text, ir = build("custom_expression")
    assert "Checker" in ir.helpers
    assert "float3 Checker(float2 UV)" in text
    assert "return float3(UV.x, UV.y, UV.x * UV.y);" in text


def test_reroute_is_transparent():
    """Провод не создаёт ни переменной, ни записи в отчёте."""
    text, ir = build("reroute_chain")
    assert ir.todos == []
    assert "surface.albedo     = (_Tint).rgb;" in text


def test_four_uv_sets():
    text, ir = build("uv_sets")
    assert ir.max_uv == 3
    for index in range(4):
        assert "float2 uv%d;" % index in text
        assert "surfaceInputs.uv%d = input.uv%d;" % (index, index) in text
    # Пятого набора быть не должно — лишний интерполятор ничем не оплачен.
    assert "float2 uv4;" not in text


def test_material_function_is_inlined():
    """
    Функция материала разворачивается в тот же код, что и написанная руками
    математика: ни вызова несуществующей функции, ни TODO.
    """
    text, ir = build("material_function_inline")
    assert ir.todos == []
    assert "MF_Tint" not in text
    # Colour * Amount, где оба — свойства материала.
    assert "_Base" in text and "_Strength" in text
    assert "*" in text


def test_material_function_selects_requested_output():
    """Из функции с несколькими выходами берётся запрошенный, а не первый."""
    from graphbuilder import Graph

    def material(requested):
        g = Graph("/Game/Test/M_Sel")
        base = g.node("VectorParameter", parameter_name="Base",
                      default_value=[1.0, 0.5, 0.25, 1.0])
        call = g.node("MaterialFunctionCall", {"Colour": (base, "RGB")},
                      material_function="/Game/Test/MF_TwoOut")
        g.parameter("vectors", "Base", [1.0, 0.5, 0.25, 1.0])
        return g.connect("MP_BASE_COLOR", call, requested).build()

    straight, _ = shader_gen.generate_shader(
        material("Straight"), "UassetConverted/Straight",
        functions=fixtures_nodes.FUNCTIONS)
    inverted, _ = shader_gen.generate_shader(
        material("Inverted"), "UassetConverted/Inverted",
        functions=fixtures_nodes.FUNCTIONS)

    # Выход "Inverted" — это нода OneMinus (её эмит помечает код комментарием),
    # "Straight" — просто проброс входа без неё.
    assert "OneMinus" not in straight, "взят не тот выход: пришёл Inverted вместо Straight"
    assert "OneMinus" in inverted, "запрошенный выход Inverted не выбран"


def test_unknown_function_still_reports():
    """Функция, графа которой нет в манифесте, обязана остаться в отчёте."""
    graph = fixtures_nodes.material_function_inline()
    _text, ir = shader_gen.generate_shader(graph, "UassetConverted/X", functions={})
    assert any(todo["message_key"] == "shader.todo.function_not_ported"
               for todo in ir.todos)
