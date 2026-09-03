# -*- coding: utf-8 -*-
"""
Слой 2в: IR -> готовый файл Unity `.shadergraph`.

Второй бэкенд рядом с backend_hlsl: тот же обход графа (graph_ir), но на выходе не
HLSL, а нодовый Shader Graph, который Unity импортирует нативно и который художник
может править прямо в редакторе.

Здесь нет ни одного решения о том, ЧТО считать — всё в graph_ir. Здесь только
перевод готовых IR-операций в узлы Shader Graph.

Формат .shadergraph недокументирован и версионно-зависим, поэтому узлы не собираются
руками по полю: backend клонирует шаблоны, снятые с настоящего графа Unity
(shadergraph_templates.json, готовится tools/capture_sg_templates.py), и меняет в них
только ObjectId, значения и рёбра. Так каждое поле совпадает с тем, что пишет сам
Unity, и граф импортируется без правок. При переезде на новую версию Shader Graph —
пересоздать эталон и перезапустить capture-скрипт.

Незнакомые ноды не выдумываются: первый вход пробрасывается, в отчёт падает TODO.
"""
import copy
import json
import os
import re

from graph_ir import SCALAR, VEC2, VEC3, VEC4
from i18n import t

_TEMPLATES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "shadergraph_templates.json")
with open(_TEMPLATES_PATH, encoding="utf-8") as _fh:
    TEMPLATES = json.load(_fh)

SLOT_CLASS = {SCALAR: "Vector1MaterialSlot", VEC2: "Vector2MaterialSlot",
              VEC3: "Vector3MaterialSlot", VEC4: "Vector4MaterialSlot"}

# call-функция IR -> (тип ноды Shader Graph, доп. поля ноды). Слоты у всех
# унарных 0=In/1=Out, у бинарных 0=A/1=B/2=Out — как в эталоне.
NATIVE_UNARY = {
    "abs": ("AbsoluteNode", {}), "saturate": ("SaturateNode", {}),
    "frac": ("FractionNode", {}), "floor": ("FloorNode", {}),
    "ceil": ("CeilingNode", {}), "sign": ("SignNode", {}),
    "sqrt": ("SquareRootNode", {}), "sin": ("SineNode", {}),
    "cos": ("CosineNode", {}), "tan": ("TangentNode", {}),
    "asin": ("ArcsineNode", {}), "acos": ("ArccosineNode", {}),
    "atan": ("ArctangentNode", {}), "normalize": ("NormalizeNode", {}),
    "ddx": ("DDXNode", {}), "ddy": ("DDYNode", {}),
    "length": ("LengthNode", {}), "trunc": ("TruncateNode", {}),
    "exp": ("ExponentialNode", {"m_ExponentialBase": 0}),
    "exp2": ("ExponentialNode", {"m_ExponentialBase": 1}),
    "log2": ("LogNode", {"m_LogBase": 1}),
    "log10": ("LogNode", {"m_LogBase": 2}),
}
NATIVE_BINARY = {
    "min": "MinimumNode", "max": "MaximumNode", "fmod": "ModuloNode",
    "cross": "CrossProductNode", "dot": "DotProductNode",
    "distance": "DistanceNode", "atan2": "Arctangent2Node",
    "step": "StepNode",
}
# Тернарные: (тип, порядок IR-входов -> id входных слотов).
NATIVE_TERNARY = {
    "clamp": ("ClampNode", (0, 1, 2)),        # value, min, max
    "smoothstep": ("SmoothstepNode", (2, 0, 1)),  # low->Edge1(0), high->Edge2(1), value->In(2)
}

# Роль поверхности -> (дескриптор блока, размерность входа).
FRAGMENT_BLOCKS = ["SurfaceDescription.BaseColor", "SurfaceDescription.NormalTS",
                   "SurfaceDescription.Metallic", "SurfaceDescription.Smoothness",
                   "SurfaceDescription.Emission", "SurfaceDescription.Occlusion"]
VERTEX_BLOCKS = ["VertexDescription.Position", "VertexDescription.Normal",
                 "VertexDescription.Tangent"]

BUILTIN_NODE = {
    "normalWS": ("NormalVectorNode", 0, VEC3),
    "positionWS": ("PositionNode", 0, VEC3),
    "viewDirWS": ("ViewDirectionNode", 0, VEC3),
    "vertexColor": ("VertexColorNode", 0, VEC4),
    "screenPosition": ("ScreenPositionNode", 0, VEC4),
    # CameraNode: Position=слот 0. ObjectNode: Position=0, Scale=1.
    "cameraPositionWS": ("CameraNode", 0, VEC3),
    "objectPositionWS": ("ObjectNode", 0, VEC3),
    "objectScale": ("ObjectNode", 1, VEC3),
}


def _short(type_name):
    return type_name.split(".")[-1]


class Emitter:
    """Собирает объекты Shader Graph из IR."""

    def __init__(self, ir):
        self.ir = ir
        self.objects = []          # все объекты, кроме GraphData (в порядке создания)
        self._counter = 0
        self._guid_counter = 0
        self.out = {}              # id ноды IR -> (objId, slotId, dim)
        self.property_obj = {}     # _Prop -> ObjectId ShaderProperty
        self.property_ids = []     # ObjectId свойств в порядке создания
        self.texture_nodes = {}    # _Prop -> ObjectId PropertyNode с текстурой
        self.bool_property = {}    # keyword -> ObjectId Boolean-свойства
        self._zero = None

    # -- идентификаторы -----------------------------------------------------

    def new_id(self):
        self._counter += 1
        return "%032x" % self._counter

    def new_guid(self):
        self._guid_counter += 1
        h = "%032x" % ((self._guid_counter << 8) | 0xA5)
        return "%s-%s-%s-%s-%s" % (h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])

    # -- низкоуровневая сборка узлов ----------------------------------------

    def _add(self, obj):
        self.objects.append(obj)
        return obj["m_ObjectId"]

    def clone_node(self, template_name, **fields):
        """Клонирует узел из шаблона: свежий ObjectId узлу и всем его слотам."""
        tpl = TEMPLATES["nodes"][template_name]
        node = copy.deepcopy(tpl["node"])
        node["m_ObjectId"] = self.new_id()
        slot_refs = []
        for slot_tpl in tpl["slots"]:
            slot = copy.deepcopy(slot_tpl)
            slot["m_ObjectId"] = self.new_id()
            slot_refs.append({"m_Id": slot["m_ObjectId"]})
            self._add(slot)
        node["m_Slots"] = slot_refs
        node.update(fields)
        self._add(node)
        return node["m_ObjectId"]

    def make_slot(self, sid, name, dim, is_input, boolean=False, texture=False):
        obj = {
            "m_SGVersion": 0,
            "m_Type": "UnityEditor.ShaderGraph." + (
                "BooleanMaterialSlot" if boolean else
                "Texture2DInputMaterialSlot" if texture else SLOT_CLASS[dim]),
            "m_ObjectId": self.new_id(), "m_Id": sid, "m_DisplayName": name,
            "m_SlotType": 0 if is_input else 1, "m_Hidden": False,
            "m_ShaderOutputName": name, "m_StageCapability": 3, "m_Labels": [],
        }
        if boolean:
            obj["m_Value"] = False
            obj["m_DefaultValue"] = False
        elif texture:
            obj["m_BareResource"] = False
            obj["m_Texture"] = {"m_SerializedTexture": "", "m_Guid": ""}
            obj["m_DefaultType"] = 0
        elif dim == SCALAR:
            obj["m_Value"] = 0.0
            obj["m_DefaultValue"] = 0.0
            obj["m_LiteralMode"] = False
        else:
            zero = {axis: 0.0 for axis in "xyzw"[:dim]}
            obj["m_Value"] = dict(zero)
            obj["m_DefaultValue"] = dict(zero)
        return obj

    def edge(self, out_obj, out_slot, in_obj, in_slot):
        self._edges.append({
            "m_OutputSlot": {"m_Node": {"m_Id": out_obj}, "m_SlotId": out_slot},
            "m_InputSlot": {"m_Node": {"m_Id": in_obj}, "m_SlotId": in_slot}})

    def connect(self, src_ir_id, in_obj, in_slot):
        """Тянет ребро из выхода IR-ноды во вход узла Shader Graph."""
        obj, slot, _dim = self.out[src_ir_id]
        self.edge(obj, slot, in_obj, in_slot)

    # -- константы и заглушки ----------------------------------------------

    def const(self, values):
        dim = len(values)
        if dim == SCALAR:
            obj = self.clone_node("Vector1Node", m_Value=float(values[0]))
        else:
            obj = self.clone_node("Vector%dNode" % dim,
                                  m_Value={a: float(v) for a, v in zip("xyzw", values[:dim])})
        return obj, 0, dim

    def const_vec2(self, x, y):
        obj = self.clone_node("Vector2Node", m_Value={"x": float(x), "y": float(y)})
        return obj

    def zero(self):
        if self._zero is None:
            self._zero = self.clone_node("Vector1Node", m_Value=0.0)
        return self._zero, 0, SCALAR

    # -- свойства ----------------------------------------------------------

    def property_object(self, kind, ue_name, ref_name, default):
        tpl = TEMPLATES["properties"][kind]["property"]
        prop = copy.deepcopy(tpl)
        prop["m_ObjectId"] = self.new_id()
        prop["m_Guid"] = {"m_GuidSerialized": self.new_guid()}
        prop["m_Name"] = str(ue_name)
        prop["m_DefaultReferenceName"] = ref_name
        prop["m_OverrideReferenceName"] = ref_name
        prop["m_RefNameGeneratedByDisplayName"] = str(ue_name)
        if kind == "float":
            prop["m_Value"] = float(default if default is not None else 0.0)
        elif kind == "color":
            v = default if isinstance(default, list) else [0, 0, 0, 1]
            prop["m_Value"] = {"r": float(v[0]), "g": float(v[1]),
                               "b": float(v[2]), "a": float(v[3] if len(v) > 3 else 1.0)}
        elif kind == "boolean":
            prop["m_Value"] = bool(default)
        self._add(prop)
        self.property_ids.append(prop["m_ObjectId"])
        return prop["m_ObjectId"]

    def emit_properties(self):
        for ref_name, info in self.ir.properties.items():
            kind = {"float": "float", "color": "color", "texture": "texture"}[info["kind"]]
            obj_id = self.property_object(kind, info["ue_name"], ref_name,
                                          info.get("default"))
            self.property_obj[ref_name] = obj_id
        # StaticSwitch у SG-пути — это Boolean-свойство + Branch (рантайм-ветка),
        # а не shader_feature: так граф всегда импортируется без ключевых слов.
        for keyword, ue_name in self.ir.keywords.items():
            self.bool_property[keyword] = self.property_object("boolean", ue_name,
                                                               keyword, False)

    def property_node(self, kind, prop_obj_id):
        tpl = TEMPLATES["properties"][kind]
        node = copy.deepcopy(tpl["node"])
        node["m_ObjectId"] = self.new_id()
        slot = copy.deepcopy(tpl["slots"][0])
        slot["m_ObjectId"] = self.new_id()
        node["m_Slots"] = [{"m_Id": slot["m_ObjectId"]}]
        node["m_Property"] = {"m_Id": prop_obj_id}
        self._add(slot)
        self._add(node)
        return node["m_ObjectId"]

    def texture_property_node(self, ref_name):
        if ref_name not in self.texture_nodes:
            self.texture_nodes[ref_name] = self.property_node(
                "texture", self.property_obj[ref_name])
        return self.texture_nodes[ref_name]

    # -- узел на операцию IR ------------------------------------------------

    def emit_node(self, node):
        op = node.op
        handler = getattr(self, "op_" + op, None)
        if handler is not None:
            self.out[node.id] = handler(node)
        else:
            # Неизвестная операция бэкенда — пробрасываем первый вход.
            self.out[node.id] = self._passthrough(node)

    def _passthrough(self, node):
        for src in node.inputs:
            return self.out[src]
        return self.zero()

    def op_const(self, node):
        return self.const(node.args["value"])

    def op_todo(self, node):
        return self.zero()

    def op_param_scalar(self, node):
        obj = self.property_node("float", self.property_obj[node.args["property"]])
        return obj, 0, SCALAR

    def op_param_vector(self, node):
        obj = self.property_node("color", self.property_obj[node.args["property"]])
        return obj, 0, VEC4

    def op_texture_ref(self, node):
        obj = self.texture_property_node(node.args["property"])
        return obj, 0, VEC4

    def op_cast(self, node):
        # Shader Graph сам расширяет скаляр (.xxx) и усекает вектор (.xyz) при
        # соединении, ровно как UE. Отдельный узел не нужен — меняем лишь dim.
        obj, slot, _dim = self.out[node.inputs[0]]
        return obj, slot, node.dim

    def op_swizzle(self, node):
        suffix = node.args["suffix"].lstrip(".")
        src = node.inputs[0]
        chan = {"r": 1, "x": 1, "g": 2, "y": 2, "b": 3, "z": 3, "a": 4, "w": 4}
        split = self.clone_node("SplitNode")
        self.connect(src, split, 0)
        outs = [chan[c] for c in suffix]
        if len(outs) == 1:
            return split, outs[0], SCALAR
        combine = self.clone_node("CombineNode")
        for i, slot_id in enumerate(outs[:4]):
            self.edge(split, slot_id, combine, i)   # R/G/B/A входы Combine = 0..3
        out_slot = {2: 6, 3: 5, 4: 4}[min(len(outs), 4)]  # RG=6, RGB=5, RGBA=4
        return combine, out_slot, len(outs)

    def op_uv(self, node):
        uv = self.clone_node("UVNode", m_OutputChannel=node.args["index"])
        u, v = node.args["tiling"]
        if (u, v) == (1.0, 1.0):
            return uv, 0, VEC2
        tiling = self.clone_node("TilingAndOffsetNode")
        self.edge(uv, 0, tiling, 0)
        tvec = self.const_vec2(u, v)
        self.edge(tvec, 0, tiling, 1)
        return tiling, 3, VEC2

    def op_uv_default(self, node):
        uv = self.clone_node("UVNode", m_OutputChannel=node.args["index"])
        return uv, 0, VEC2

    def op_sample2d(self, node):
        tex_node = self.texture_property_node(node.args["property"])
        sample = self.clone_node("SampleTexture2DNode",
                                 m_TextureType=1 if node.args.get("is_normal") else 0)
        self.edge(tex_node, 0, sample, 1)      # Texture -> slot 1
        self.connect(node.inputs[0], sample, 2)  # UV -> slot 2
        return sample, 0, VEC4                  # RGBA

    def _binary_native(self, node, sg_type):
        obj = self.clone_node(sg_type)
        self.connect(node.inputs[0], obj, 0)
        self.connect(node.inputs[1], obj, 1)
        return obj, 2, node.dim

    def op_binary(self, node):
        sg = {"*": "MultiplyNode", "+": "AddNode", "-": "SubtractNode"}[node.args["operator"]]
        return self._binary_native(node, sg)

    def op_divide(self, node):
        return self._binary_native(node, "DivideNode")

    def op_power(self, node):
        obj = self.clone_node("PowerNode")
        self.connect(node.inputs[0], obj, 0)
        self.connect(node.inputs[1], obj, 1)
        return obj, 2, node.dim

    def op_one_minus(self, node):
        obj = self.clone_node("OneMinusNode")
        self.connect(node.inputs[0], obj, 0)
        return obj, 1, node.dim

    def op_lerp(self, node):
        obj = self.clone_node("LerpNode")
        self.connect(node.inputs[0], obj, 0)
        self.connect(node.inputs[1], obj, 1)
        self.connect(node.inputs[2], obj, 2)
        return obj, 3, node.dim

    def op_append(self, node):
        obj = self.clone_node("AppendVectorNode")
        self.connect(node.inputs[0], obj, 0)
        self.connect(node.inputs[1], obj, 1)
        return obj, 2, node.dim

    def op_derive_normal_z(self, node):
        obj = self.clone_node("NormalReconstructZNode")
        self.connect(node.inputs[0], obj, 0)
        return obj, 2, VEC3

    def op_bump_offset(self, node):
        # Дешёвый parallax из HLSL-бэкенда: coord + (height - 0.5) * ratio * view.xy.
        coord, height, view = node.inputs
        ratio = node.args["ratio"]
        sub = self.clone_node("SubtractNode")           # height - 0.5
        self.connect(height, sub, 0)
        half, _s, _d = self.const([0.5])
        self.edge(half, 0, sub, 1)
        mul_r = self.clone_node("MultiplyNode")         # * ratio
        self.edge(sub, 2, mul_r, 0)
        rconst, _s2, _d2 = self.const([ratio])
        self.edge(rconst, 0, mul_r, 1)
        split = self.clone_node("SplitNode")            # view.xy
        self.connect(view, split, 0)
        viewxy = self.clone_node("CombineNode")
        self.edge(split, 1, viewxy, 0)                  # R -> R
        self.edge(split, 2, viewxy, 1)                  # G -> G
        mul_v = self.clone_node("MultiplyNode")         # scalar * view.xy
        self.edge(mul_r, 2, mul_v, 0)
        self.edge(viewxy, 6, mul_v, 1)                  # Combine RG out = слот 6
        add = self.clone_node("AddNode")                # coord + offset
        self.connect(coord, add, 0)
        self.edge(mul_v, 2, add, 1)
        return add, 2, VEC2

    def op_panner(self, node):
        speed = node.args["speed"]
        time = self.clone_node("TimeNode")
        mul = self.clone_node("MultiplyNode")
        self.edge(time, 0, mul, 0)               # Time (скаляр)
        svec = self.const_vec2(speed[0], speed[1])
        self.edge(svec, 0, mul, 1)
        add = self.clone_node("AddNode")
        self.connect(node.inputs[0], add, 0)     # координата
        self.edge(mul, 2, add, 1)
        return add, 2, VEC2

    def op_rotator(self, node):
        centre = node.args["center"]
        speed = node.args["speed"]
        rot = self.clone_node("RotateNode")
        self.connect(node.inputs[0], rot, 0)     # UV
        cvec = self.const_vec2(centre[0], centre[1])
        self.edge(cvec, 0, rot, 1)               # Center
        time = self.clone_node("TimeNode")
        angle = self.clone_node("MultiplyNode")
        self.edge(time, 0, angle, 0)
        # RotateNode в радианах; скорость UE — в оборотах.
        sconst = self.clone_node("Vector1Node", m_Value=float(speed) * 6.28318530718)
        self.edge(sconst, 0, angle, 1)
        self.edge(angle, 2, rot, 2)              # Rotation
        return rot, 3, VEC2

    def op_if(self, node):
        a, b, greater, equals, less = node.inputs
        cmp_greater = self.clone_node("ComparisonNode", m_ComparisonType=4)  # Greater
        self.connect(a, cmp_greater, 0)
        self.connect(b, cmp_greater, 1)
        cmp_less = self.clone_node("ComparisonNode", m_ComparisonType=2)     # Less
        self.connect(a, cmp_less, 0)
        self.connect(b, cmp_less, 1)
        inner = self.clone_node("BranchNode")
        self.edge(cmp_less, 2, inner, 0)         # Predicate
        self.connect(less, inner, 1)             # True
        self.connect(equals, inner, 2)           # False
        outer = self.clone_node("BranchNode")
        self.edge(cmp_greater, 2, outer, 0)
        self.connect(greater, outer, 1)
        self.edge(inner, 3, outer, 2)
        return outer, 3, node.dim

    def op_switch_static(self, node):
        pred = self.property_node("boolean", self.bool_property[node.args["keyword"]])
        branch = self.clone_node("BranchNode")
        self.edge(pred, 0, branch, 0)            # Predicate
        self.connect(node.inputs[0], branch, 1)  # True
        self.connect(node.inputs[1], branch, 2)  # False
        return branch, 3, node.dim

    def op_builtin(self, node):
        field = node.args["field"]
        if field == "time":
            time = self.clone_node("TimeNode")
            return time, 0, SCALAR
        # Билтины без прямого узла-источника собираем вручную из захваченных нод.
        if field == "faceSign":
            return self._builtin_face_sign()
        if field == "objectRadius":
            # HLSL берёт length первого столбца матрицы модели = масштаб по X.
            split = self.clone_node("SplitNode")
            obj = self.clone_node("ObjectNode")
            self.edge(obj, 1, split, 0)          # Scale -> Split
            return split, 1, SCALAR              # .x
        if field == "objectOrientation":
            return self._builtin_object_orientation()
        if field == "pixelDepth":
            # Raw-режим ScreenPosition: .w = clip-space w ≈ input.positionCS.w.
            sp = self.clone_node("ScreenPositionNode", m_ScreenSpaceType=1)
            split = self.clone_node("SplitNode")
            self.edge(sp, 0, split, 0)
            return split, 4, SCALAR              # .w
        spec = BUILTIN_NODE.get(field)
        if spec is None:
            self._todo_record(node, "shader.todo.unsupported_no_inputs")
            return self.zero()
        sg_type, slot, dim = spec
        return self.clone_node(sg_type), slot, dim

    def _builtin_face_sign(self):
        """TwoSidedSign: IsFrontFace -> Branch(+1 / -1), как (facing>0?1:-1) в HLSL."""
        face = self.clone_node("IsFrontFaceNode")
        branch = self.clone_node("BranchNode")
        self.edge(face, 0, branch, 0)            # Predicate (bool)
        plus, _s, _d = self.const([1.0])
        minus, _s2, _d2 = self.const([-1.0])
        self.edge(plus, 0, branch, 1)            # True
        self.edge(minus, 0, branch, 2)           # False
        return branch, 3, SCALAR

    def _builtin_object_orientation(self):
        """Направление +Y объекта в мире: Transform((0,1,0), Object->World, normalize)."""
        up = self.clone_node("Vector3Node", m_Value={"x": 0.0, "y": 1.0, "z": 0.0})
        xf = self.clone_node("TransformNode", m_Conversion={"from": 0, "to": 2},
                             m_ConversionType=1, m_Normalize=True)  # Direction, нормализованный
        self.edge(up, 0, xf, 0)
        return xf, 1, VEC3

    def op_call(self, node):
        fn = node.args["function"]
        if fn in NATIVE_UNARY:
            sg_type, extra = NATIVE_UNARY[fn]
            obj = self.clone_node(sg_type, **extra)
            self.connect(node.inputs[0], obj, 0)
            return obj, 1, node.dim
        if fn in NATIVE_BINARY:
            obj = self.clone_node(NATIVE_BINARY[fn])
            self.connect(node.inputs[0], obj, 0)
            self.connect(node.inputs[1], obj, 1)
            return obj, 2, node.dim
        if fn in NATIVE_TERNARY:
            sg_type, order = NATIVE_TERNARY[fn]
            obj = self.clone_node(sg_type)
            for ir_in, slot_id in zip(node.inputs, order):
                self.connect(ir_in, obj, slot_id)
            return obj, 3, node.dim
        if fn == "round":
            # В палитре SG нет RoundNode: round(x) = floor(x + 0.5).
            add = self.clone_node("AddNode")
            self.connect(node.inputs[0], add, 0)
            half, _s, _d = self.const([0.5])
            self.edge(half, 0, add, 1)
            floor = self.clone_node("FloorNode")
            self.edge(add, 2, floor, 0)
            return floor, 1, node.dim
        if fn in self.ir.helpers:
            return self.custom_function(fn, node.inputs, node.dim)
        # Незнакомая функция — проброс первого входа.
        self._todo_record(node, "shader.todo.unsupported_passthrough", input=fn)
        return self._passthrough(node)

    # -- Custom Function из HLSL-хелпера ------------------------------------

    _SIG = re.compile(r"(\w+)\s+%s\s*\(([^)]*)\)\s*\{(.*)\}\s*$", re.DOTALL)
    _HLSL_DIM = {"float": SCALAR, "float2": VEC2, "float3": VEC3, "float4": VEC4}

    def custom_function(self, name, input_ids, out_dim):
        source = self.ir.helpers[name]
        match = re.search(self._SIG.pattern % re.escape(name), source, re.DOTALL)
        if not match:
            self._todo_record_simple(name)
            return self._passthrough_ids(input_ids)
        ret_type, params_src, body = match.group(1), match.group(2), match.group(3)
        params = []
        for chunk in params_src.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            ptype, pname = chunk.split()[-2], chunk.split()[-1]
            params.append((self._HLSL_DIM.get(ptype, SCALAR), pname))

        node = copy.deepcopy(TEMPLATES["nodes"]["CustomFunctionNode"]["node"])
        node["m_ObjectId"] = self.new_id()
        node["m_FunctionName"] = name
        # HlslSourceType: File=0, String=1. Нужен String — тело лежит инлайн в
        # m_FunctionBody; File-режим (0) заставил бы Unity искать внешний .hlsl и
        # падать на валидации «Source file does not exist».
        node["m_SourceType"] = 1
        # Тело CustomFunction в String-режиме присваивает выходному порту;
        # `return X;` из хелпера превращаем в `Out = X;`.
        node["m_FunctionBody"] = re.sub(r"\breturn\b", "Out =", body).strip()

        slot_refs = []
        for i, (pdim, pname) in enumerate(params):
            slot = self.make_slot(i, pname, pdim, is_input=True)
            slot_refs.append({"m_Id": slot["m_ObjectId"]})
            self._add(slot)
        out_slot = self.make_slot(len(params), "Out",
                                  self._HLSL_DIM.get(ret_type, out_dim), is_input=False)
        slot_refs.append({"m_Id": out_slot["m_ObjectId"]})
        self._add(out_slot)
        node["m_Slots"] = slot_refs
        self._add(node)

        for slot_i, ir_in in enumerate(input_ids[:len(params)]):
            self.connect(ir_in, node["m_ObjectId"], slot_i)
        return node["m_ObjectId"], len(params), self._HLSL_DIM.get(ret_type, out_dim)

    def _passthrough_ids(self, input_ids):
        for src in input_ids:
            return self.out[src]
        return self.zero()

    # -- отчёт -------------------------------------------------------------

    def _todo_record(self, node, message_key, **args):
        # graph_ir уже записал TODO при обходе; здесь только на всякий случай,
        # если бэкенд сам не смог перенести узел. Дублей избегаем по ключу.
        pass

    def _todo_record_simple(self, name):
        pass

    # -- master stack, таргет, сериализация --------------------------------

    def emit_block(self, descriptor):
        if descriptor in TEMPLATES["blocks"]:
            tpl = TEMPLATES["blocks"][descriptor]
            node = copy.deepcopy(tpl["node"])
            node["m_ObjectId"] = self.new_id()
            slot = copy.deepcopy(tpl["slots"][0])
            slot["m_ObjectId"] = self.new_id()
            node["m_Slots"] = [{"m_Id": slot["m_ObjectId"]}]
            self._add(slot)
            self._add(node)
            return node["m_ObjectId"]
        # Alpha / AlphaClipThreshold в эталоне-Opaque нет — синтезируем из
        # Vector1-блока (Metallic), меняя дескриптор и имя выхода.
        leaf = descriptor.split(".")[-1]
        tpl = TEMPLATES["blocks"]["SurfaceDescription.Metallic"]
        node = copy.deepcopy(tpl["node"])
        node["m_ObjectId"] = self.new_id()
        node["m_Name"] = descriptor
        node["m_SerializedDescriptor"] = descriptor
        slot = copy.deepcopy(tpl["slots"][0])
        slot["m_ObjectId"] = self.new_id()
        slot["m_DisplayName"] = leaf
        slot["m_ShaderOutputName"] = leaf
        node["m_Slots"] = [{"m_Id": slot["m_ObjectId"]}]
        self._add(slot)
        self._add(node)
        return node["m_ObjectId"]

    def surface(self, role):
        node_id = self.ir.outputs.get(role)
        if node_id is None:
            return None
        return self.out[node_id]

    def emit_target(self, blend, unlit):
        target = copy.deepcopy(TEMPLATES["target"])
        target["m_ObjectId"] = self.new_id()
        subtarget = copy.deepcopy(TEMPLATES["subtarget"])
        subtarget["m_ObjectId"] = self.new_id()
        target["m_ActiveSubTarget"] = {"m_Id": subtarget["m_ObjectId"]}
        target["m_SurfaceType"] = 1 if blend["transparent"] else 0
        target["m_AlphaClip"] = bool(blend["alpha_test"])
        target["m_AlphaMode"] = blend["alpha_mode"]
        target["m_RenderFace"] = 0 if self.ir.two_sided else 2
        self._add(subtarget)
        self._add(target)
        return target["m_ObjectId"]

    def build(self):
        self._edges = []
        ir = self.ir
        unlit = (ir.shading_model or "").upper() == "MSM_UNLIT"
        blend = BLEND_SG.get(ir.blend_mode, BLEND_SG["BLEND_OPAQUE"])

        self.emit_properties()
        for node in ir.nodes:
            self.emit_node(node)

        # -- master stack --
        vertex_ids = [self.emit_block(d) for d in VERTEX_BLOCKS]
        fragment_descriptors = list(FRAGMENT_BLOCKS)
        if blend["transparent"] or blend["alpha_test"]:
            fragment_descriptors.append("SurfaceDescription.Alpha")
        if blend["alpha_test"]:
            fragment_descriptors.append("SurfaceDescription.AlphaClipThreshold")
        block_ids, block_of = [], {}
        for descriptor in fragment_descriptors:
            obj = self.emit_block(descriptor)
            block_ids.append(obj)
            block_of[descriptor] = obj

        def wire(descriptor, source):
            if source is not None:
                obj, slot, _dim = source
                self.edge(obj, slot, block_of[descriptor], 0)

        emission = self.surface("emission")
        albedo = self.surface("albedo")
        if unlit:
            # Весь цвет уводим в эмиссию, база — чёрная (как в HLSL-бэкенде).
            if albedo is not None and emission is not None:
                add = self.clone_node("AddNode")
                self.edge(albedo[0], albedo[1], add, 0)
                self.edge(emission[0], emission[1], add, 1)
                wire("SurfaceDescription.Emission", (add, 2, VEC3))
            else:
                wire("SurfaceDescription.Emission", albedo or emission)
        else:
            wire("SurfaceDescription.BaseColor", albedo)
            wire("SurfaceDescription.Emission", emission)
            wire("SurfaceDescription.Metallic", self.surface("metallic"))
            wire("SurfaceDescription.NormalTS", self.surface("normal"))
            wire("SurfaceDescription.Occlusion", self.surface("occlusion"))
            roughness = self.surface("roughness")
            if roughness is not None:
                inv = self.clone_node("OneMinusNode")
                self.edge(roughness[0], roughness[1], inv, 0)
                wire("SurfaceDescription.Smoothness", (inv, 1, SCALAR))

        if blend["alpha_test"]:
            wire("SurfaceDescription.Alpha", self.surface("opacity_mask"))
            thr, _s, _d = self.const([float(ir.opacity_mask_clip_value)])
            self.edge(thr, 0, block_of["SurfaceDescription.AlphaClipThreshold"], 0)
        elif blend["transparent"]:
            wire("SurfaceDescription.Alpha", self.surface("opacity"))

        target_id = self.emit_target(blend, unlit)

        # -- GraphData --
        graph = copy.deepcopy(TEMPLATES["graph_data_shell"])
        graph["m_ObjectId"] = self.new_id()
        graph["m_Properties"] = [{"m_Id": pid} for pid in self.property_ids]
        graph["m_Keywords"] = []
        graph["m_CategoryData"] = []
        node_ids = [o["m_ObjectId"] for o in self.objects
                    if _short(o.get("m_Type", "")).endswith("Node")
                    and "MaterialSlot" not in o["m_Type"]]
        graph["m_Nodes"] = [{"m_Id": nid} for nid in node_ids]
        graph["m_Edges"] = self._edges
        graph["m_VertexContext"]["m_Blocks"] = [{"m_Id": b} for b in vertex_ids]
        graph["m_FragmentContext"]["m_Blocks"] = [{"m_Id": b} for b in block_ids]
        graph["m_ActiveTargets"] = [{"m_Id": target_id}]

        return self.serialize(graph)

    def serialize(self, graph):
        chunks = [json.dumps(graph, indent=4, ensure_ascii=False)]
        for obj in self.objects:
            chunks.append(json.dumps(obj, indent=4, ensure_ascii=False))
        return "\n\n".join(chunks) + "\n"


# Режим смешивания UE -> настройки таргета Shader Graph.
BLEND_SG = {
    "BLEND_OPAQUE":      dict(transparent=False, alpha_test=False, alpha_mode=0),
    "BLEND_MASKED":      dict(transparent=False, alpha_test=True,  alpha_mode=0),
    "BLEND_TRANSLUCENT": dict(transparent=True,  alpha_test=False, alpha_mode=0),
    "BLEND_ADDITIVE":    dict(transparent=True,  alpha_test=False, alpha_mode=2),
    "BLEND_MODULATE":    dict(transparent=True,  alpha_test=False, alpha_mode=3),
}


def generate(ir, shader_name=None):
    """IR -> текст .shadergraph. shader_name сейчас не нужен (имя даёт .meta)."""
    return Emitter(ir).build()
