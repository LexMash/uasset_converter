using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

namespace UassetImporter
{
    // Модель graph_ir.json — нейтрального описания графа, которое пишет
    // питоновская половина конвертера.
    //
    // Формат IR намеренно плоский: ноды лежат массивом, связи — это индексы в
    // том же массиве. Так его читает и JsonUtility, которому недоступны словари,
    // и любой другой инструмент, которому однажды понадобится тот же граф.
    //
    // Живёт в основной сборке, а не рядом с writer-ом: сам по себе IR ни от
    // какого пакета не зависит, и окно конвертера должно уметь сказать
    // «графов нет» даже там, где Shader Graph не установлен.

    [Serializable]
    public class IrArgs
    {
        // JsonUtility не умеет произвольные словари, поэтому аргументы всех
        // операций перечислены полями. Лишние поля просто остаются нулевыми —
        // это дешевле, чем свой парсер ради десятка ключей.
        public string property;
        public string function;
        public string @operator;
        public string suffix;
        public string keyword;
        public string field;
        public string message_key;
        public string type;
        public int node;
        public int index;
        public int from;
        public bool is_normal;
        public bool temp;
        public float speed;
        public float ratio;
        public float[] value;
        public float[] tiling;
        public float[] center;
    }

    [Serializable]
    public class IrNode
    {
        public int id;
        public string op;
        public int dim;
        public int[] inputs;
        public IrArgs args;
        public string comment;
    }

    [Serializable]
    public class IrProperty
    {
        public string name;        // имя свойства в шейдере, например _BaseColor
        public string kind;        // float | color | texture
        public string ue_name;     // как параметр назывался в Unreal
        public float[] @default;   // для float здесь один элемент
        public bool is_normal;
    }

    [Serializable]
    public class IrKeyword
    {
        public string name;
        public string ue_name;
    }

    [Serializable]
    public class IrHelper
    {
        public string name;
        public string code;
    }

    [Serializable]
    public class IrOutputs
    {
        // Роли поверхности; -1 означает «не подключено», значение по умолчанию
        // подставит бэкенд.
        public int albedo = -1;
        public int metallic = -1;
        public int roughness = -1;
        public int emission = -1;
        public int normal = -1;
        public int occlusion = -1;
        public int opacity = -1;
        public int opacity_mask = -1;
    }

    [Serializable]
    public class IrGraph
    {
        public string ue_path;
        public string shader;
        public string pipeline;
        public string blend_mode;
        public string shading_model;
        public bool two_sided;
        public float alpha_cutoff;
        public int max_uv;
        public string[] builtins;
        public IrProperty[] properties;
        public IrKeyword[] keywords;
        public IrHelper[] helpers;
        public IrNode[] nodes;
        public IrOutputs outputs;
        public string[] dropped_parameters;

        public bool IsUnlit => shading_model != null &&
                               shading_model.ToUpperInvariant() == "MSM_UNLIT";

        public bool IsAlphaTested => blend_mode == "BLEND_MASKED";

        public bool IsTransparent => blend_mode == "BLEND_TRANSLUCENT" ||
                                     blend_mode == "BLEND_ADDITIVE" ||
                                     blend_mode == "BLEND_MODULATE";
    }

    [Serializable]
    public class GraphIrFile
    {
        public int version;
        public IrGraph[] graphs;

        /// <summary>
        /// Читает graph_ir.json из папки output. Возвращает null, если файла нет:
        /// это не ошибка, а обычное состояние, когда конвертер ещё не запускали
        /// или шаг шейдеров выключен.
        /// </summary>
        public static GraphIrFile Load(string outputDir)
        {
            var path = Path.Combine(outputDir ?? "", "graph_ir.json");
            if (!File.Exists(path)) return null;

            try
            {
                var parsed = JsonUtility.FromJson<GraphIrFile>(File.ReadAllText(path));
                if (parsed?.graphs == null) return null;
                foreach (var graph in parsed.graphs)
                {
                    graph.nodes = graph.nodes ?? Array.Empty<IrNode>();
                    graph.properties = graph.properties ?? Array.Empty<IrProperty>();
                    graph.keywords = graph.keywords ?? Array.Empty<IrKeyword>();
                    graph.helpers = graph.helpers ?? Array.Empty<IrHelper>();
                    graph.builtins = graph.builtins ?? Array.Empty<string>();
                    graph.outputs = graph.outputs ?? new IrOutputs();
                    foreach (var node in graph.nodes)
                    {
                        node.inputs = node.inputs ?? Array.Empty<int>();
                        node.args = node.args ?? new IrArgs();
                    }
                }
                return parsed;
            }
            catch (Exception error)
            {
                Debug.LogWarning($"[Uasset] graph_ir.json could not be read: {error.Message}");
                return null;
            }
        }

        public Dictionary<int, IrNode> NodesById(IrGraph graph)
        {
            var map = new Dictionary<int, IrNode>();
            foreach (var node in graph.nodes) map[node.id] = node;
            return map;
        }
    }
}
