# -*- coding: utf-8 -*-
"""
Слой 2б: IR -> готовый файл .shader.

Здесь нет ни одного решения о том, ЧТО считать: всё это уже сделано в
graph_ir. Здесь только рендер — как записать посчитанное на HLSL.

Правило разделения: операция, результат которой короткий и используется один
раз (константа, свойство, свизл, приведение размерности), подставляется по
месту; всё остальное получает переменную. Иначе одна текстура, у которой берут
три канала, семплилась бы трижды.
"""
from graph_ir import HLSL_TYPE, SCALAR, VEC2, VEC3, VEC4
from i18n import t
from shader_template import SHADER_TEMPLATE

# Операции без собственной переменной — их код подставляется в место
# использования. ComponentMask тоже свизл, но он приходит из настоящей ноды
# графа, и переменная под него делает код читаемым.
INLINE_OPS = {"const", "param_scalar", "param_vector", "texture_ref",
              "cast", "swizzle", "uv_default"}

# Как называется поле структуры входов и что в него класть в полном проходе.
SURFACE_INPUT_FIELDS = {
    "vertexColor":      (VEC4, "input.color", "input.color"),
    "normalWS":         (VEC3, "normalize(input.normalWS)", "normalize(input.normalWS)"),
    "positionWS":       (VEC3, "input.positionWS", "input.positionWS"),
    "cameraPositionWS": (VEC3, "GetCameraPositionWS()", "GetCameraPositionWS()"),
    "viewDirWS":        (VEC3, "SafeNormalize(GetCameraPositionWS() - input.positionWS)",
                               "SafeNormalize(GetCameraPositionWS() - input.positionWS)"),
    "objectPositionWS": (VEC3, "UNITY_MATRIX_M._m03_m13_m23", "UNITY_MATRIX_M._m03_m13_m23"),
    "objectScale":      (VEC3, "float3(length(UNITY_MATRIX_M._m00_m10_m20), "
                               "length(UNITY_MATRIX_M._m01_m11_m21), "
                               "length(UNITY_MATRIX_M._m02_m12_m22))",
                               "float3(length(UNITY_MATRIX_M._m00_m10_m20), "
                               "length(UNITY_MATRIX_M._m01_m11_m21), "
                               "length(UNITY_MATRIX_M._m02_m12_m22))"),
    "objectRadius":     (SCALAR, "length(UNITY_MATRIX_M._m00_m10_m20)",
                                 "length(UNITY_MATRIX_M._m00_m10_m20)"),
    "objectOrientation": (VEC3, "normalize(UNITY_MATRIX_M._m01_m11_m21)",
                                "normalize(UNITY_MATRIX_M._m01_m11_m21)"),
    "screenPosition":   (VEC4, "float4(GetNormalizedScreenSpaceUV(input.positionCS), 0, 1)",
                               "float4(0, 0, 0, 1)"),
    "pixelDepth":       (SCALAR, "input.positionCS.w", "input.positionCS.w"),
    # VFACE есть только в полном проходе; в теневом и глубинном сторона
    # поверхности на альфа-маску не влияет, поэтому там честная единица.
    "faceSign":         (SCALAR, "(facing > 0 ? 1.0 : -1.0)", "1.0"),
    "time":             (SCALAR, "_Time.y", "_Time.y"),
}


# ----------------------------------------------------------------------------
# Режимы смешивания
# ----------------------------------------------------------------------------

BLEND_SETUP = {
    "BLEND_OPAQUE":      dict(queue="Geometry",    rtype="Opaque",      blend="Off",
                              zwrite="On",  alpha_test=False, transparent=False),
    "BLEND_MASKED":      dict(queue="AlphaTest",   rtype="TransparentCutout", blend="Off",
                              zwrite="On",  alpha_test=True,  transparent=False),
    "BLEND_TRANSLUCENT": dict(queue="Transparent", rtype="Transparent", blend="SrcAlpha OneMinusSrcAlpha",
                              zwrite="Off", alpha_test=False, transparent=True),
    "BLEND_ADDITIVE":    dict(queue="Transparent", rtype="Transparent", blend="One One",
                              zwrite="Off", alpha_test=False, transparent=True),
    "BLEND_MODULATE":    dict(queue="Transparent", rtype="Transparent", blend="DstColor Zero",
                              zwrite="Off", alpha_test=False, transparent=True),
}


def blend_setup(ir):
    return BLEND_SETUP.get(ir.blend_mode, BLEND_SETUP["BLEND_OPAQUE"])


# ----------------------------------------------------------------------------
# Рендер выражений
# ----------------------------------------------------------------------------

class HlslEmitter:
    def __init__(self, ir):
        self.ir = ir
        self.expressions = {}     # id ноды -> готовое выражение
        self.statements = []
        self._temp = 0

    def render(self):
        for node in self.ir.nodes:
            if node.op == "todo":
                self._todo(node)
                self.expressions[node.id] = "0"
            elif node.op == "switch_static":
                self.expressions[node.id] = self._switch(node)
            elif node.op in INLINE_OPS and not node.args.get("temp"):
                self.expressions[node.id] = self._inline(node)
            else:
                self.expressions[node.id] = self._temporary(node)
        return "\n".join(self.statements)

    def value(self, node_id):
        return self.expressions[node_id]

    def _temporary(self, node):
        expression = self._expression(node)
        self._temp += 1
        name = "n%d" % self._temp
        tail = "  // %s" % node.comment if node.comment else ""
        self.statements.append("            %s %s = %s;%s"
                               % (HLSL_TYPE[node.dim], name, expression, tail))
        return name

    def _todo(self, node):
        """Пометка о неточном переносе — текстом на языке текущей локали."""
        message = t(node.args["message_key"], **(node.args.get("message_args") or {}))
        self.statements.append("            // TODO [%s #%d]: %s"
                               % (node.args["type"], node.args["node"], message))

    def _switch(self, node):
        """
        Статический switch — ветвление на этапе компиляции. Обе ветки объявляют
        одну и ту же переменную, поэтому её тип обязан совпадать; размерности
        уже приведены в IR.
        """
        true_value, false_value = (self.value(i) for i in node.inputs)
        self._temp += 1
        name = "n%d" % self._temp
        keyword = node.args["keyword"]
        self.statements.append("#if defined(%s)" % keyword)
        self.statements.append("            %s %s = %s;" % (HLSL_TYPE[node.dim], name, true_value))
        self.statements.append("#else")
        self.statements.append("            %s %s = %s;" % (HLSL_TYPE[node.dim], name, false_value))
        self.statements.append("#endif")
        return name

    def _inline(self, node):
        op = node.op
        if op == "const":
            values = node.args["value"]
            if node.dim == SCALAR:
                return "%g" % values[0]
            return "%s(%s)" % (HLSL_TYPE[node.dim], ", ".join("%g" % v for v in values))

        if op in ("param_scalar", "param_vector", "texture_ref"):
            return node.args["property"]

        if op == "uv_default":
            return "IN.uv%d" % node.args["index"]

        if op == "swizzle":
            return "(%s)%s" % (self.value(node.inputs[0]), node.args["suffix"])

        if op == "cast":
            return self._cast(self.value(node.inputs[0]), node.args["from"], node.dim)

        raise AssertionError("операция %r помечена как инлайновая, но не отрендерена" % op)

    @staticmethod
    def _cast(expression, from_dim, to_dim):
        if from_dim == to_dim:
            return expression
        if from_dim == SCALAR:
            # В HLSL конструктор вектора требует ровно столько аргументов,
            # сколько компонент: float3(x) — ошибка компиляции (это правило
            # GLSL, не HLSL). Выражения здесь всегда короткие — имя переменной,
            # свойство или число, — так что повторить его безопасно.
            return "%s(%s)" % (HLSL_TYPE[to_dim], ", ".join([expression] * to_dim))
        if from_dim > to_dim:
            return "(%s).%s" % (expression, "xyzw"[:to_dim])
        # расширение вектора — добиваем нулями, как это делает UE
        pad = ", ".join(["0"] * (to_dim - from_dim))
        return "%s(%s, %s)" % (HLSL_TYPE[to_dim], expression, pad)

    def _expression(self, node):
        op = node.op
        arguments = [self.value(i) for i in node.inputs]

        if op == "binary":
            return "%s %s %s" % (arguments[0], node.args["operator"], arguments[1])
        if op == "divide":
            # Ноль в знаменателе даёт NaN, который потом расползается по всему
            # кадру белыми пикселями. Unreal тут тоже подстраховывается.
            return "%s / max(%s, 1e-5)" % (arguments[0], arguments[1])
        if op == "power":
            return "pow(max(%s, 1e-5), %s)" % (arguments[0], arguments[1])
        if op == "one_minus":
            return "1.0 - %s" % arguments[0]
        if op == "lerp":
            return "lerp(%s, %s, %s)" % tuple(arguments)
        if op == "call":
            return "%s(%s)" % (node.args["function"], ", ".join(arguments))
        if op == "append":
            return "%s(%s, %s)" % (HLSL_TYPE[node.dim], arguments[0], arguments[1])
        if op == "if":
            a, b, greater, equals, less = arguments
            return "((%s) > (%s) ? %s : ((%s) < (%s) ? %s : %s))" % (
                a, b, greater, a, b, less, equals)
        if op == "sample2d":
            prop = node.args["property"]
            sample = "SAMPLE_TEXTURE2D(%s, sampler%s, %s)" % (prop, prop, arguments[0])
            if node.args.get("is_normal"):
                # Нормаль в Unity хранится упакованной; UnpackNormal разбирает
                # и DXT5nm, и BC5 — тем же способом, что и Lit-шейдер URP.
                return "float4(UnpackNormal(%s), 1.0)" % sample
            return sample
        if op == "uv":
            index = node.args["index"]
            u, v = node.args["tiling"]
            expression = "IN.uv%d" % index
            if (u, v) != (1.0, 1.0):
                expression = "%s * float2(%g, %g)" % (expression, u, v)
            return expression
        if op == "panner":
            x, y = node.args["speed"]
            return "%s + _Time.y * float2(%g, %g)" % (arguments[0], x, y)
        if op == "rotator":
            centre = node.args["center"]
            return "UERotate2D(%s, float2(%g, %g), _Time.y * %g)" % (
                arguments[0], centre[0], centre[1], node.args["speed"])
        if op == "derive_normal_z":
            return "float3(%s, sqrt(saturate(1.0 - dot(%s, %s))))" % (
                arguments[0], arguments[0], arguments[0])
        if op == "bump_offset":
            # Дешёвый parallax: сдвиг UV вдоль проекции взгляда на плоскость.
            return "%s + ((%s) - 0.5) * %g * (%s).xy" % (
                arguments[0], arguments[1], node.args["ratio"], arguments[2])
        if op == "swizzle":
            return "(%s)%s" % (arguments[0], node.args["suffix"])
        if op == "builtin":
            return "IN.%s" % node.args["field"]

        raise AssertionError("нет рендера для операции %r" % op)


# ----------------------------------------------------------------------------
# Блоки шейдера
# ----------------------------------------------------------------------------

def properties_block(ir):
    """Блок Properties. display name — исходное имя из Unreal, чтобы инспектор читался как там."""
    lines = []
    for prop, info in ir.properties.items():
        label = info["ue_name"].replace('"', "'")
        if info["kind"] == "float":
            lines.append('        %s("%s", Float) = %g' % (prop, label, info["default"]))
        elif info["kind"] == "color":
            value = info["default"]
            # [HDR] вешаем только если цвет действительно выходит за единицу —
            # иначе обычный тинт получает лишний виджет интенсивности.
            hdr = "[HDR]" if any(component > 1.0 for component in value[:3]) else ""
            lines.append('        %s%s("%s", Color) = (%g, %g, %g, %g)'
                         % (hdr, prop, label, value[0], value[1], value[2], value[3]))
        else:
            lines.append('        %s("%s", 2D) = "%s" {}' % (prop, label, info["default"]))
    for keyword, ue_name in ir.keywords.items():
        lines.append('        [Toggle(%s)]%s_Toggle("%s", Float) = 0'
                     % (keyword, keyword, ue_name.replace('"', "'")))
    return "\n".join(lines)


def cbuffer_block(ir):
    lines = []
    for prop, info in ir.properties.items():
        if info["kind"] == "float":
            lines.append("            float %s;" % prop)
        elif info["kind"] == "color":
            lines.append("            float4 %s;" % prop)
        else:
            lines.append("            float4 %s_ST;" % prop)
    # Свойства-переключатели тоже обязаны лежать в CBUFFER: SRP Batcher требует,
    # чтобы там были ВСЕ float-свойства материала, иначе батчинг отваливается.
    for keyword in ir.keywords:
        lines.append("            float %s_Toggle;" % keyword)
    return "\n".join(lines)


def texture_block(ir):
    lines = []
    for prop in ir.textures:
        lines.append("        TEXTURE2D(%s);" % prop)
        lines.append("        SAMPLER(sampler%s);" % prop)
    return "\n".join(lines)


def helpers_block(ir):
    """Только те функции, которые действительно вызваны в этом шейдере."""
    if not ir.helpers:
        return ""
    lines = ["", "// --- %s ---" % t("shader.helpers_header"), ""]
    for name in sorted(ir.helpers):
        lines.append(ir.helpers[name])
        lines.append("")
    return "\n".join(lines)


def keyword_pragmas(ir):
    return "\n".join("            #pragma shader_feature_local %s" % k for k in ir.keywords)


def uv_blocks(ir):
    """Поля и передача UV-наборов ровно по числу использованных."""
    count = ir.max_uv + 1
    attributes, varyings, transfer, fill = [], [], [], []
    for index in range(count):
        attributes.append("                float2 uv%d        : TEXCOORD%d;" % (index, index))
        varyings.append("                float2 uv%d         : TEXCOORD%d;" % (index, index + 3))
        transfer.append("                output.uv%d = input.uv%d;" % (index, index))
        fill.append("                surfaceInputs.uv%d = input.uv%d;" % (index, index))
    return ("\n".join(attributes), "\n".join(varyings),
            "\n".join(transfer), "\n".join(fill), count)


def lightmap_blocks(ir):
    """Развязка лайтмапного UV от материального.

    Статический лайтмап в Unity всегда лежит в меш-канале 1 (TEXCOORD1),
    динамический — в канале 2 (TEXCOORD2). Если материал уже занял эти каналы
    своими UV-наборами (uvN : TEXCOORDN), переиспользуем их; иначе объявляем
    выделенное поле. Иначе OUTPUT_LIGHTMAP_UV/Meta ссылались бы на несуществующее
    поле, и вариант LIGHTMAP_ON не компилировался бы (отсюда была магента).

    Возвращает:
      fwd_attribute  — доп. поле лайтмапа для Attributes прохода ForwardLit;
      static_src     — выражение статического лайтмап-UV (канал 1);
      dynamic_src    — выражение динамического лайтмап-UV (канал 2);
      meta_attribute — доп. поля лайтмапа для Attributes прохода Meta.
    """
    has_uv1 = ir.max_uv >= 1
    has_uv2 = ir.max_uv >= 2
    static_src = "input.uv1" if has_uv1 else "input.staticLightmapUV"
    dynamic_src = "input.uv2" if has_uv2 else "input.dynamicLightmapUV"

    fwd_attribute = "" if has_uv1 else "                float2 staticLightmapUV : TEXCOORD1;\n"

    meta_lines = []
    if not has_uv1:
        meta_lines.append("                float2 staticLightmapUV : TEXCOORD1;")
    if not has_uv2:
        meta_lines.append("                float2 dynamicLightmapUV : TEXCOORD2;")
    meta_attribute = "".join(line + "\n" for line in meta_lines)

    return fwd_attribute, static_src, dynamic_src, meta_attribute


def surface_inputs_block(ir, uv_count):
    lines = ["            float2 uv%d;" % index for index in range(uv_count)]
    for field in sorted(ir.builtins):
        dim = SURFACE_INPUT_FIELDS[field][0]
        lines.append("            %s %s;" % (HLSL_TYPE[dim], field))
    return "\n".join(lines)


def fill_block(ir, uv_fill, full_pass):
    lines = [uv_fill] if uv_fill else []
    for field in sorted(ir.builtins):
        _dim, forward, simple = SURFACE_INPUT_FIELDS[field]
        lines.append("                surfaceInputs.%s = %s;" % (field, forward if full_pass else simple))
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# Сборка файла
# ----------------------------------------------------------------------------

DEFAULTS = {
    "metallic": "0",
    "roughness": "0.5",
    "emission": "float3(0, 0, 0)",
    "normal": "float3(0, 0, 1)",
    "occlusion": "1",
    "opacity": "1",
    "opacity_mask": "1",
}


def generate(ir, shader_name):
    """IR -> текст .shader. Возвращает готовый файл целиком."""
    emitter = HlslEmitter(ir)
    body = emitter.render()

    setup = blend_setup(ir)
    unlit = (ir.shading_model or "").upper() == "MSM_UNLIT"

    def surface(role):
        node_id = ir.outputs.get(role)
        if node_id is None:
            # У unlit-материала весь цвет уходит в эмиссию. Если BaseColor не
            # подключён, дефолт обязан быть чёрным: белый прибавился бы к свечению.
            if role == "albedo":
                return "float3(0, 0, 0)" if unlit else "float3(1, 1, 1)"
            return DEFAULTS[role]
        return emitter.value(node_id)

    if setup["alpha_test"]:
        alpha = surface("opacity_mask")
    elif setup["transparent"]:
        alpha = surface("opacity")
    else:
        alpha = "1.0"

    if unlit:
        # У URP нет unlit-варианта UniversalFragmentPBR, поэтому весь цвет
        # уводим в emission при нулевом albedo — результат тот же, что unlit.
        albedo = "float3(0, 0, 0)"
        emission = "(%s) + (%s)" % (surface("albedo"), surface("emission"))
        metallic, roughness = "0", "1"
    else:
        albedo, emission = surface("albedo"), surface("emission")
        metallic, roughness = surface("metallic"), surface("roughness")

    todo_header = ""
    if ir.todos:
        todo_header = "\n// %s" % t("shader.header_todo", count=len(ir.todos))

    uv_attributes, uv_varyings, uv_transfer, uv_fill, uv_count = uv_blocks(ir)
    lm_attribute, lm_static_src, lm_dynamic_src, meta_lm_attributes = lightmap_blocks(ir)

    return SHADER_TEMPLATE.format(
        c_generated=t("shader.comment.generated"),
        c_source=t("shader.comment.source", path=ir.ue_path),
        c_nodes=t("shader.comment.nodes", count=ir.node_count),
        c_smoothness=t("shader.comment.smoothness"),
        todo_header=todo_header,
        shader_name=shader_name,
        properties=properties_block(ir),
        cutoff="%.4g" % ir.opacity_mask_clip_value,
        cull=0 if ir.two_sided else 2,
        rtype=setup["rtype"],
        queue=setup["queue"],
        cbuffer=cbuffer_block(ir),
        textures=texture_block(ir),
        helpers=helpers_block(ir),
        surface_inputs=surface_inputs_block(ir, uv_count),
        uv_attributes=uv_attributes,
        uv_varyings=uv_varyings,
        uv_transfer=uv_transfer,
        lightmap_attribute=lm_attribute,
        lightmap_uv_src=lm_static_src,
        lightmap_dynamic_src=lm_dynamic_src,
        meta_lightmap_attributes=meta_lm_attributes,
        forward_fill=fill_block(ir, uv_fill, full_pass=True),
        simple_fill=fill_block(ir, uv_fill, full_pass=False),
        body=body,
        albedo=albedo,
        metallic=metallic,
        roughness=roughness,
        emission=emission,
        normal=surface("normal"),
        occlusion=surface("occlusion"),
        alpha=alpha,
        blend=setup["blend"],
        zwrite=setup["zwrite"],
        keyword_pragmas=keyword_pragmas(ir),
        alpha_clip=("                clip(surface.alpha - _Cutoff);"
                    if setup["alpha_test"] else ""),
        output_alpha="surface.alpha" if setup["transparent"] else "1.0",
    )
