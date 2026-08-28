using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using UnityEditor;

namespace UassetImporter
{
    /// <summary>
    /// Запуск конвертера как внешнего процесса без блокировки редактора.
    ///
    /// Process.WaitForExit() здесь недопустим: экспорт большого проекта идёт
    /// десятки минут, и всё это время Unity была бы заморожена. Поэтому вывод
    /// читается в фоновых потоках, складывается в очередь, а разбирается в
    /// EditorApplication.update — то есть в главном потоке, где только и можно
    /// трогать UI.
    ///
    /// Формат вывода один на все шаги — протокол UAC|, тот же, которым
    /// ue_export.py рапортует изнутри движка:
    ///     UAC|LOG|текст
    ///     UAC|PROGRESS|сделано/всего|подпись
    ///     UAC|DONE|код
    /// </summary>
    public class ProcessRunner
    {
        readonly ConcurrentQueue<string> _lines = new ConcurrentQueue<string>();
        Process _process;
        bool _pumping;

        public bool IsRunning => _process != null && !_process.HasExited;

        public Action<string> OnLog;
        public Action<int, int, string> OnProgress;
        public Action<int> OnFinished;

        public void Start(string fileName, string arguments, string workingDirectory)
        {
            if (IsRunning)
                throw new InvalidOperationException("a process is already running");

            var info = new ProcessStartInfo(fileName, arguments)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
                WorkingDirectory = workingDirectory ?? "",
            };
            // Дочерний Python по умолчанию пишет в кодировке системной локали,
            // а читаем мы UTF-8 — без этого не-латиница превращается в кашу.
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";

            _process = new Process { StartInfo = info, EnableRaisingEvents = true };
            _process.OutputDataReceived += (_, e) => { if (e.Data != null) _lines.Enqueue(e.Data); };
            _process.ErrorDataReceived += (_, e) => { if (e.Data != null) _lines.Enqueue(e.Data); };

            _process.Start();
            _process.BeginOutputReadLine();
            _process.BeginErrorReadLine();

            if (!_pumping)
            {
                EditorApplication.update += Pump;
                _pumping = true;
            }
        }

        /// <summary>Мягкая остановка: процесс убивается, шаг считается отменённым.</summary>
        public void Cancel()
        {
            if (!IsRunning) return;
            try
            {
                _process.Kill();
            }
            catch (Exception)
            {
                // Процесс мог завершиться сам между проверкой и убийством —
                // это не ошибка, а гонка, и делать с ней нечего.
            }
        }

        void Pump()
        {
            while (_lines.TryDequeue(out var line))
                Dispatch(line);

            if (_process == null || !_process.HasExited)
                return;

            // Хвост вывода мог не успеть дойти до очереди к моменту выхода.
            _process.WaitForExit();
            while (_lines.TryDequeue(out var line))
                Dispatch(line);

            var code = _process.ExitCode;
            _process.Dispose();
            _process = null;

            EditorApplication.update -= Pump;
            _pumping = false;

            OnFinished?.Invoke(code);
        }

        void Dispatch(string line)
        {
            if (line.StartsWith("UAC|PROGRESS|", StringComparison.Ordinal))
            {
                // UAC|PROGRESS|7/120|/Game/Foo/Bar
                var body = line.Substring("UAC|PROGRESS|".Length);
                var bar = body.IndexOf('|');
                if (bar > 0)
                {
                    var counts = body.Substring(0, bar).Split('/');
                    if (counts.Length == 2 &&
                        int.TryParse(counts[0], out var done) &&
                        int.TryParse(counts[1], out var total))
                    {
                        OnProgress?.Invoke(done, total, body.Substring(bar + 1));
                        return;
                    }
                }
                OnLog?.Invoke(body);
                return;
            }

            if (line.StartsWith("UAC|DONE|", StringComparison.Ordinal))
                return;   // код возврата берём из самого процесса, он честнее

            if (line.StartsWith("UAC|LOG|", StringComparison.Ordinal))
            {
                OnLog?.Invoke(line.Substring("UAC|LOG|".Length));
                return;
            }

            if (line.StartsWith("UAC|", StringComparison.Ordinal))
            {
                OnLog?.Invoke(line.Substring("UAC|".Length).Trim());
                return;
            }

            // Всё, что не по протоколу, — это ошибки самого интерпретатора
            // (traceback, SyntaxError). Их прятать нельзя: без них непонятно,
            // почему шаг упал.
            if (line.Trim().Length > 0)
                OnLog?.Invoke(line);
        }
    }
}
