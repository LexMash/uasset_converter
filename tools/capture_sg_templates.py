# -*- coding: utf-8 -*-
"""
Снимает шаблоны узлов Shader Graph из эталонного .shadergraph.

Зачем. Формат .shadergraph недокументирован и версионно-зависим. Вместо того
чтобы собирать каждый узел вручную (и рисковать несовпадением полей при импорте
в Unity), backend_shadergraph клонирует готовые узлы, снятые с настоящего графа,
меняя только ObjectId, значения и рёбра. Этот инструмент и готовит такие шаблоны.

Эталон — граф, где на полотно вынесены все нужные ноды (можно несоединённые) и по
одному свойству каждого типа на Blackboard. Обновлять шаблоны при переезде на
новую версию Shader Graph: пересоздать эталон в новом Unity и прогнать этот скрипт.

    python tools/capture_sg_templates.py [reference.shadergraph]

Пишет shadergraph_templates.json рядом с backend_shadergraph.py.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REF = os.path.join(ROOT, "all_nodes_graph_lit.shadergraph")
OUT = os.path.join(ROOT, "shadergraph_templates.json")

# Ноды, которые умеет эмитить бэкенд. Короткое имя класса Shader Graph.
NEEDED_NODES = [
    # входы / константы
    "Vector1Node", "Vector2Node", "Vector3Node", "Vector4Node", "ColorNode",
    "TimeNode", "UVNode", "PositionNode", "NormalVectorNode", "ViewDirectionNode",
    "VertexColorNode", "ScreenPositionNode", "CameraNode", "FresnelNode", "SphereMaskNode",
    # текстуры
    "SampleTexture2DNode", "Texture2DAssetNode", "TilingAndOffsetNode",
    # арифметика
    "MultiplyNode", "AddNode", "SubtractNode", "DivideNode", "PowerNode", "OneMinusNode",
    "LerpNode", "AbsoluteNode", "SaturateNode", "FractionNode", "FloorNode", "CeilingNode",
    "SignNode", "SquareRootNode", "SineNode", "CosineNode", "TangentNode", "ArcsineNode",
    "ArccosineNode", "ArctangentNode", "Arctangent2Node", "NormalizeNode", "DDXNode", "DDYNode",
    "LogNode", "ExponentialNode", "MinimumNode", "MaximumNode", "ModuloNode", "CrossProductNode",
    "DotProductNode", "DistanceNode", "LengthNode", "StepNode", "SmoothstepNode", "ClampNode",
    "ReciprocalNode",
    # каналы
    "SwizzleNode", "SplitNode", "CombineNode", "AppendVectorNode",
    # нормали / гео
    "NormalReconstructZNode", "NormalUnpackNode", "ParallaxMappingNode", "RotateNode",
    # логика
    "BranchNode", "ComparisonNode",
    # утилиты
    "CustomFunctionNode", "PropertyNode", "KeywordNode",
]

SHORT = lambda t: t.split(".")[-1]


def parse_objects(text):
    """Файл .shadergraph — последовательность JSON-объектов через пустую строку."""
    return [json.loads(chunk) for chunk in re.split(r"\n\n(?=\{)", text.strip())]


def node_template(node, by_id):
    """Узел + его слоты как отдельные объекты (порядок слотов сохраняем)."""
    slots = [by_id[ref["m_Id"]] for ref in node.get("m_Slots", [])]
    return {"node": node, "slots": slots}


def main():
    ref = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REF
    text = open(ref, encoding="utf-8").read()
    objs = parse_objects(text)
    by_id = {o["m_ObjectId"]: o for o in objs if "m_ObjectId" in o}
    graph = objs[0]
    assert SHORT(graph["m_Type"]) == "GraphData", graph["m_Type"]

    result = {"sg_version": graph.get("m_SGVersion"), "source": os.path.basename(ref)}

    # --- оболочка GraphData: скалярные поля берём как есть, списки очищаем ---
    shell = {}
    for key, value in graph.items():
        if isinstance(value, list):
            shell[key] = []
        elif key in ("m_VertexContext", "m_FragmentContext"):
            shell[key] = {"m_Position": value["m_Position"], "m_Blocks": []}
        else:
            shell[key] = value
    result["graph_data_shell"] = shell

    # --- таргет и субтаргет ---
    target, subtarget = None, None
    for o in objs:
        s = SHORT(o.get("m_Type", ""))
        if s == "UniversalTarget":
            target = o
        elif s == "UniversalLitSubTarget":
            subtarget = o
    result["target"] = target
    result["subtarget"] = subtarget

    # --- блоки master stack: по дескриптору ---
    blocks = {}
    for o in objs:
        if SHORT(o.get("m_Type", "")) == "BlockNode":
            blocks[o["m_SerializedDescriptor"]] = node_template(o, by_id)
    result["blocks"] = blocks

    # --- узлы ---
    nodes = {}
    first_by_type = {}
    for o in objs:
        s = SHORT(o.get("m_Type", ""))
        if s in NEEDED_NODES and s not in first_by_type:
            first_by_type[s] = o
    for name in NEEDED_NODES:
        if name in first_by_type:
            nodes[name] = node_template(first_by_type[name], by_id)
        else:
            print("!! нет в эталоне:", name)
    result["nodes"] = nodes

    # --- свойства: PropertyNode + сама ShaderProperty, классифицируем по типу ---
    KIND = {
        "Vector1ShaderProperty": "float", "ColorShaderProperty": "color",
        "Texture2DShaderProperty": "texture", "BooleanShaderProperty": "boolean",
        "Vector2ShaderProperty": "vector2", "Vector3ShaderProperty": "vector3",
        "Vector4ShaderProperty": "vector4",
    }
    props = {}
    for o in objs:
        if SHORT(o.get("m_Type", "")) != "PropertyNode":
            continue
        prop = by_id.get(o["m_Property"]["m_Id"])
        if not prop:
            continue
        kind = KIND.get(SHORT(prop["m_Type"]))
        if kind and kind not in props:
            props[kind] = {"property": prop, **node_template(o, by_id)}
    result["properties"] = props

    # --- ключевое слово: ShaderKeyword + KeywordNode ---
    keyword_obj = next((o for o in objs if SHORT(o.get("m_Type", "")) == "ShaderKeyword"), None)
    kw_node = next((o for o in objs if SHORT(o.get("m_Type", "")) == "KeywordNode"), None)
    result["keyword"] = {"keyword": keyword_obj,
                         **(node_template(kw_node, by_id) if kw_node else {})}

    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(result, fh, indent=1, ensure_ascii=False)
    print("готово:", OUT)
    print("узлов:", len(nodes), "| блоков:", len(blocks), "| свойств:", list(props))


if __name__ == "__main__":
    main()
