# -*- coding: utf-8 -*-
"""
Golden-тесты генератора Shader Graph — второй путь материалов.

Как и у HLSL-пути: смысл не в том, что эталон «правильный», а в том, что он НЕ
МЕНЯЕТСЯ. Осознанное изменение фиксируется перегенерацией:

    UAC_REGEN=1 python -m pytest tests/test_shadergraph_golden.py

Дополнительно проверяется структурная целостность графа (все рёбра и ссылки
указывают на существующие объекты и слоты нужного направления) — это и есть та
проверка, которую иначе сделал бы только импорт в Unity.
"""
import io
import json
import os

import pytest

import fixtures
import shadergraph_gen

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
REGEN = os.environ.get("UAC_REGEN") == "1"


def _silent(*_args, **_kwargs):
    pass


def _objects(text):
    return [json.loads(chunk) for chunk in text.strip().split("\n\n")]


@pytest.mark.parametrize("name", sorted(fixtures.ALL))
def test_shadergraph_matches_golden(name):
    graph = fixtures.ALL[name]()
    text, _ir = shadergraph_gen.generate_shadergraph(graph, _silent)

    path = os.path.join(GOLDEN_DIR, name + ".shadergraph")
    if REGEN or not os.path.isfile(path):
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        if not REGEN:
            pytest.skip("эталон %s.shadergraph создан заново — перепроверь его" % name)
        return

    with io.open(path, encoding="utf-8", newline="") as fh:
        expected = fh.read()
    assert text == expected


@pytest.mark.parametrize("name", sorted(fixtures.ALL))
def test_shadergraph_is_structurally_valid(name):
    """Каждое ребро и ссылка ведут к существующему объекту и слоту верного типа."""
    graph = fixtures.ALL[name]()
    text, _ir = shadergraph_gen.generate_shadergraph(graph, _silent)
    objs = _objects(text)

    ids = [o["m_ObjectId"] for o in objs]
    assert len(ids) == len(set(ids)), "дублирующиеся ObjectId"
    by_id = {o["m_ObjectId"]: o for o in objs}
    graph_data = objs[0]
    assert graph_data["m_Type"].endswith("GraphData")

    def slots_of(node):
        result = {}
        for ref in node.get("m_Slots", []):
            slot = by_id[ref["m_Id"]]           # KeyError = битая ссылка на слот
            result[slot["m_Id"]] = slot["m_SlotType"]
        return result

    for ref in graph_data["m_Properties"] + graph_data["m_Nodes"]:
        assert ref["m_Id"] in by_id
    for context in ("m_VertexContext", "m_FragmentContext"):
        for ref in graph_data[context]["m_Blocks"]:
            assert "BlockNode" in by_id[ref["m_Id"]]["m_Type"]
    assert graph_data["m_ActiveTargets"][0]["m_Id"] in by_id

    seen_inputs = set()
    for edge in graph_data["m_Edges"]:
        out_ref, in_ref = edge["m_OutputSlot"], edge["m_InputSlot"]
        out_node, in_node = by_id[out_ref["m_Node"]["m_Id"]], by_id[in_ref["m_Node"]["m_Id"]]
        assert slots_of(out_node).get(out_ref["m_SlotId"]) == 1, "выход не найден/не выход"
        assert slots_of(in_node).get(in_ref["m_SlotId"]) == 0, "вход не найден/не вход"
        key = (in_ref["m_Node"]["m_Id"], in_ref["m_SlotId"])
        assert key not in seen_inputs, "во вход тянется больше одного ребра"
        seen_inputs.add(key)
