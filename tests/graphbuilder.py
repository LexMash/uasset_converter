# -*- coding: utf-8 -*-
"""
Конструктор синтетических графов материала в том же формате, в каком их
дампит ue_export.dump_material_graph.

Настоящий манифест для тестов не годится: он привязан к конкретному проекту
Unreal и весит десятки мегабайт. Здесь граф собирается из нескольких строк,
зато покрывает ровно ту ноду, ради которой написан тест.
"""
import json


class Graph:
    def __init__(self, ue_path="/Game/Test/M_Test", **kwargs):
        self.data = {
            "ue_path": ue_path,
            "nodes": [],
            "outputs": {},
            "parameters": {"scalars": {}, "vectors": {}, "textures": {}, "switches": {}},
            "blend_mode": kwargs.get("blend_mode", "BLEND_OPAQUE"),
            "shading_model": kwargs.get("shading_model", "MSM_DEFAULT_LIT"),
            "material_domain": kwargs.get("material_domain", "MD_SURFACE"),
            "two_sided": kwargs.get("two_sided", False),
            "opacity_mask_clip_value": kwargs.get("opacity_mask_clip_value", 0.333),
            "dither_opacity_mask": False,
        }

    def node(self, type_name, inputs=None, outputs=None, **props):
        """
        inputs — словарь «имя входа -> источник». Источник это либо индекс ноды,
        либо кортеж (индекс, имя выхода) когда нужен конкретный канал, либо None
        для неподключённого входа. Порядок ключей = порядок входов в UE.
        """
        index = len(self.data["nodes"])
        entries = []
        for position, (name, source) in enumerate((inputs or {}).items()):
            from_index, from_output = None, None
            if isinstance(source, tuple):
                from_index, from_output = source
            elif source is not None:
                from_index = source
            entries.append({"name": name, "index": position,
                            "from": from_index, "from_output": from_output})

        self.data["nodes"].append({
            "index": index,
            "type": type_name,
            "props": props,
            "inputs": entries,
            "outputs": outputs or [],
        })
        return index

    def connect(self, material_property, source, output=None):
        self.data["outputs"][material_property] = {"from": source, "from_output": output}
        return self

    def parameter(self, kind, name, value=None):
        """Регистрирует параметр так, как это делает дампер: имя -> дефолт."""
        self.data["parameters"][kind][name] = value
        return self

    def build(self):
        self.data["node_count"] = len(self.data["nodes"])
        return self.data


def dumps(graph):
    return json.dumps(graph, indent=2, ensure_ascii=False, sort_keys=False)
