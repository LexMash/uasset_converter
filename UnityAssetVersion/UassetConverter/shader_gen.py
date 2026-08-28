#!/usr/bin/env python3
"""
Слой 2: граф мастер-материала Unreal -> шейдер Unity.

Сам ничего не транспилирует: обход графа живёт в graph_ir, рендер HLSL — в
backend_hlsl. Здесь только оркестровка — прочитать манифест, схлопнуть
одинаковые графы, записать файлы и индекс.

Форматов вывода два, и оба берутся из одного IR:
  * .shader  — готовый HLSL, пишется прямо здесь;
  * graph_ir.json — нейтральное описание, из которого редакторная часть в
    Unity собирает .shadergraph под ту версию Shader Graph, что стоит в проекте.

Незнакомые ноды не выдумываются: они попадают в отчёт, а в коде остаётся
пометка TODO рядом с проброшенным первым входом.
"""
import argparse
import json
import os
import sys

import backend_hlsl
from graph_ir import build_ir, sanitize   # noqa: F401  (sanitize импортирует postprocess)
from i18n import t, set_language


def graph_signature(graph):
    """
    Отпечаток структуры графа без учёта того, в каком паке он лежит.
    Одноимённые мастера из разных паков ModSci чаще всего идентичны — тогда
    хватит одного шейдера на всех.
    """
    payload = {
        "nodes": [{"type": n["type"], "props": n["props"],
                   "inputs": [(i["name"], i["index"], i["from"], i["from_output"])
                              for i in n["inputs"]]}
                  for n in graph["nodes"]],
        "outputs": graph["outputs"],
        "blend_mode": graph.get("blend_mode"),
        "shading_model": graph.get("shading_model"),
        "two_sided": graph.get("two_sided"),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def generate_shader(graph, shader_name, log=None, functions=None):
    """
    Совместимая точка входа: граф -> (текст шейдера, IR).

    IR отдаётся наружу, потому что вызывающему нужны свойства, ключевые слова,
    TODO и отброшенные параметры — всё это считается ровно один раз, при обходе.
    """
    ir = build_ir(graph, functions, log)
    return backend_hlsl.generate(ir, shader_name), ir


def main():
    parser = argparse.ArgumentParser(
        description="Transpile Unreal master materials into Unity shaders")
    parser.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                         "config.json"))
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as fh:
        config = json.load(fh)
    set_language(config.get("language") or "auto")

    out_dir = config["paths"]["output_dir"]
    manifest_path = os.path.join(out_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        print(t("post.no_manifest", path=manifest_path))
        return 1

    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    graphs = manifest.get("material_graphs") or []
    if not graphs:
        print(t("shader.no_graphs"))
        return 0

    # Графы функций материалов лежат в манифесте отдельным разделом: одна и та
    # же функция вызывается из десятков мастеров, дублировать её незачем.
    functions = {entry["ue_path"]: entry
                 for entry in (manifest.get("material_functions") or [])}

    namespace = config["shader_gen"].get("namespace", "UassetConverted")
    output_mode = config["shader_gen"].get("output", "hlsl")
    pipeline = config.get("pipeline", "urp")
    write_hlsl = output_mode in ("hlsl", "both")

    # Рукописный Lit-шейдер для HDRP невозможен: его проходы и их порядок —
    # внутреннее дело пайплайна, и поддерживаемого способа написать такой
    # шейдер вручную у Unity нет. Для HDRP путь один — Shader Graph, и IR
    # для него всё равно пишется ниже.
    if pipeline == "hdrp" and write_hlsl:
        print(t("shader.hdrp_needs_shadergraph"))
        write_hlsl = False

    shaders_dir = os.path.join(out_dir, "Shaders")
    os.makedirs(shaders_dir, exist_ok=True)

    by_signature = {}     # отпечаток -> имя шейдера
    used_names = {}       # имя -> отпечаток
    results, all_todos, ir_graphs = [], [], []

    for graph in sorted(graphs, key=lambda g: g["ue_path"]):
        name = graph["ue_path"].rsplit("/", 1)[-1]
        signature = graph_signature(graph)

        if signature in by_signature:
            results.append({"ue_path": graph["ue_path"], "shader": by_signature[signature],
                            "reused": True})
            print("= %-28s -> %s" % (name, t("shader.reused", shader=by_signature[signature])))
            continue

        # Одноимённые мастера с РАЗНЫМИ графами обязаны получить разные имена,
        # иначе Shader.Find в Unity вернёт случайный из них.
        unique = name
        if name in used_names:
            pack = graph["ue_path"].split("/")[2]
            unique = "%s_%s" % (name, pack)
        shader_name = "%s/%s" % (namespace, unique)

        text, ir = generate_shader(graph, shader_name, print, functions)
        if write_hlsl:
            path = os.path.join(shaders_dir, unique + ".shader")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        ir_graphs.append(ir.to_json(shader_name, config.get("pipeline", "urp")))

        by_signature[signature] = shader_name
        used_names[unique] = signature
        results.append({"ue_path": graph["ue_path"], "shader": shader_name,
                        "file": "Shaders/%s.shader" % unique if write_hlsl else "",
                        "properties": dict(ir.properties),
                        "keywords": dict(ir.keywords),
                        "dropped_parameters": ir.dropped_parameters,
                        "todos": ir.todos, "reused": False})
        for todo in ir.todos:
            all_todos.append(dict(todo, material=graph["ue_path"]))

        flag = "  " + t("shader.todo_count", count=len(ir.todos)) if ir.todos else ""
        if ir.dropped_parameters:
            flag += "  " + t("shader.no_unity_equivalent",
                             names=", ".join(ir.dropped_parameters))
        print("+ %-28s -> %-42s %s%s"
              % (name, shader_name,
                 t("shader.node_and_property_count", nodes=graph["node_count"],
                   properties=len(ir.properties)), flag))

    index_path = os.path.join(out_dir, "shaders.json")
    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump({"shaders": results}, fh, indent=2, ensure_ascii=False)

    # IR пишем всегда, даже когда выбран только HLSL: он ничего не стоит, зато
    # переключить формат в Unity можно потом, не перезапуская экспорт.
    ir_path = os.path.join(out_dir, "graph_ir.json")
    with open(ir_path, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "graphs": ir_graphs}, fh, indent=2, ensure_ascii=False)

    written = sum(1 for r in results if not r["reused"])
    print("\n" + t("shader.done", written=written,
                   reused=len(results) - written, todos=len(all_todos)))
    print(t("shader.index", path=index_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
