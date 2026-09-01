# -*- coding: utf-8 -*-
"""
Слой 2а: граф мастер-материала Unreal -> нейтральное промежуточное представление.

Зачем отдельный слой. Обход графа держится отдельно от генерации текста: здесь
принимаются все решения о том, ЧТО считать, а backend_hlsl только рендерит из
готового списка операций текст .shader. Так логику переноса можно покрыть
тестами, не разбирая при этом строки HLSL.

IR намеренно маленький: около тридцати операций, у каждой известна размерность
результата. Всё, что не ложится в этот словарь, попадает в операцию custom с
готовым кодом HLSL — так расширение сводится к одному месту.

Незнакомые ноды не выдумываются: они дают TODO и проброс первого входа.
"""
import re

SCALAR, VEC2, VEC3, VEC4 = 1, 2, 3, 4

HLSL_TYPE = {1: "float", 2: "float2", 3: "float3", 4: "float4"}

# Шаблон передаёт во фрагмент четыре набора UV. Больше в Unity просто нет.
MAX_UV_SETS = 4


def sanitize(name):
    """'Base Rough Min' -> '_BaseRoughMin' — валидное имя свойства ShaderLab."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", str(name)).title().replace(" ", "")
    if not cleaned:
        cleaned = "Param"
    if cleaned[0].isdigit():
        cleaned = "P" + cleaned
    return "_" + cleaned


def swizzle_for(output_name, dim):
    """Какой суффикс взять с выхода ноды: RGB -> .rgb, R -> .r и т.д."""
    if not output_name:
        return "", dim
    key = output_name.strip().lower()
    table = {
        "rgb": (".rgb", VEC3), "rgba": ("", VEC4),
        "r": (".r", SCALAR), "g": (".g", SCALAR),
        "b": (".b", SCALAR), "a": (".a", SCALAR),
        "x": (".x", SCALAR), "y": (".y", SCALAR), "z": (".z", SCALAR), "w": (".w", SCALAR),
        "result": ("", dim), "": ("", dim),
    }
    return table.get(key, ("", dim))


# ----------------------------------------------------------------------------
# Словарь операций
# ----------------------------------------------------------------------------
# Однооперандные функции, у которых размерность результата равна размерности
# аргумента, а имя в HLSL совпадает с именем ноды Unreal.
UNARY_FUNCTIONS = {
    "Abs": "abs",
    "Saturate": "saturate",
    "Frac": "frac",
    "Floor": "floor",
    "Ceil": "ceil",
    "Round": "round",
    "Truncate": "trunc",
    "Sign": "sign",
    "SquareRoot": "sqrt",
    "Sine": "sin",
    "Cosine": "cos",
    "Tangent": "tan",
    "Arcsine": "asin",
    "Arccosine": "acos",
    "Arctangent": "atan",
    "Normalize": "normalize",
    "DDX": "ddx",
    "DDY": "ddy",
    "Logarithm2": "log2",
    "Logarithm10": "log10",
    "Exponential": "exp",
    "Exponential2": "exp2",
}

# Двухоперандные функции: имя ноды -> имя в HLSL.
BINARY_FUNCTIONS = {
    "Min": "min",
    "Max": "max",
    "Fmod": "fmod",
    "Arctangent2": "atan2",
    "Cross": "cross",
}

# Ноды-«провода»: значение проходит насквозь, отмечать их в отчёте не за что.
PASSTHROUGH_NODES = {
    "Reroute", "NamedRerouteUsage", "NamedRerouteDeclaration",
    "MaterialFunctionOutput", "Comment",
}

# Входы пиксельного шейдера. Значение — (имя поля в SurfaceInputs, размерность).
# Бэкенд HLSL по этому же списку решает, какие поля вообще заводить в структуре:
# тащить в шейдер позицию в мире, если её никто не спрашивал, незачем.
BUILTIN_INPUTS = {
    "VertexColor": ("vertexColor", VEC4),
    "PixelNormalWS": ("normalWS", VEC3),
    "VertexNormalWS": ("normalWS", VEC3),
    "WorldPosition": ("positionWS", VEC3),
    "CameraPositionWS": ("cameraPositionWS", VEC3),
    "CameraVectorWS": ("viewDirWS", VEC3),
    "ObjectPositionWS": ("objectPositionWS", VEC3),
    "ObjectRadius": ("objectRadius", SCALAR),
    "ObjectScale": ("objectScale", VEC3),
    "ObjectOrientation": ("objectOrientation", VEC3),
    "ActorPositionWS": ("objectPositionWS", VEC3),
    "TwoSidedSign": ("faceSign", SCALAR),
    "ScreenPosition": ("screenPosition", VEC4),
    "PixelDepth": ("pixelDepth", SCALAR),
}


class Node:
    """Одна операция IR."""

    __slots__ = ("id", "op", "dim", "args", "inputs", "comment")

    def __init__(self, node_id, op, dim, args=None, inputs=None, comment=None):
        self.id = node_id
        self.op = op
        self.dim = dim
        self.args = args or {}
        self.inputs = inputs or []      # список id других нод
        self.comment = comment


class GraphIR:
    """Результат обхода одного мастер-материала."""

    def __init__(self, graph):
        self.ue_path = graph["ue_path"]
        self.node_count = graph.get("node_count", 0)
        self.blend_mode = graph.get("blend_mode") or "BLEND_OPAQUE"
        self.shading_model = graph.get("shading_model") or "MSM_DEFAULT_LIT"
        self.two_sided = bool(graph.get("two_sided"))
        self.opacity_mask_clip_value = graph.get("opacity_mask_clip_value") or 0.333

        self.nodes = []
        self.properties = {}     # _Prop -> описание
        self.textures = {}       # _Prop -> {"is_normal": bool}
        self.keywords = {}       # _KEYWORD -> имя параметра UE
        self.helpers = {}        # имя -> исходник функции HLSL
        self.builtins = set()    # какие поля SurfaceInputs понадобились
        self.max_uv = 0
        self.todos = []
        self.outputs = {}        # роль поверхности -> id ноды
        self.dropped_parameters = []


# ----------------------------------------------------------------------------
# Функции движка, перенесённые вручную
# ----------------------------------------------------------------------------
# Инлайн графа функции даёт верный результат, но для этих трёх ручная версия
# короче и читаемее, а для BlendAngleCorrectedNormals — ещё и заметно дешевле.
MANUAL_FUNCTIONS = {
    "CheapContrast": ("CheapContrast", ["In", "Contrast"], SCALAR, [SCALAR, SCALAR]),
    "CheapContrast_RGB": ("CheapContrast_RGB", ["In", "Contrast"], VEC3, [VEC3, SCALAR]),
    "BlendAngleCorrectedNormals": ("BlendAngleCorrectedNormals",
                                   ["BaseNormal", "AdditionalNormal"], VEC3, [VEC3, VEC3]),
}

HELPER_SOURCES = {
    "CheapContrast": """// Engine_MaterialFunctions01/ImageAdjustment/CheapContrast
float CheapContrast(float In, float Contrast)
{
    return saturate(lerp(0.0 - Contrast, 1.0 + Contrast, In));
}""",
    "CheapContrast_RGB": """float3 CheapContrast_RGB(float3 In, float Contrast)
{
    return saturate(lerp((0.0 - Contrast).xxx, (1.0 + Contrast).xxx, In));
}""",
    "BlendAngleCorrectedNormals": """// Engine_MaterialFunctions02/Utility/BlendAngleCorrectedNormals
// Reoriented Normal Mapping, the way the Unreal node does it.
float3 BlendAngleCorrectedNormals(float3 BaseNormal, float3 AdditionalNormal)
{
    BaseNormal.b += 1.0;
    AdditionalNormal *= float3(-1.0, -1.0, 1.0);
    return BaseNormal * dot(BaseNormal, AdditionalNormal) / max(BaseNormal.b, 1e-5) - AdditionalNormal;
}""",
    "UEDesaturation": """// Unreal Desaturation node
float3 UEDesaturation(float3 In, float Fraction, float3 LuminanceFactors)
{
    return lerp(In, dot(In, LuminanceFactors).xxx, Fraction);
}""",
    "UESphereMask": """// Unreal SphereMask node
float UESphereMask(float3 A, float3 B, float Radius, float Hardness)
{
    float normalized = distance(A, B) / max(Radius, 1e-5);
    return saturate((1.0 - normalized) / max(1.0 - Hardness, 1e-5));
}""",
    "UEFresnel": """// Unreal Fresnel node
float UEFresnel(float3 NormalWS, float3 ViewDirWS, float Exponent, float BaseReflect)
{
    float facing = 1.0 - saturate(dot(normalize(NormalWS), normalize(ViewDirWS)));
    return BaseReflect + (1.0 - BaseReflect) * pow(facing, max(Exponent, 1e-5));
}""",
    "UERotateAboutAxis": """// Unreal RotateAboutAxis / CustomRotator node
float2 UERotate2D(float2 UV, float2 Center, float Angle)
{
    float2 offset = UV - Center;
    float s, c;
    sincos(Angle * 6.28318530718, s, c);
    return float2(offset.x * c - offset.y * s, offset.x * s + offset.y * c) + Center;
}""",
}


class IRBuilder:
    """Обходит граф Unreal и складывает IR."""

    def __init__(self, graph, functions=None, log=None):
        self.graph = graph
        self.nodes = graph["nodes"]
        self.functions = functions or {}     # путь функции -> её граф
        self.log = log or (lambda *_: None)

        self.ir = GraphIR(graph)
        self._cache = {}        # индекс ноды UE -> id ноды IR
        self._samples = {}      # (свойство, uv, is_normal) -> id ноды IR
        self._const_cache = {}  # (значения, размерность) -> id ноды IR
        self._function_stack = []   # защита от циклов при инлайне функций
        self._scope = None       # значения FunctionInput при инлайне

    # -- фабрика нод --------------------------------------------------------

    def emit(self, op, dim, args=None, inputs=None, comment=None):
        node = Node(len(self.ir.nodes), op, dim, args, inputs, comment)
        self.ir.nodes.append(node)
        return node.id

    def dim_of(self, node_id):
        return self.ir.nodes[node_id].dim

    def const(self, values, dim=None):
        """Числовая константа. Одинаковые константы делят одну ноду."""
        if not isinstance(values, (list, tuple)):
            values = [values]
        values = [float(v) for v in values]
        dim = dim or len(values)
        key = (tuple(values), dim)
        if key not in self._const_cache:
            self._const_cache[key] = self.emit("const", dim, {"value": values})
        return self._const_cache[key]

    def swizzle(self, node_id, suffix, dim):
        if not suffix:
            return node_id
        return self.emit("swizzle", dim, {"suffix": suffix}, [node_id])

    def cast(self, node_id, to_dim):
        """Приведение размерности по правилам UE: скаляр размножается, лишнее режется."""
        from_dim = self.dim_of(node_id)
        if from_dim == to_dim:
            return node_id
        return self.emit("cast", to_dim, {"from": from_dim}, [node_id])

    def note_todo(self, node, message_key, **args):
        """
        Пометка о неточном переносе.

        Кроме записи в отчёт, в IR добавляется узел-комментарий: он ничего не
        вычисляет, но бэкенд ставит по нему TODO прямо в код рядом с тем местом,
        где приблизительность возникла. Читать шейдер без этого невозможно —
        отчёт лежит отдельным файлом и в редакторе кода его не видно.
        """
        record = {"node": node["index"], "type": node["type"],
                  "message_key": message_key, "message_args": args}
        self.ir.todos.append(record)
        self.emit("todo", SCALAR, dict(record))

    def helper(self, name):
        """Подключает функцию-хелпер; в шейдер попадут только использованные."""
        if name in HELPER_SOURCES:
            self.ir.helpers[name] = HELPER_SOURCES[name]
        return name

    # -- свойства -----------------------------------------------------------

    def add_scalar(self, ue_name, default):
        prop = sanitize(ue_name)
        self.ir.properties.setdefault(prop, {
            "kind": "float", "ue_name": str(ue_name),
            "default": float(default if default is not None else 0.0)})
        return prop

    def add_vector(self, ue_name, default):
        prop = sanitize(ue_name)
        value = default if isinstance(default, list) else [0.0, 0.0, 0.0, 1.0]
        self.ir.properties.setdefault(prop, {
            "kind": "color", "ue_name": str(ue_name), "default": value})
        return prop

    def add_texture(self, ue_name, sampler_type, default_path):
        prop = sanitize(ue_name)
        is_normal = "normal" in str(sampler_type or "").lower()
        self.ir.properties.setdefault(prop, {
            "kind": "texture", "ue_name": str(ue_name),
            "default": "bump" if is_normal else "white",
            "is_normal": is_normal, "ue_default_texture": default_path})
        self.ir.textures.setdefault(prop, {"is_normal": is_normal})
        return prop, is_normal

    def add_keyword(self, ue_name):
        keyword = "_" + re.sub(r"[^A-Za-z0-9]+", "_", str(ue_name)).upper().strip("_")
        self.ir.keywords.setdefault(keyword, str(ue_name))
        return keyword

    def builtin(self, name):
        field, dim = BUILTIN_INPUTS[name]
        self.ir.builtins.add(field)
        return self.emit("builtin", dim, {"field": field}, comment=name)

    # -- обход --------------------------------------------------------------

    def input_of(self, node, name_or_index):
        for entry in node["inputs"]:
            if entry["name"] == name_or_index or entry["index"] == name_or_index:
                return entry
        return None

    def eval_input(self, node, name_or_index, fallback=None, default=0.0):
        """
        Значение входа. Если провод не подключён — берём константу-заглушку
        ноды (const_a / const_b / ...), как это делает сам Unreal.
        """
        entry = self.input_of(node, name_or_index)
        if entry is not None and entry["from"] is not None:
            return self.eval_node(entry["from"], entry["from_output"])

        value = node["props"].get(fallback) if fallback else None
        if isinstance(value, list):
            return self.const(value)
        if value is None:
            value = default
        return self.const(float(value))

    def eval_node(self, index, output_name=None):
        """
        Кэшируем саму ноду, а не пару «нода + канал»: иначе текстура, у которой
        берут RGB, R и A, семплится три раза вместо одного. Свизл — это уже
        просто операция поверх готового значения.
        """
        # Материал-функция с несколькими выходами даёт разный результат под
        # разные запрошенные выходы, поэтому её кэшируем по (нода, выход), а имя
        # выхода передаём внутрь — свизлом поверх его не подобрать.
        if self.nodes[index]["type"] == "MaterialFunctionCall":
            call_key = (id(self._scope), index, output_name)
            if call_key not in self._cache:
                self._cache[call_key] = self.node_MaterialFunctionCall(
                    self.nodes[index], output_name)
            return self._cache[call_key]

        cache_key = (id(self._scope), index)
        if cache_key not in self._cache:
            node = self.nodes[index]
            handler = getattr(self, "node_" + node["type"], None)
            if handler is not None:
                self._cache[cache_key] = handler(node)
            elif node["type"] in PASSTHROUGH_NODES:
                self._cache[cache_key] = self.passthrough(node)
            elif node["type"] in UNARY_FUNCTIONS:
                self._cache[cache_key] = self.unary(node, UNARY_FUNCTIONS[node["type"]])
            elif node["type"] in BINARY_FUNCTIONS:
                self._cache[cache_key] = self.binary_function(node, BINARY_FUNCTIONS[node["type"]])
            elif node["type"] in BUILTIN_INPUTS:
                self._cache[cache_key] = self.builtin(node["type"])
            else:
                self._cache[cache_key] = self.fallback_node(node)

        node_id = self._cache[cache_key]
        suffix, new_dim = swizzle_for(output_name, self.dim_of(node_id))
        return self.swizzle(node_id, suffix, new_dim)

    def passthrough(self, node):
        """Провод: первый подключённый вход проходит насквозь, без замечаний."""
        for entry in node["inputs"]:
            if entry["from"] is not None:
                return self.eval_node(entry["from"], entry["from_output"])
        return self.const(0.0)

    def fallback_node(self, node):
        """
        Незнакомая нода. Пробрасываем первый подключённый вход и оставляем
        TODO — молча подставлять правдоподобную математику нельзя.
        """
        for entry in node["inputs"]:
            if entry["from"] is not None:
                self.note_todo(node, "shader.todo.unsupported_passthrough",
                               input=entry["name"] or entry["index"])
                return self.eval_node(entry["from"], entry["from_output"])
        self.note_todo(node, "shader.todo.unsupported_no_inputs")
        return self.const(0.0)

    # -- параметры ----------------------------------------------------------

    def node_ScalarParameter(self, node):
        prop = self.add_scalar(node["props"].get("parameter_name"),
                               node["props"].get("default_value"))
        return self.emit("param_scalar", SCALAR, {"property": prop})

    def node_VectorParameter(self, node):
        prop = self.add_vector(node["props"].get("parameter_name"),
                               node["props"].get("default_value"))
        return self.emit("param_vector", VEC4, {"property": prop})

    def node_StaticBoolParameter(self, node):
        return self.node_StaticSwitchParameter(node)

    def node_StaticSwitchParameter(self, node):
        """
        Статический switch — это ветвление на этапе компиляции, поэтому
        превращается в shader_feature, а не в рантайм-lerp.
        """
        keyword = self.add_keyword(node["props"].get("parameter_name"))
        true_id = self.eval_input(node, "True", "const_a")
        false_id = self.eval_input(node, "False", "const_b")
        dim = max(self.dim_of(true_id), self.dim_of(false_id))
        return self.emit("switch_static", dim, {"keyword": keyword},
                         [self.cast(true_id, dim), self.cast(false_id, dim)])

    def node_StaticSwitch(self, node):
        """
        Непараметрический StaticSwitch: условие — константа времени компиляции.
        Значение берём из props и выбираем ветку прямо здесь, не оставляя
        мёртвого кода в шейдере.
        """
        taken = "True" if node["props"].get("value") else "False"
        fallback = "const_a" if taken == "True" else "const_b"
        return self.eval_input(node, taken, fallback)

    # -- текстуры и координаты ----------------------------------------------

    def node_TextureCoordinate(self, node):
        index = int(node["props"].get("coordinate_index") or 0)
        if index >= MAX_UV_SETS:
            self.note_todo(node, "shader.todo.uv_set_clamped",
                           requested=index, used=MAX_UV_SETS - 1)
            index = MAX_UV_SETS - 1
        self.ir.max_uv = max(self.ir.max_uv, index)
        u = float(node["props"].get("u_tiling") or 1.0)
        v = float(node["props"].get("v_tiling") or 1.0)
        return self.emit("uv", VEC2, {"index": index, "tiling": [u, v]},
                         comment="TexCoord%d" % index)

    def node_Panner(self, node):
        base = self.cast(self.eval_input(node, "Coordinate", "const_coordinate"), VEC2)
        speed_x = float(node["props"].get("speed_x") or 0.0)
        speed_y = float(node["props"].get("speed_y") or 0.0)
        return self.emit("panner", VEC2, {"speed": [speed_x, speed_y]}, [base],
                         comment="Panner")

    def node_Rotator(self, node):
        base = self.cast(self.eval_input(node, "Coordinate", "const_coordinate"), VEC2)
        centre_x = float(node["props"].get("center_x") or 0.5)
        centre_y = float(node["props"].get("center_y") or 0.5)
        speed = float(node["props"].get("speed") or 0.25)
        self.helper("UERotateAboutAxis")
        return self.emit("rotator", VEC2,
                         {"center": [centre_x, centre_y], "speed": speed}, [base],
                         comment="Rotator")

    def node_CustomRotator(self, node):
        base = self.cast(self.eval_input(node, "UVs", "const_coordinate"), VEC2)
        centre = self.cast(self.eval_input(node, "Rotation Center", None, 0.5), VEC2)
        angle = self.cast(self.eval_input(node, "Rotation Angle (0-1)", None, 0.0), SCALAR)
        self.helper("UERotateAboutAxis")
        return self.emit("call", VEC2, {"function": "UERotate2D"},
                         [base, centre, angle], comment="CustomRotator")

    def _uv_for(self, node):
        entry = self.input_of(node, "UVs")
        if entry is not None and entry["from"] is not None:
            return self.cast(self.eval_node(entry["from"], entry["from_output"]), VEC2)
        # Неподключённый вход UV — это UV0 без тайлинга. Отдельная операция,
        # чтобы бэкенд не заводил под неё лишнюю переменную.
        return self.emit("uv_default", VEC2, {"index": 0})

    def _sample_texture(self, node):
        prop, is_normal = self.add_texture(node["props"].get("parameter_name"),
                                           node["props"].get("sampler_type"),
                                           node["props"].get("texture"))
        uv = self._uv_for(node)
        # В графе может быть несколько нод, тянущих одну текстуру по одним и
        # тем же UV. Кэш по ноде их не схлопнет, а лишний семпл — это лишняя
        # выборка из памяти в каждом пикселе.
        key = (prop, uv, is_normal)
        if key not in self._samples:
            self._samples[key] = self.emit(
                "sample2d", VEC4, {"property": prop, "is_normal": is_normal}, [uv],
                comment="sample %s%s" % (prop, " (normal)" if is_normal else ""))
        return self._samples[key]

    def node_TextureSampleParameter2D(self, node):
        return self._sample_texture(node)

    def node_TextureSample(self, node):
        return self._sample_texture(node)

    def node_TextureObjectParameter(self, node):
        prop, _ = self.add_texture(node["props"].get("parameter_name"),
                                   node["props"].get("sampler_type"),
                                   node["props"].get("texture"))
        return self.emit("texture_ref", VEC4, {"property": prop})

    # -- арифметика ---------------------------------------------------------

    def _binary(self, node, operator, comment):
        a = self.eval_input(node, "A", "const_a")
        b = self.eval_input(node, "B", "const_b")
        dim = max(self.dim_of(a), self.dim_of(b))
        # В UE скаляр размножается на вектор, вектор к вектору идёт покомпонентно.
        return self.emit("binary", dim, {"operator": operator},
                         [self.cast(a, dim), self.cast(b, dim)], comment=comment)

    def node_Multiply(self, node):
        return self._binary(node, "*", "Multiply")

    def node_Add(self, node):
        return self._binary(node, "+", "Add")

    def node_Subtract(self, node):
        return self._binary(node, "-", "Subtract")

    def node_Divide(self, node):
        a = self.eval_input(node, "A", "const_a")
        b = self.eval_input(node, "B", "const_b")
        dim = max(self.dim_of(a), self.dim_of(b))
        return self.emit("divide", dim, {}, [self.cast(a, dim), self.cast(b, dim)],
                         comment="Divide")

    def binary_function(self, node, function):
        a = self.eval_input(node, "A", "const_a")
        b = self.eval_input(node, "B", "const_b")
        dim = max(self.dim_of(a), self.dim_of(b))
        if function == "cross":
            dim = VEC3
        return self.emit("call", dim, {"function": function},
                         [self.cast(a, dim), self.cast(b, dim)], comment=node["type"])

    def unary(self, node, function):
        value = self.eval_input(node, 0, "const_a")
        return self.emit("call", self.dim_of(value), {"function": function},
                         [value], comment=node["type"])

    def node_LinearInterpolate(self, node):
        a = self.eval_input(node, "A", "const_a")
        b = self.eval_input(node, "B", "const_b")
        alpha = self.eval_input(node, "Alpha", "const_alpha")
        dim = max(self.dim_of(a), self.dim_of(b))
        # Скалярная альфа не расширяется: lerp в HLSL и так примет её как есть,
        # а лишний cast засорил бы и код, и граф.
        if self.dim_of(alpha) > 1:
            alpha = self.cast(alpha, dim)
        return self.emit("lerp", dim, {}, [self.cast(a, dim), self.cast(b, dim), alpha],
                         comment="Lerp")

    def node_Clamp(self, node):
        # У Clamp первый вход безымянный, поэтому берём по индексу 0.
        value = self.eval_input(node, 0, "const_a")
        dim = self.dim_of(value)
        low = self.cast(self.eval_input(node, "Min", "min_default"), dim)
        high = self.cast(self.eval_input(node, "Max", "max_default", 1.0), dim)
        return self.emit("call", dim, {"function": "clamp"}, [value, low, high],
                         comment="Clamp")

    def node_Min(self, node):
        return self.binary_function(node, "min")

    def node_Max(self, node):
        return self.binary_function(node, "max")

    def node_AppendVector(self, node):
        a = self.eval_input(node, "A", "const_a")
        b = self.eval_input(node, "B", "const_b")
        dim = min(self.dim_of(a) + self.dim_of(b), VEC4)
        return self.emit("append", dim, {}, [a, b], comment="Append")

    def node_ComponentMask(self, node):
        value = self.eval_input(node, 0, "const_a")
        channels = "".join(c for c, key in zip("xyzw", ("r", "g", "b", "a"))
                           if node["props"].get(key))
        if not channels:
            channels = "x"
        return self.emit("swizzle", len(channels),
                         {"suffix": "." + channels, "temp": True}, [value],
                         comment="Mask .%s" % channels)

    def node_OneMinus(self, node):
        value = self.eval_input(node, 0, "const_a")
        return self.emit("one_minus", self.dim_of(value), {}, [value], comment="OneMinus")

    def node_Power(self, node):
        base = self.eval_input(node, "Base", "const_a")
        exponent = self.eval_input(node, "Exp", "const_exponent")
        dim = self.dim_of(base)
        return self.emit("power", dim, {}, [base, self.cast(exponent, dim)], comment="Power")

    def node_Dot(self, node):
        a = self.eval_input(node, "A", "const_a")
        b = self.eval_input(node, "B", "const_b")
        dim = max(self.dim_of(a), self.dim_of(b))
        return self.emit("call", SCALAR, {"function": "dot"},
                         [self.cast(a, dim), self.cast(b, dim)], comment="Dot")

    def node_Distance(self, node):
        a = self.eval_input(node, "A", "const_a")
        b = self.eval_input(node, "B", "const_b")
        dim = max(self.dim_of(a), self.dim_of(b))
        return self.emit("call", SCALAR, {"function": "distance"},
                         [self.cast(a, dim), self.cast(b, dim)], comment="Distance")

    def node_Length(self, node):
        value = self.eval_input(node, 0, "const_a")
        return self.emit("call", SCALAR, {"function": "length"}, [value], comment="Length")

    def node_Step(self, node):
        edge = self.eval_input(node, "Y", "const_a")
        value = self.eval_input(node, "X", "const_b")
        dim = max(self.dim_of(edge), self.dim_of(value))
        return self.emit("call", dim, {"function": "step"},
                         [self.cast(edge, dim), self.cast(value, dim)], comment="Step")

    def node_SmoothStep(self, node):
        low = self.eval_input(node, "Min", "const_min")
        high = self.eval_input(node, "Max", "const_max", 1.0)
        value = self.eval_input(node, "Value", "const_value")
        dim = self.dim_of(value)
        return self.emit("call", dim, {"function": "smoothstep"},
                         [self.cast(low, dim), self.cast(high, dim), value],
                         comment="SmoothStep")

    def node_If(self, node):
        """
        Нода If сравнивает A и B и выбирает одну из трёх веток. Ветку Equals
        Unreal считает необязательной: если её нет, равенство идёт в AGreaterThanB.
        """
        a = self.eval_input(node, "A", "const_a")
        b = self.eval_input(node, "B", "const_b")
        greater = self.eval_input(node, "A > B", "const_a_greater_than_b")
        less = self.eval_input(node, "A < B", "const_a_less_than_b")
        equals_entry = self.input_of(node, "A == B")
        equals = (self.eval_node(equals_entry["from"], equals_entry["from_output"])
                  if equals_entry is not None and equals_entry["from"] is not None
                  else greater)

        dim = max(self.dim_of(greater), self.dim_of(less), self.dim_of(equals))
        return self.emit("if", dim, {}, [self.cast(a, SCALAR), self.cast(b, SCALAR),
                                         self.cast(greater, dim), self.cast(equals, dim),
                                         self.cast(less, dim)], comment="If")

    def node_Fresnel(self, node):
        exponent = self.cast(self.eval_input(node, "ExponentIn", "exponent", 5.0), SCALAR)
        base = self.cast(self.eval_input(node, "BaseReflectFractionIn",
                                         "base_reflect_fraction", 0.04), SCALAR)
        normal_entry = self.input_of(node, "Normal")
        if normal_entry is not None and normal_entry["from"] is not None:
            normal = self.cast(self.eval_node(normal_entry["from"],
                                              normal_entry["from_output"]), VEC3)
        else:
            normal = self.builtin("PixelNormalWS")
        view = self.builtin("CameraVectorWS")
        self.helper("UEFresnel")
        return self.emit("call", SCALAR, {"function": "UEFresnel"},
                         [normal, view, exponent, base], comment="Fresnel")

    def node_SphereMask(self, node):
        a = self.cast(self.eval_input(node, "A", "const_a"), VEC3)
        b = self.cast(self.eval_input(node, "B", "const_b"), VEC3)
        radius = self.cast(self.eval_input(node, "Radius", "attenuation_radius", 256.0), SCALAR)
        hardness = self.cast(self.eval_input(node, "Hardness", "hardness_percent", 0.5), SCALAR)
        self.helper("UESphereMask")
        return self.emit("call", SCALAR, {"function": "UESphereMask"},
                         [a, b, radius, hardness], comment="SphereMask")

    def node_Desaturation(self, node):
        value = self.cast(self.eval_input(node, 0, "const_a"), VEC3)
        fraction = self.cast(self.eval_input(node, "Fraction", "const_b"), SCALAR)
        factors = node["props"].get("luminance_factors") or [0.3, 0.59, 0.11, 1.0]
        self.helper("UEDesaturation")
        return self.emit("call", VEC3, {"function": "UEDesaturation"},
                         [value, fraction, self.const(list(factors[:3]))],
                         comment="Desaturation")

    def node_DeriveNormalZ(self, node):
        xy = self.cast(self.eval_input(node, "InXY", "const_a"), VEC2)
        return self.emit("derive_normal_z", VEC3, {}, [xy], comment="DeriveNormalZ")

    def node_ConstantBiasScale(self, node):
        value = self.eval_input(node, 0, "const_a")
        bias = float(node["props"].get("bias") if node["props"].get("bias") is not None else 1.0)
        scale = float(node["props"].get("scale") if node["props"].get("scale") is not None else 0.5)
        dim = self.dim_of(value)
        biased = self.emit("binary", dim, {"operator": "+"},
                           [value, self.cast(self.const(bias), dim)], comment="Bias")
        return self.emit("binary", dim, {"operator": "*"},
                         [biased, self.cast(self.const(scale), dim)], comment="Scale")

    def node_Time(self, node):
        self.ir.builtins.add("time")
        return self.emit("builtin", SCALAR, {"field": "time"}, comment="Time")

    def node_BumpOffset(self, node):
        """
        Полноценный parallax в URP требует своего прохода; здесь делается
        то же, что делает сама нода в дешёвом режиме — сдвиг UV по высоте.
        """
        coordinate = self.cast(self.eval_input(node, "Coordinate", "const_coordinate"), VEC2)
        height = self.cast(self.eval_input(node, "Height", "const_a"), SCALAR)
        ratio = float(node["props"].get("height_ratio") or 0.05)
        view = self.builtin("CameraVectorWS")
        return self.emit("bump_offset", VEC2, {"ratio": ratio},
                         [coordinate, height, view], comment="BumpOffset")

    # -- константы ----------------------------------------------------------

    def node_Constant(self, node):
        return self.const(float(node["props"].get("r") or 0.0))

    def node_Constant2Vector(self, node):
        props = node["props"]
        return self.const([props.get("r") or 0.0, props.get("g") or 0.0])

    def node_Constant3Vector(self, node):
        value = node["props"].get("constant") or [0.0, 0.0, 0.0, 1.0]
        return self.const([value[0], value[1], value[2]])

    def node_Constant4Vector(self, node):
        value = node["props"].get("constant") or [0.0, 0.0, 0.0, 1.0]
        return self.const(list(value[:4]))

    # -- произвольный HLSL --------------------------------------------------

    def node_CustomExpression(self, node):
        """
        Нода Custom — это кусок HLSL, написанный руками автором материала.
        Переносим его как есть: угадывать, что там, нельзя, а выкидывать —
        значит потерять самую содержательную часть материала.
        """
        code = node["props"].get("code") or ""
        name = re.sub(r"[^A-Za-z0-9_]", "", str(node["props"].get("description") or "")) \
            or "Custom%d" % node["index"]
        dim = {"CMOT_FLOAT1": SCALAR, "CMOT_FLOAT2": VEC2,
               "CMOT_FLOAT3": VEC3, "CMOT_FLOAT4": VEC4}.get(
                   node["props"].get("output_type"), VEC4)

        inputs, signature = [], []
        for entry in node["inputs"]:
            if entry["from"] is None:
                continue
            value = self.eval_node(entry["from"], entry["from_output"])
            inputs.append(value)
            signature.append((entry["name"] or ("In%d" % entry["index"]),
                              self.dim_of(value)))

        arguments = ", ".join("%s %s" % (HLSL_TYPE[d], n) for n, d in signature)
        self.ir.helpers[name] = "%s %s(%s)\n{\n%s\n}" % (
            HLSL_TYPE[dim], name, arguments, code)
        return self.emit("call", dim, {"function": name}, inputs, comment="Custom")

    # -- вызовы функций материала -------------------------------------------

    def node_MaterialFunctionCall(self, node, requested_output=None):
        path = str(node["props"].get("material_function") or "")
        name = path.rsplit("/", 1)[-1]

        manual = MANUAL_FUNCTIONS.get(name)
        if manual is not None:
            return self._call_manual(node, manual, name)

        function_graph = self.functions.get(path)
        if function_graph is not None and path not in self._function_stack:
            return self._inline_function(node, path, function_graph, requested_output)

        self.note_todo(node, "shader.todo.function_not_ported", path=path or "<unnamed>")
        return self.fallback_node(node)

    def _call_manual(self, node, manual, name):
        function, input_names, out_dim, in_dims = manual
        arguments = []
        for input_name, want_dim in zip(input_names, in_dims):
            fallback = "const_a" if len(arguments) == 0 else "const_b"
            arguments.append(self.cast(self.eval_input(node, input_name, fallback), want_dim))
        self.helper(function)
        return self.emit("call", out_dim, {"function": function}, arguments, comment=name)

    def _inline_function(self, node, path, function_graph, requested_output=None):
        """
        Инлайн графа функции материала.

        Вход FunctionInput внутри функции — это значение соответствующего входа
        у ноды вызова, выход FunctionOutput — результат (тот, чьё имя запросил
        потребитель, — `requested_output`). Рекурсия по вложенным вызовам
        ограничена стеком путей: материал с функцией, вызывающей саму себя,
        встречается редко, но повесить конвертер он не должен.
        """
        outer_nodes, outer_cache, outer_scope = self.nodes, self._cache, self._scope

        scope = {}
        for entry in node["inputs"]:
            if entry["from"] is not None:
                scope[entry["name"]] = self.eval_node(entry["from"], entry["from_output"])

        self.nodes = function_graph["nodes"]
        self._cache = {}
        self._scope = scope
        self._function_stack.append(path)
        try:
            result = self._function_output(function_graph, node, requested_output)
        finally:
            self._function_stack.pop()
            self.nodes, self._cache, self._scope = outer_nodes, outer_cache, outer_scope
        return result

    def _function_output(self, function_graph, call_node, requested_output=None):
        outputs = [n for n in function_graph["nodes"] if n["type"] == "FunctionOutput"]
        if not outputs:
            self.note_todo(call_node, "shader.todo.function_not_ported",
                           path=function_graph.get("ue_path") or "<unnamed>")
            return self.const(0.0)

        # Функция может отдавать несколько выходов; берём тот, чьё имя совпало
        # с запрошенным, иначе первый — так же ведёт себя и сам Unreal.
        chosen = outputs[0]
        if requested_output is not None:
            for candidate in outputs:
                if candidate["props"].get("output_name") == requested_output:
                    chosen = candidate
                    break

        entry = chosen["inputs"][0] if chosen["inputs"] else None
        if entry is None or entry["from"] is None:
            return self.const(0.0)
        return self.eval_node(entry["from"], entry["from_output"])

    def node_FunctionInput(self, node):
        """Внутри инлайна — значение из вызова; вне его — заглушка по типу."""
        name = str(node["props"].get("input_name") or "")
        if self._scope and name in self._scope:
            return self._scope[name]

        entry = self.input_of(node, "Preview")
        if entry is not None and entry["from"] is not None:
            return self.eval_node(entry["from"], entry["from_output"])

        dim = {"FunctionInput_Scalar": SCALAR, "FunctionInput_Vector2": VEC2,
               "FunctionInput_Vector3": VEC3, "FunctionInput_Vector4": VEC4}.get(
                   node["props"].get("input_type"), SCALAR)
        return self.const([0.0] * dim, dim)

    def node_FunctionOutput(self, node):
        return self.passthrough(node)

    # -- атрибуты материала --------------------------------------------------

    def node_MakeMaterialAttributes(self, node):
        """
        Ноды атрибутов носят целый набор выходов поверхности. Разбирать их
        целиком незачем: важен тот вход, который спросили, и он берётся по
        имени в eval_output.
        """
        return self.passthrough(node)

    def node_GetMaterialAttributes(self, node):
        return self.passthrough(node)

    def node_SetMaterialAttributes(self, node):
        return self.passthrough(node)

    def node_BlendMaterialAttributes(self, node):
        a = self.eval_input(node, "A", "const_a")
        b = self.eval_input(node, "B", "const_b")
        alpha = self.eval_input(node, "Alpha", "const_alpha")
        dim = max(self.dim_of(a), self.dim_of(b))
        return self.emit("lerp", dim, {}, [self.cast(a, dim), self.cast(b, dim), alpha],
                         comment="BlendMaterialAttributes")

    # -- выходы материала ----------------------------------------------------

    OUTPUT_ROLES = [
        ("albedo", "MP_BASE_COLOR", VEC3),
        ("metallic", "MP_METALLIC", SCALAR),
        ("roughness", "MP_ROUGHNESS", SCALAR),
        ("emission", "MP_EMISSIVE_COLOR", VEC3),
        ("normal", "MP_NORMAL", VEC3),
        ("occlusion", "MP_AMBIENT_OCCLUSION", SCALAR),
        ("opacity", "MP_OPACITY", SCALAR),
        ("opacity_mask", "MP_OPACITY_MASK", SCALAR),
    ]

    def build(self):
        for role, key, dim in self.OUTPUT_ROLES:
            entry = self.graph["outputs"].get(key)
            if not entry or entry.get("from") is None:
                continue
            node_id = self.eval_node(entry["from"], entry.get("from_output"))
            self.ir.outputs[role] = self.cast(node_id, dim)

        # Параметры, которые есть в графе, но не доехали до шейдера. Обычно это
        # те, что питают только выходы, которых в Unity нет — например Specular:
        # в metallic-workflow у URP такого входа просто не существует.
        # StaticSwitch становится не свойством, а ключевым словом шейдера —
        # такие параметры перенесены, просто другим механизмом.
        as_keywords = set(self.ir.keywords.values())
        dropped = []
        for names in (self.graph.get("parameters") or {}).values():
            for ue_name in names:
                if sanitize(ue_name) not in self.ir.properties and str(ue_name) not in as_keywords:
                    dropped.append(str(ue_name))
        self.ir.dropped_parameters = sorted(set(dropped))
        return self.ir


def build_ir(graph, functions=None, log=None):
    return IRBuilder(graph, functions, log).build()
