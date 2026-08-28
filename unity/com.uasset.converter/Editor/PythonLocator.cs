using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;

namespace UassetImporter
{
    /// <summary>
    /// Поиск интерпретатора Python и проверка того, что нужные пакеты стоят.
    ///
    /// Шаг экспорта идёт через Python, встроенный в сам Unreal, и внешний
    /// интерпретатор ему не нужен. А вот генерация шейдеров и постобработка —
    /// это обычные скрипты, и их запускает системный Python. Пользователю надо
    /// сказать об этом ОДИН раз и внятно, а не уронить конвертацию на середине
    /// сообщением «файл не найден».
    /// </summary>
    public static class PythonLocator
    {
        /// <summary>Пакеты, без которых постобработка не соберёт маски и нормали.</summary>
        public static readonly string[] RequiredPackages = { "PIL", "numpy" };

        public class Result
        {
            public string Path;                 // найденный интерпретатор, или null
            public List<string> MissingPackages = new List<string>();
            public string Error;                // почему не вышло, если Path == null

            public bool Ok => !string.IsNullOrEmpty(Path) && MissingPackages.Count == 0;
        }

        /// <summary>
        /// Ищет Python: сначала явно указанный, потом PATH, потом лаунчер py,
        /// потом типовые места установки. Первый, который отвечает версией 3.x
        /// и умеет импортировать нужные пакеты, выигрывает.
        /// </summary>
        public static Result Locate(string configured)
        {
            var result = new Result();
            var candidates = Candidates(configured);
            Result firstWorking = null;

            foreach (var candidate in candidates)
            {
                if (!IsPython3(candidate.file, candidate.arguments)) continue;

                var missing = MissingPackagesOf(candidate.file, candidate.arguments);
                var attempt = new Result { Path = Display(candidate), MissingPackages = missing };

                // Интерпретатор с пакетами лучше интерпретатора без них, поэтому
                // не хватаемся за первый попавшийся, если он неполный.
                if (missing.Count == 0)
                    return attempt;
                if (firstWorking == null)
                    firstWorking = attempt;
            }

            if (firstWorking != null)
                return firstWorking;

            result.Error = Loc.T("unity.convert.python_not_found");
            return result;
        }

        /// <summary>Команда установки пакетов — её показываем пользователю дословно.</summary>
        public static string InstallCommand(string pythonPath, string converterDir)
        {
            var requirements = Path.Combine(converterDir ?? "", "requirements.txt");
            return $"\"{pythonPath}\" -m pip install -r \"{requirements}\"";
        }

        struct Candidate
        {
            public string file;
            public string arguments;   // для лаунчера py нужен ключ -3
        }

        static string Display(Candidate candidate)
        {
            return string.IsNullOrEmpty(candidate.arguments)
                ? candidate.file
                : candidate.file + " " + candidate.arguments;
        }

        static IEnumerable<Candidate> Candidates(string configured)
        {
            if (!string.IsNullOrEmpty(configured))
                yield return new Candidate { file = configured, arguments = "" };

            yield return new Candidate { file = "python", arguments = "" };
            yield return new Candidate { file = "python3", arguments = "" };
            // Лаунчер Windows: единственный надёжный способ получить именно 3.x,
            // когда в PATH висит заглушка из Microsoft Store.
            yield return new Candidate { file = "py", arguments = "-3" };

            foreach (var path in CommonInstallPaths())
                yield return new Candidate { file = path, arguments = "" };
        }

        static IEnumerable<string> CommonInstallPaths()
        {
            var roots = new List<string>();
            var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (!string.IsNullOrEmpty(localAppData))
                roots.Add(Path.Combine(localAppData, "Programs", "Python"));
            roots.Add(@"C:\Python");
            roots.Add(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles));

            foreach (var root in roots)
            {
                if (string.IsNullOrEmpty(root) || !Directory.Exists(root)) continue;
                string[] folders;
                try
                {
                    folders = Directory.GetDirectories(root, "Python3*");
                }
                catch (Exception)
                {
                    continue;
                }
                // От свежих к старым: у новой версии больше шансов оказаться рабочей.
                Array.Sort(folders, StringComparer.OrdinalIgnoreCase);
                Array.Reverse(folders);
                foreach (var folder in folders)
                {
                    var exe = Path.Combine(folder, "python.exe");
                    if (File.Exists(exe)) yield return exe;
                }
            }
        }

        static bool IsPython3(string file, string arguments)
        {
            var output = Capture(file, Join(arguments, "--version"));
            return output != null && output.Contains("Python 3");
        }

        static List<string> MissingPackagesOf(string file, string arguments)
        {
            var missing = new List<string>();
            foreach (var package in RequiredPackages)
            {
                var script = $"import importlib.util,sys; " +
                             $"sys.stdout.write('yes' if importlib.util.find_spec('{package}') else 'no')";
                var output = Capture(file, Join(arguments, "-c \"" + script + "\""));
                if (output == null || !output.Contains("yes"))
                    missing.Add(package == "PIL" ? "Pillow" : package);
            }
            return missing;
        }

        static string Join(string arguments, string tail)
        {
            return string.IsNullOrEmpty(arguments) ? tail : arguments + " " + tail;
        }

        /// <summary>
        /// Запускает кандидата и возвращает его вывод, или null если не завёлся.
        /// Таймаут обязателен: несуществующая заглушка python.exe из Microsoft
        /// Store умеет висеть, открывая магазин, и без таймаута повесила бы редактор.
        /// </summary>
        static string Capture(string file, string arguments)
        {
            try
            {
                var info = new ProcessStartInfo(file, arguments)
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    StandardOutputEncoding = Encoding.UTF8,
                    StandardErrorEncoding = Encoding.UTF8,
                };
                using (var process = Process.Start(info))
                {
                    if (process == null) return null;
                    var output = process.StandardOutput.ReadToEnd() +
                                 process.StandardError.ReadToEnd();
                    if (!process.WaitForExit(5000))
                    {
                        try { process.Kill(); } catch (Exception) { }
                        return null;
                    }
                    return output;
                }
            }
            catch (Exception)
            {
                // Кандидата просто нет — это норма, перебираем дальше.
                return null;
            }
        }
    }
}
