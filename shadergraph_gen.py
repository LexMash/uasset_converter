#!/usr/bin/env python3
"""
Слой 2 (второй путь): граф мастер-материала Unreal -> Unity Shader Graph.

Оркестровка, как в shader_gen: прочитать манифест, схлопнуть одинаковые графы,
записать файлы и индекс. Обход графа и генерация — в graph_ir и
backend_shadergraph; здесь только то же, что и для HLSL, но на выходе .shadergraph.

Файл .shadergraph Unity импортирует нативно (свой импортёр не нужен), .meta он
создаёт сам при первом импорте. Индекс shadergraphs.json связывает мастер Unreal
с путём графа — по нему Unity-сторона находит шейдер и вешает его на материал.
"""
import argparse
import glob
import json
import os
import sys

import backend_shadergraph
from graph_ir import build_ir
from shader_gen import graph_signature   # тот же отпечаток, что и у HLSL-пути
from i18n import t, set_language


def generate_shadergraph(graph, log=None, functions=None):
    """Граф -> (текст .shadergraph, IR). IR отдаём наружу ради свойств и TODO."""
    ir = build_ir(graph, functions, log)
    return backend_shadergraph.generate(ir), ir


def main():
    parser = argparse.ArgumentParser(
        description="Transpile Unreal master materials into Unity Shader Graph")
    parser.add_argument("--config", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config.json"))
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

    functions = {entry["ue_path"]: entry
                 for entry in (manifest.get("material_functions") or [])}

    shaders_dir = os.path.join(out_dir, "Shaders")
    os.makedirs(shaders_dir, exist_ok=True)

    # Каждый прогон — авторитетный набор графов. Старые .shadergraph (от прежних,
    # возможно более крупных, экспортов) удаляем: иначе они «протекают» в Unity —
    # осиротевший файл всё равно копируется и импортируется, и старый формат
    # (например Custom Function в File-режиме) ломает импорт. .shader HLSL-пути
    # лежат тут же, но у них другое расширение — их не трогаем.
    for stale in glob.glob(os.path.join(shaders_dir, "*.shadergraph")):
        os.remove(stale)
        if os.path.isfile(stale + ".meta"):
            os.remove(stale + ".meta")

    by_signature = {}     # отпечаток -> {"file", "shader"}
    used_names = {}       # имя -> отпечаток
    results = []

    for graph in sorted(graphs, key=lambda g: g["ue_path"]):
        name = graph["ue_path"].rsplit("/", 1)[-1]
        signature = graph_signature(graph)

        if signature in by_signature:
            results.append({"ue_path": graph["ue_path"],
                            "shadergraph": by_signature[signature]["file"],
                            "reused": True})
            print("= %-28s -> %s" % (name, t("shader.reused",
                                            shader=by_signature[signature]["file"])))
            continue

        # Одноимённые мастера с РАЗНЫМИ графами обязаны получить разные имена.
        unique = name
        if name in used_names:
            pack = graph["ue_path"].split("/")[2]
            unique = "%s_%s" % (name, pack)

        text, ir = generate_shadergraph(graph, print, functions)
        rel = "Shaders/%s.shadergraph" % unique
        with open(os.path.join(out_dir, rel), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

        by_signature[signature] = {"file": rel}
        used_names[unique] = signature
        results.append({"ue_path": graph["ue_path"], "shadergraph": rel,
                        "properties": dict(ir.properties),
                        "keywords": dict(ir.keywords),
                        "dropped_parameters": ir.dropped_parameters,
                        "todos": ir.todos, "reused": False})

        flag = "  " + t("shader.todo_count", count=len(ir.todos)) if ir.todos else ""
        print("+ %-28s -> %-42s %s%s"
              % (name, rel, t("shader.node_and_property_count",
                             nodes=graph["node_count"], properties=len(ir.properties)), flag))

    index_path = os.path.join(out_dir, "shadergraphs.json")
    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump({"shadergraphs": results}, fh, indent=2, ensure_ascii=False)

    written = sum(1 for r in results if not r["reused"])
    print("\n" + t("shader.done", written=written,
                   reused=len(results) - written,
                   todos=sum(len(r.get("todos", [])) for r in results)))
    print(t("shader.index", path=index_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
