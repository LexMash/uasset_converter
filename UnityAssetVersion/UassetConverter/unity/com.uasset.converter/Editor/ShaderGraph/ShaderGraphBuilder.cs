using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.Graphing;
using UnityEditor.ShaderGraph;
using UnityEditor.ShaderGraph.Internal;
using UnityEditor.ShaderGraph.Serialization;
using UnityEngine;

namespace UassetImporter.ShaderGraphExport
{
    /// <summary>
    /// Сборка .shadergraph из нейтрального IR.
    ///
    /// Файл не пишется руками как JSON, а строится через GraphData и
    /// сериализуется самим Shader Graph. Это и есть причина, по которой сборка
    /// живёт в Unity, а не в питоновской половине: формат внутренний и
    /// привязан к версии пакета, и единственный надёжный способ получить
    /// валидный файл — попросить сам пакет его записать.
    ///
    /// Операции IR, для которых есть готовая нода, становятся нодой. Всё
    /// остальное — Custom Function с тем же HLSL, который ушёл бы в .shader.
    /// Так покрытие обоих форматов одинаковое: не бывает материала, который
    /// переносится в HLSL, но не переносится в граф.
    /// </summary>
    public static class ShaderGraphBuilder
    {
        [InitializeOnLoadMethod]
        static void Register()
        {
            ShaderGraphWriter.Implementation = WriteAll;
        }

        static int WriteAll(string outputDir, string pipeline, Action<string> log)
        {
            var file = GraphIrFile.Load(outputDir);
            if (file == null) return 0;

            var folder = Path.Combine(outputDir, "Shaders");
            Directory.CreateDirectory(folder);

            var written = 0;
            foreach (var graph in file.graphs)
            {
                var name = ShortName(graph.shader);
                var path = Path.Combine(folder, name + ".shadergraph");
                try
                {
                    File.WriteAllText(path, Serialize(graph, pipeline));
                    written++;
                }
                catch (Exception error)
                {
                    log?.Invoke(Loc.T("unity.shadergraph.failed",
                                      "shader", graph.shader, "error", error.Message));
                }
            }
            return written;
        }

        static string ShortName(string shaderName)
        {
            if (string.IsNullOrEmpty(shaderName)) return "Shader";
            var slash = shaderName.LastIndexOf('/');
            return slash >= 0 ? shaderName.Substring(slash + 1) : shaderName;
        }

        // -- сборка одного графа ----------------------------------------------

        static string Serialize(IrGraph ir, string pipeline)
        {
            var graph = new GraphData { path = "Shader Graphs" };
            ApplyTarget(graph, ir, pipeline);

            var properties = AddProperties(graph, ir);
            var nodes = new Dictionary<int, AbstractMaterialNode>();
            var outputSlot = new Dictionary<int, int>();

            var context = new BuildContext
            {
                Graph = graph,
                Ir = ir,
                Properties = properties,
                Nodes = nodes,
                OutputSlot = outputSlot,
            };

            foreach (var node in ir.nodes)
                BuildNode(context, node);

            ConnectBlocks(context);

            GraphUtil.ConfigureBlocks(graph);
            return MultiJson.Serialize(graph);
        }

        class BuildContext
        {
            public GraphData Graph;
            public IrGraph Ir;
            public Dictionary<string, AbstractShaderProperty> Properties;
            public Dictionary<int, AbstractMaterialNode> Nodes;
            public Dictionary<int, int> OutputSlot;   // id ноды IR -> id выходного слота
        }

        static void ApplyTarget(GraphData graph, IrGraph ir, string pipeline)
        {
            // Целевой пайплайн выбирается по имени типа: прямая ссылка на
            // UniversalTarget привязала бы сборку ещё и к пакету URP, а он
            // в HDRP-проекте не стоит.
            var targetName = pipeline == "hdrp" ? "HDTarget" : "UniversalTarget";
            foreach (var type in TypeCache.GetTypesDerivedFrom<Target>())
            {
                if (type.Name != targetName) continue;
                if (!(Activator.CreateInstance(type) is Target target)) continue;
                graph.SetTargetActive(target);
                ApplySurfaceOptions(target, ir);
                return;
            }
            Debug.LogWarning($"[Uasset] Target {targetName} not found — the graph will be built without it");
        }

        /// <summary>
        /// Прозрачность, отсечение по альфе и двусторонность живут в полях
        /// самого Target и называются в URP и HDRP по-разному. Ставим их
        /// рефлексией по имени: так одна ветка кода обслуживает оба пайплайна,
        /// и отсутствие поля означает лишь то, что настройка останется
        /// дефолтной, а не что сборка упадёт.
        /// </summary>
        static void ApplySurfaceOptions(Target target, IrGraph ir)
        {
            SetMember(target, "alphaClip", ir.IsAlphaTested);
            SetMember(target, "alphaTest", ir.IsAlphaTested);
            SetMember(target, "twoSided", ir.two_sided);
            SetMember(target, "doubleSidedMode", ir.two_sided ? 1 : 0);

            if (ir.IsTransparent)
            {
                SetMember(target, "surfaceType", 1);      // Transparent
                SetMember(target, "surfaceMode", 1);
                SetMember(target, "zWriteControl", 0);
            }
        }

        static void SetMember(object instance, string name, object value)
        {
            var type = instance.GetType();
            var property = type.GetProperty(name,
                System.Reflection.BindingFlags.Instance |
                System.Reflection.BindingFlags.Public |
                System.Reflection.BindingFlags.NonPublic);
            try
            {
                if (property != null && property.CanWrite)
                {
                    property.SetValue(instance, Convert.ChangeType(value, Nullable.GetUnderlyingType(
                        property.PropertyType) ?? UnderlyingType(property.PropertyType)));
                    return;
                }
                var field = type.GetField(name,
                    System.Reflection.BindingFlags.Instance |
                    System.Reflection.BindingFlags.Public |
                    System.Reflection.BindingFlags.NonPublic);
                if (field != null)
                    field.SetValue(instance, Convert.ChangeType(value, UnderlyingType(field.FieldType)));
            }
            catch (Exception)
            {
                // Поля с таким именем в этой версии пакета нет или у него другой
                // тип. Настройка останется дефолтной — это лучше, чем упасть
                // посреди записи полусотни графов.
            }
        }

        static Type UnderlyingType(Type type)
        {
            return type.IsEnum ? Enum.GetUnderlyingType(type) : type;
        }

        // -- свойства ----------------------------------------------------------

        static Dictionary<string, AbstractShaderProperty> AddProperties(GraphData graph, IrGraph ir)
        {
            var map = new Dictionary<string, AbstractShaderProperty>();
            foreach (var entry in ir.properties)
            {
                AbstractShaderProperty property;
                switch (entry.kind)
                {
                    case "float":
                        property = new Vector1ShaderProperty
                        {
                            value = entry.@default != null && entry.@default.Length > 0
                                ? entry.@default[0] : 0f,
                        };
                        break;
                    case "color":
                        var rgba = entry.@default ?? new[] { 0f, 0f, 0f, 1f };
                        property = new ColorShaderProperty
                        {
                            value = new Color(At(rgba, 0), At(rgba, 1), At(rgba, 2), At(rgba, 3)),
                            // HDR вешаем только когда цвет действительно выходит
                            // за единицу — иначе обычный тинт получает лишний
                            // виджет интенсивности, как и в HLSL-ветке.
                            colorMode = At(rgba, 0) > 1f || At(rgba, 1) > 1f || At(rgba, 2) > 1f
                                ? ColorMode.HDR : ColorMode.Default,
                        };
                        break;
                    default:
                        property = new Texture2DShaderProperty
                        {
                            defaultType = entry.is_normal
                                ? Texture2DShaderProperty.DefaultType.Bump
                                : Texture2DShaderProperty.DefaultType.White,
                        };
                        break;
                }

                // Отображаемое имя — исходное из Unreal, ссылка — то же имя
                // свойства, что и в HLSL-шейдере. Иначе материалы, собранные
                // постобработкой, не найдут своих параметров.
                property.displayName = entry.ue_name;
                property.overrideReferenceName = entry.name;
                graph.AddGraphInput(property);
                map[entry.name] = property;
            }

            foreach (var keyword in ir.keywords)
            {
                var definition = new ShaderKeyword(KeywordType.Boolean)
                {
                    displayName = keyword.ue_name,
                    overrideReferenceName = keyword.name,
                    keywordScope = KeywordScope.Local,
                };
                graph.AddGraphInput(definition);
            }

            return map;
        }

        static float At(float[] values, int index)
        {
            return values != null && index < values.Length ? values[index] : 0f;
        }

        // -- ноды ---------------------------------------------------------------

        static void BuildNode(BuildContext context, IrNode node)
        {
            AbstractMaterialNode built;
            var outputSlotId = 0;

            switch (node.op)
            {
                case "todo":
                    // В графе TODO не нужен: он уже в отчёте, а нода-пустышка
                    // только мешала бы читать схему.
                    return;

                case "const":
                    built = Constant(node);
                    break;

                case "param_scalar":
                case "param_vector":
                case "texture_ref":
                    built = new PropertyNode();
                    ((PropertyNode)built).property = context.Properties.TryGetValue(
                        node.args.property, out var found) ? found : null;
                    break;

                case "uv":
                case "uv_default":
                    built = UvNode(node);
                    break;

                case "sample2d":
                    built = SampleNode(context, node, out outputSlotId);
                    break;

                case "binary":
                    built = BinaryNode(node.args.@operator);
                    break;

                case "divide":
                    built = new DivideNode();
                    break;

                case "lerp":
                    built = new LerpNode();
                    break;

                case "power":
                    built = new PowerNode();
                    break;

                case "one_minus":
                    built = new OneMinusNode();
                    break;

                case "append":
                    built = new CombineNode();
                    outputSlotId = OutputSlotForCombine(node.dim);
                    break;

                case "swizzle":
                    built = SwizzleNode(node);
                    break;

                case "cast":
                    built = CastNode(node);
                    break;

                case "if":
                    built = new BranchNode();
                    break;

                case "builtin":
                    built = BuiltinNode(node.args.field);
                    break;

                case "call":
                    built = CallNode(context, node);
                    break;

                default:
                    // Всё, для чего нет прямого аналога — panner, rotator,
                    // bump_offset, switch_static, — уезжает готовым HLSL.
                    built = CustomFunction(context, node);
                    break;
            }

            if (built == null) return;

            context.Graph.AddNode(built);
            context.Nodes[node.id] = built;
            context.OutputSlot[node.id] = outputSlotId;

            ConnectInputs(context, node, built);
        }

static AbstractMaterialNode Constant(IrNode node)
        {
            var value = node.args.value ?? new float[] { 0f };
            AbstractMaterialNode built;
            switch (node.dim)
            {
                case 1: built = new Vector1Node(); break;
                case 2: built = new Vector2Node(); break;
                case 3: built = new Vector3Node(); break;
                default: built = new Vector4Node(); break;
            }

            // Значение выставляется через входные слоты, а не через свойство
            // ноды: у Vector1..Vector4 оно называется по-разному, а слоты у
            // всех четырёх устроены одинаково — по компоненту на слот.
            var component = 0;
            foreach (var slot in built.GetInputSlots<MaterialSlot>())
            {
                if (slot is Vector1MaterialSlot scalar)
                    scalar.value = At(value, component);
                component++;
            }
            return built;
        }

        static AbstractMaterialNode UvNode(IrNode node)
        {
            var uv = new UVNode();
            var index = node.args.index;
            SetMember(uv, "uvChannel", Mathf.Clamp(index, 0, 3));
            return uv;
        }

        static AbstractMaterialNode SampleNode(BuildContext context, IrNode node, out int outputSlotId)
        {
            // Нода семплирования отдаёт RGBA нулевым слотом; в IR результат
            // всегда четырёхкомпонентный, поэтому берём именно его.
            outputSlotId = 0;
            var sample = new SampleTexture2DNode();
            if (node.args.is_normal)
                SetMember(sample, "textureType", 1);   // Normal

            if (context.Properties.TryGetValue(node.args.property, out var property))
            {
                var reference = new PropertyNode { property = property };
                context.Graph.AddNode(reference);
                // Слот текстуры у SampleTexture2DNode — первый после RGBA-выходов.
                TryConnect(context.Graph, reference, 0, sample, TextureInputSlot);
            }
            return sample;
        }

        const int TextureInputSlot = 5;

        static AbstractMaterialNode BinaryNode(string @operator)
        {
            switch (@operator)
            {
                case "+": return new AddNode();
                case "-": return new SubtractNode();
                default: return new MultiplyNode();
            }
        }

        static AbstractMaterialNode SwizzleNode(IrNode node)
        {
            var swizzle = new SwizzleNode();
            SetMember(swizzle, "convertedMask", (node.args.suffix ?? ".x").TrimStart('.'));
            return swizzle;
        }

        static AbstractMaterialNode CastNode(IrNode node)
        {
            // Приведение размерности в графе — это Combine (расширение) или
            // Swizzle (сужение). Shader Graph приводит типы на слотах сам,
            // поэтому чаще всего достаточно прозрачного узла.
            if (node.dim >= 3) return new CombineNode();
            return new SwizzleNode();
        }

        static int OutputSlotForCombine(int dim)
        {
            // CombineNode отдаёт RGBA, RGB, RG отдельными слотами.
            switch (dim)
            {
                case 2: return 3;
                case 3: return 2;
                default: return 1;
            }
        }

        static AbstractMaterialNode BuiltinNode(string field)
        {
            switch (field)
            {
                case "vertexColor": return new VertexColorNode();
                case "normalWS": return new NormalVectorNode();
                case "positionWS": return new PositionNode();
                case "viewDirWS": return new ViewDirectionNode();
                case "cameraPositionWS": return new CameraNode();
                case "screenPosition": return new ScreenPositionNode();
                case "time": return new TimeNode();
                case "objectPositionWS":
                case "objectScale":
                case "objectRadius":
                case "objectOrientation": return new ObjectNode();
                default: return new Vector1Node();
            }
        }

        static readonly Dictionary<string, Func<AbstractMaterialNode>> CallNodes =
            new Dictionary<string, Func<AbstractMaterialNode>>
            {
                { "abs", () => new AbsoluteNode() },
                { "saturate", () => new SaturateNode() },
                { "frac", () => new FractionNode() },
                { "floor", () => new FloorNode() },
                { "ceil", () => new CeilingNode() },
                { "round", () => new RoundNode() },
                { "sign", () => new SignNode() },
                { "sqrt", () => new SquareRootNode() },
                { "sin", () => new SineNode() },
                { "cos", () => new CosineNode() },
                { "tan", () => new TangentNode() },
                { "asin", () => new ArcsineNode() },
                { "acos", () => new ArccosineNode() },
                { "atan", () => new ArctangentNode() },
                { "atan2", () => new Arctangent2Node() },
                { "normalize", () => new NormalizeNode() },
                { "ddx", () => new DDXNode() },
                { "ddy", () => new DDYNode() },
                { "log2", () => new LogNode() },
                { "log10", () => new LogNode() },
                { "exp", () => new ExponentialNode() },
                { "exp2", () => new ExponentialNode() },
                { "min", () => new MinimumNode() },
                { "max", () => new MaximumNode() },
                { "fmod", () => new ModuloNode() },
                { "cross", () => new CrossProductNode() },
                { "dot", () => new DotProductNode() },
                { "distance", () => new DistanceNode() },
                { "length", () => new LengthNode() },
                { "step", () => new StepNode() },
                { "smoothstep", () => new SmoothstepNode() },
                { "clamp", () => new ClampNode() },
            };

        static AbstractMaterialNode CallNode(BuildContext context, IrNode node)
        {
            if (CallNodes.TryGetValue(node.args.function ?? "", out var factory))
                return factory();
            // Вызов хелпера — CheapContrast, UEFresnel, перенесённая функция
            // материала, кусок Custom из Unreal: всё это уже есть готовым HLSL.
            return CustomFunction(context, node);
        }

        /// <summary>
        /// Custom Function с телом из того же HLSL, что попал бы в .shader.
        /// Это универсальный запасной путь: покрытие графа не может отстать от
        /// покрытия HLSL, потому что берётся из него же.
        /// </summary>
        static AbstractMaterialNode CustomFunction(BuildContext context, IrNode node)
        {
            var custom = new CustomFunctionNode();
            var name = node.args.function;
            if (string.IsNullOrEmpty(name)) name = node.op + "_" + node.id;

            var body = HlslBodyFor(context, node, name, out var arguments);
            // Имена полей у CustomFunctionNode менялись между версиями пакета,
            // поэтому ставим их по имени: не нашлось — нода останется пустой,
            // но граф всё равно откроется, и место видно глазами.
            SetMember(custom, "functionName", name);
            SetMember(custom, "functionSource", "");
            SetMember(custom, "functionType", 1);      // String, а не файл
            SetMember(custom, "functionBody", body);
            custom.name = name;

            // Слоты описываются вручную: сколько входов пришло, столько и
            // заводим, плюс один выход нужной размерности.
            var ids = new List<int>();
            for (var i = 0; i < arguments.Count; i++)
            {
                custom.AddSlot(VectorSlot(i, "In" + i, arguments[i], SlotType.Input));
                ids.Add(i);
            }
            custom.AddSlot(VectorSlot(arguments.Count, "Out", node.dim, SlotType.Output));
            ids.Add(arguments.Count);
            // Слоты по умолчанию, оставшиеся от конструктора, надо убрать —
            // иначе у ноды окажутся лишние входы, которые никуда не ведут.
            custom.RemoveSlotsNameNotMatching(ids);

            context.OutputSlot[node.id] = arguments.Count;
            return custom;
        }

        static MaterialSlot VectorSlot(int id, string name, int dim, SlotType type)
        {
            switch (dim)
            {
                case 1: return new Vector1MaterialSlot(id, name, name, type, 0f);
                case 2: return new Vector2MaterialSlot(id, name, name, type, Vector2.zero);
                case 3: return new Vector3MaterialSlot(id, name, name, type, Vector3.zero);
                default: return new Vector4MaterialSlot(id, name, name, type, Vector4.zero);
            }
        }

        static string HlslBodyFor(BuildContext context, IrNode node, string name,
                                  out List<int> argumentDims)
        {
            argumentDims = new List<int>();
            var nodesById = new Dictionary<int, IrNode>();
            foreach (var candidate in context.Ir.nodes) nodesById[candidate.id] = candidate;

            var arguments = new List<string>();
            foreach (var input in node.inputs)
            {
                var dim = nodesById.TryGetValue(input, out var source) ? source.dim : 1;
                argumentDims.Add(dim);
                arguments.Add("In" + (arguments.Count));
            }

            var helper = FindHelper(context.Ir, node.args.function);
            var call = HlslExpression(node, arguments);
            var prefix = helper != null ? helper.code + "\n\n" : "";
            return prefix + string.Format("Out = {0};", call);
        }

        static IrHelper FindHelper(IrGraph ir, string function)
        {
            if (string.IsNullOrEmpty(function)) return null;
            foreach (var helper in ir.helpers)
                if (helper.name == function) return helper;
            return null;
        }

        /// <summary>
        /// Выражение операции на HLSL — то же, что пишет питоновский бэкенд.
        /// Продублировано осознанно и в одном месте: сюда попадают только те
        /// операции, для которых ноды в Shader Graph нет.
        /// </summary>
        static string HlslExpression(IrNode node, List<string> a)
        {
            switch (node.op)
            {
                case "panner":
                    var speed = node.args.tiling ?? node.args.value ?? new[] { 0f, 0f };
                    return $"{Arg(a, 0)} + _Time.y * float2({F(speed, 0)}, {F(speed, 1)})";
                case "rotator":
                    var centre = node.args.center ?? new[] { 0.5f, 0.5f };
                    return $"UERotate2D({Arg(a, 0)}, float2({F(centre, 0)}, {F(centre, 1)}), " +
                           $"_Time.y * {node.args.speed})";
                case "derive_normal_z":
                    return $"float3({Arg(a, 0)}, sqrt(saturate(1.0 - dot({Arg(a, 0)}, {Arg(a, 0)}))))";
                case "bump_offset":
                    return $"{Arg(a, 0)} + (({Arg(a, 1)}) - 0.5) * {node.args.ratio} * ({Arg(a, 2)}).xy";
                case "switch_static":
                    // Ветвление времени компиляции в графе выражается ключевым
                    // словом; здесь остаётся честный выбор по нему.
                    return $"#if defined({node.args.keyword})\n    {Arg(a, 0)}\n#else\n    {Arg(a, 1)}\n#endif";
                default:
                    return $"{node.args.function}({string.Join(", ", a)})";
            }
        }

        static string Arg(List<string> arguments, int index)
        {
            return index < arguments.Count ? arguments[index] : "0";
        }

        static string F(float[] values, int index)
        {
            return (values != null && index < values.Length ? values[index] : 0f)
                .ToString(System.Globalization.CultureInfo.InvariantCulture);
        }

        // -- связи ---------------------------------------------------------------

        static void ConnectInputs(BuildContext context, IrNode node, AbstractMaterialNode built)
        {
            var slotIds = InputSlotsOf(built, node);
            for (var i = 0; i < node.inputs.Length && i < slotIds.Count; i++)
            {
                if (!context.Nodes.TryGetValue(node.inputs[i], out var source)) continue;
                var sourceSlot = context.OutputSlot.TryGetValue(node.inputs[i], out var slot) ? slot : 0;
                TryConnect(context.Graph, source, sourceSlot, built, slotIds[i]);
            }
        }

        /// <summary>
        /// Идентификаторы входных слотов берём у самой ноды, а не из таблицы
        /// констант: номера слотов у встроенных нод менялись между версиями
        /// пакета, а порядок входов — нет.
        /// </summary>
        static List<int> InputSlotsOf(AbstractMaterialNode node, IrNode ir)
        {
            var ids = new List<int>();
            foreach (var slot in node.GetInputSlots<MaterialSlot>())
                ids.Add(slot.id);
            ids.Sort();

            // У ноды семплирования первый вход — UV, а слот текстуры уже занят
            // ссылкой на свойство.
            if (node is SampleTexture2DNode)
                ids.Remove(TextureInputSlot);
            return ids;
        }

        static void TryConnect(GraphData graph, AbstractMaterialNode from, int fromSlot,
                               AbstractMaterialNode to, int toSlot)
        {
            try
            {
                graph.Connect(from.GetSlotReference(fromSlot), to.GetSlotReference(toSlot));
            }
            catch (Exception)
            {
                // Слота с таким номером у ноды нет — связь просто не появится.
                // Граф при этом остаётся открываемым, и место обрыва видно
                // глазами, что гораздо полезнее исключения посреди записи.
            }
        }

        // -- выходы поверхности ---------------------------------------------------

        static void ConnectBlocks(BuildContext context)
        {
            var ir = context.Ir;

            // У unlit-материала цвет уходит в эмиссию: отдельного unlit-выхода
            // в Lit-стеке нет, а результат получается тот же.
            if (ir.IsUnlit)
            {
                ConnectBlock(context, BlockFields.SurfaceDescription.Emission,
                             ir.outputs.emission >= 0 ? ir.outputs.emission : ir.outputs.albedo);
            }
            else
            {
                ConnectBlock(context, BlockFields.SurfaceDescription.BaseColor, ir.outputs.albedo);
                ConnectBlock(context, BlockFields.SurfaceDescription.Metallic, ir.outputs.metallic);
                ConnectBlock(context, BlockFields.SurfaceDescription.Emission, ir.outputs.emission);
                ConnectBlock(context, BlockFields.SurfaceDescription.Occlusion, ir.outputs.occlusion);
                ConnectSmoothness(context, ir.outputs.roughness);
            }

            ConnectBlock(context, BlockFields.SurfaceDescription.NormalTS, ir.outputs.normal);

            if (ir.IsAlphaTested)
            {
                ConnectBlock(context, BlockFields.SurfaceDescription.Alpha, ir.outputs.opacity_mask);
                AddAlphaThreshold(context, ir.alpha_cutoff);
            }
            else if (ir.IsTransparent)
            {
                ConnectBlock(context, BlockFields.SurfaceDescription.Alpha, ir.outputs.opacity);
            }
        }

        /// <summary>
        /// Unreal хранит roughness, Shader Graph ждёт smoothness — между ними
        /// нужна инверсия, и она делается настоящей нодой, чтобы её было видно
        /// в редакторе.
        /// </summary>
        static void ConnectSmoothness(BuildContext context, int roughnessNode)
        {
            if (roughnessNode < 0) return;
            if (!context.Nodes.TryGetValue(roughnessNode, out var source)) return;

            var invert = new OneMinusNode();
            context.Graph.AddNode(invert);
            var sourceSlot = context.OutputSlot.TryGetValue(roughnessNode, out var slot) ? slot : 0;
            var inputs = new List<int>();
            foreach (var materialSlot in invert.GetInputSlots<MaterialSlot>())
                inputs.Add(materialSlot.id);
            if (inputs.Count > 0)
                TryConnect(context.Graph, source, sourceSlot, invert, inputs[0]);

            ConnectBlockNode(context, BlockFields.SurfaceDescription.Smoothness, invert, 0);
        }

        static void ConnectBlock(BuildContext context, BlockFieldDescriptor field, int nodeId)
        {
            if (nodeId < 0) return;
            if (!context.Nodes.TryGetValue(nodeId, out var source)) return;
            var slot = context.OutputSlot.TryGetValue(nodeId, out var found) ? found : 0;
            ConnectBlockNode(context, field, source, slot);
        }

        static void ConnectBlockNode(BuildContext context, BlockFieldDescriptor field,
                                     AbstractMaterialNode source, int sourceSlot)
        {
            var block = FindOrCreateBlock(context.Graph, field);
            if (block == null) return;
            foreach (var slot in block.GetInputSlots<MaterialSlot>())
            {
                TryConnect(context.Graph, source, sourceSlot, block, slot.id);
                return;
            }
        }

        static BlockNode FindOrCreateBlock(GraphData graph, BlockFieldDescriptor field)
        {
            foreach (var node in graph.GetNodes<BlockNode>())
                if (node.descriptor != null && node.descriptor.name == field.name)
                    return node;

            try
            {
                var block = new BlockNode();
                block.Init(field);
                graph.AddBlock(block, graph.fragmentContext, graph.fragmentContext.blocks.Count);
                return block;
            }
            catch (Exception)
            {
                // Блока с таким выходом в этом Target нет — например Occlusion
                // в unlit-подцели. Молча пропускаем: это не поломка графа.
                return null;
            }
        }

        static void AddAlphaThreshold(BuildContext context, float cutoff)
        {
            var block = FindOrCreateBlock(context.Graph, BlockFields.SurfaceDescription.AlphaClipThreshold);
            if (block == null) return;
            foreach (var slot in block.GetInputSlots<MaterialSlot>())
            {
                if (slot is Vector1MaterialSlot scalar)
                    scalar.value = cutoff;
                return;
            }
        }
    }
}
