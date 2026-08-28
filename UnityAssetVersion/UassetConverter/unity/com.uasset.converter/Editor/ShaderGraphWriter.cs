using System;

namespace UassetImporter
{
    /// <summary>
    /// Точка входа для сборки .shadergraph — в основной сборке, у которой нет
    /// зависимости от пакета Shader Graph.
    ///
    /// Сама сборка графов живёт в UassetImporter.ShaderGraph и компилируется
    /// только там, где пакет установлен: ссылаться на него из основной сборки
    /// нельзя, иначе импортёр перестанет собираться в проектах без Shader Graph.
    /// Реализация подключает себя сюда сама, при загрузке домена.
    /// </summary>
    public static class ShaderGraphWriter
    {
        /// <summary>
        /// Ставится реализацией из UassetImporter.ShaderGraph.
        /// Аргументы: папка output, пайплайн, куда писать лог. Возвращает
        /// число записанных файлов.
        /// </summary>
        public static Func<string, string, Action<string>, int> Implementation;

        public static bool Available => Implementation != null;

        /// <summary>
        /// Собирает .shadergraph по graph_ir.json. Ноль означает «нечего делать»
        /// или «пакет не установлен» — и то, и другое не ошибка: HLSL-шейдеры
        /// при этом импортируются как обычно.
        /// </summary>
        public static int WriteAll(string outputDir, string pipeline, Action<string> log)
        {
            var graphs = GraphIrFile.Load(outputDir);
            if (graphs == null || graphs.graphs.Length == 0)
                return 0;

            if (Implementation == null)
            {
                log?.Invoke(Loc.T("unity.shadergraph.package_missing"));
                return 0;
            }

            return Implementation(outputDir, pipeline, log);
        }
    }
}
