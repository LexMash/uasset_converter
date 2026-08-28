# -*- coding: utf-8 -*-
"""
Golden-тесты генератора шейдеров.

Смысл — не в том, что эталон «правильный», а в том, что он НЕ МЕНЯЕТСЯ.
Рефакторинг генератора обязан оставить вывод побайтно тем же; осознанное
изменение поведения фиксируется перегенерацией эталонов:

    UAC_REGEN=1 python -m pytest tests/test_shader_golden.py

и диффом в ревью видно ровно то, что изменилось в шейдерах.
"""
import io
import os

import pytest

import fixtures
import shader_gen

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
REGEN = os.environ.get("UAC_REGEN") == "1"


def _silent(*_args, **_kwargs):
    pass


@pytest.mark.parametrize("name", sorted(fixtures.ALL))
def test_shader_matches_golden(name):
    graph = fixtures.ALL[name]()
    text, builder = shader_gen.generate_shader(graph, "UassetConverted/%s" % name, _silent)

    path = os.path.join(GOLDEN_DIR, name + ".shader")
    if REGEN or not os.path.isfile(path):
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        if not REGEN:
            pytest.skip("эталон %s.shader создан заново — перепроверь его глазами" % name)
        return

    with io.open(path, encoding="utf-8", newline="") as fh:
        expected = fh.read()
    assert text == expected

    # Сам факт наличия свойств проверяем отдельно: пустой блок Properties —
    # это почти всегда потерянные параметры, а не корректный шейдер.
    if graph["parameters"]["scalars"] or graph["parameters"]["vectors"]:
        assert builder.properties, "у %s не осталось ни одного свойства" % name


def test_unsupported_node_reports_todo():
    """Незнакомая нода обязана оставить след в отчёте, а не тихо исчезнуть."""
    graph = fixtures.unsupported_node()
    _text, builder = shader_gen.generate_shader(graph, "UassetConverted/X", _silent)
    types = {todo["type"] for todo in builder.todos}
    assert types == {"SomethingWeird", "AlsoWeird"}


def test_dropped_parameters_are_listed():
    """Параметр без входа в URP считается отброшенным, а не потерянным молча."""
    graph = fixtures.dropped_parameters()
    _text, builder = shader_gen.generate_shader(graph, "UassetConverted/X", _silent)
    assert "Specular Level" in builder.dropped_parameters
    assert "Never Used" in builder.dropped_parameters
    assert "Base Color" not in builder.dropped_parameters


def test_texture_sampled_once_per_uv():
    """
    Одна и та же текстура по одним и тем же UV не должна семплиться дважды:
    лишний семпл — это лишняя выборка из памяти в каждом пикселе.
    """
    graph = fixtures.masked_cutout()
    text, _builder = shader_gen.generate_shader(graph, "UassetConverted/X", _silent)
    assert text.count("SAMPLE_TEXTURE2D(_Albedo") == 1
