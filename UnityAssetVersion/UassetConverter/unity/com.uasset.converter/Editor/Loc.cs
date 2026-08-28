using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace UassetImporter
{
    /// <summary>
    /// Локализация редакторной части.
    ///
    /// Файлы локалей общие с питоновской половиной конвертера и лежат в самом
    /// пакете. Пользовательская папка Assets/UassetLocales перекрывает пакетную:
    /// так переводчик правит свой файл, не конфликтуя с обновлением пакета.
    ///
    /// Формат плоский — массив пар вместо словаря, потому что JsonUtility
    /// словари не умеет, а тащить сторонний JSON-парсер ради переводов не стоит.
    /// </summary>
    public static class Loc
    {
        const string LanguageKey = "UassetImporter.Language";
        const string PackageLocales = "Packages/com.uasset.converter/Editor/Locales";
        const string ProjectLocales = "Assets/UassetLocales";
        const string Fallback = "en";

        [Serializable]
        class Entry
        {
            public string k;
            public string v;
        }

        [Serializable]
        class LocaleFile
        {
            public string code;
            public string name;
            public string fallback;
            public string[] authors;
            public Entry[] entries;
        }

        static readonly Dictionary<string, Dictionary<string, string>> Loaded =
            new Dictionary<string, Dictionary<string, string>>();
        static readonly Dictionary<string, string> DisplayNames = new Dictionary<string, string>();
        static readonly Dictionary<string, string> Fallbacks = new Dictionary<string, string>();
        static readonly HashSet<string> Warned = new HashSet<string>();

        static string _current;

        /// <summary>Код текущего языка. При первом обращении берётся из настроек редактора.</summary>
        public static string Current
        {
            get
            {
                if (_current == null)
                    SetLanguage(EditorPrefs.GetString(LanguageKey, ""));
                return _current;
            }
        }

        public static void SetLanguage(string code)
        {
            if (string.IsNullOrEmpty(code) || code == "auto")
                code = DetectSystemLanguage();

            _current = code;
            Load(code);
            EditorPrefs.SetString(LanguageKey, code);
        }

        /// <summary>Коды всех найденных локалей — и пакетных, и проектных.</summary>
        public static List<string> AvailableCodes()
        {
            var codes = new SortedSet<string>(StringComparer.Ordinal);
            foreach (var folder in LocaleFolders())
            {
                if (!Directory.Exists(folder)) continue;
                foreach (var file in Directory.GetFiles(folder, "*.json"))
                    codes.Add(Path.GetFileNameWithoutExtension(file));
            }
            if (codes.Count == 0) codes.Add(Fallback);
            return new List<string>(codes);
        }

        /// <summary>Название языка так, как оно подписано в самом файле локали.</summary>
        public static string DisplayName(string code)
        {
            Load(code);
            return DisplayNames.TryGetValue(code, out var name) && !string.IsNullOrEmpty(name)
                ? name : code;
        }

        /// <summary>Строка по ключу. Неизвестный ключ возвращается как есть — падать нельзя.</summary>
        public static string T(string key)
        {
            var table = Load(Current);
            if (table.TryGetValue(key, out var text) && !string.IsNullOrEmpty(text))
                return text;

            var fallback = Fallbacks.TryGetValue(Current, out var f) && !string.IsNullOrEmpty(f)
                ? f : Fallback;
            if (fallback != Current)
            {
                var fallbackTable = Load(fallback);
                if (fallbackTable.TryGetValue(key, out text) && !string.IsNullOrEmpty(text))
                    return text;
            }

            if (Current != Fallback && fallback != Fallback)
            {
                var defaultTable = Load(Fallback);
                if (defaultTable.TryGetValue(key, out text) && !string.IsNullOrEmpty(text))
                    return text;
            }

            // Диагностика самой локализации по-английски и без Loc.T:
            // сообщение о ненайденном ключе не должно само зависеть от того,
            // найдётся ли ключ.
            if (Warned.Add(key))
                Debug.LogWarning($"[Uasset] no translation for key '{key}'");
            return key;
        }

        /// <summary>
        /// Строка с подстановками. Плейсхолдеры именованные ({path}, {count}),
        /// а не позиционные: переводчик обязан иметь право переставить их местами.
        /// Аргументы идут парами: T("key", "path", value, "count", 3).
        /// </summary>
        public static string T(string key, params object[] namedArguments)
        {
            var text = T(key);
            if (namedArguments == null || namedArguments.Length == 0)
                return text;

            if (namedArguments.Length % 2 != 0)
            {
                Debug.LogWarning($"[Uasset] T('{key}') called with an odd number of arguments");
                return text;
            }

            var builder = new StringBuilder(text);
            for (var i = 0; i < namedArguments.Length; i += 2)
            {
                var name = "{" + namedArguments[i] + "}";
                var value = namedArguments[i + 1] == null ? "" : namedArguments[i + 1].ToString();
                builder.Replace(name, value);
            }
            return builder.ToString();
        }

        /// <summary>Сбрасывает кэш — нужно после правки файла локали на диске.</summary>
        public static void Reload()
        {
            Loaded.Clear();
            DisplayNames.Clear();
            Fallbacks.Clear();
            Warned.Clear();
        }

        // -- внутреннее ------------------------------------------------------

        static IEnumerable<string> LocaleFolders()
        {
            // Порядок важен: проектная папка идёт первой и перекрывает пакетную.
            yield return Path.GetFullPath(ProjectLocales);
            yield return Path.GetFullPath(PackageLocales);
        }

        static Dictionary<string, string> Load(string code)
        {
            if (Loaded.TryGetValue(code, out var cached))
                return cached;

            var table = new Dictionary<string, string>(StringComparer.Ordinal);
            Loaded[code] = table;

            foreach (var folder in LocaleFolders())
            {
                var path = Path.Combine(folder, code + ".json");
                if (!File.Exists(path)) continue;

                LocaleFile parsed;
                try
                {
                    parsed = JsonUtility.FromJson<LocaleFile>(File.ReadAllText(path));
                }
                catch (Exception error)
                {
                    Debug.LogWarning($"[Uasset] locale {path} could not be read: {error.Message}");
                    continue;
                }
                if (parsed?.entries == null) continue;

                foreach (var entry in parsed.entries)
                {
                    // Первый найденный файл выигрывает: проектная локаль
                    // перекрывает пакетную поключево, а не файлом целиком.
                    if (!string.IsNullOrEmpty(entry?.k) && !table.ContainsKey(entry.k))
                        table[entry.k] = entry.v;
                }

                if (!DisplayNames.ContainsKey(code) && !string.IsNullOrEmpty(parsed.name))
                    DisplayNames[code] = parsed.name;
                if (!Fallbacks.ContainsKey(code) && !string.IsNullOrEmpty(parsed.fallback))
                    Fallbacks[code] = parsed.fallback;
            }

            return table;
        }

        static string DetectSystemLanguage()
        {
            var codes = AvailableCodes();
            var guess = LanguageCodeOf(Application.systemLanguage);
            return codes.Contains(guess) ? guess : Fallback;
        }

        /// <summary>
        /// SystemLanguage -> код локали. Перечисляем только то, для чего перевод
        /// вообще может появиться; всё остальное честно уходит в английский.
        /// </summary>
        static string LanguageCodeOf(SystemLanguage language)
        {
            switch (language)
            {
                case SystemLanguage.Russian: return "ru";
                case SystemLanguage.Ukrainian: return "uk";
                case SystemLanguage.Belarusian: return "be";
                case SystemLanguage.German: return "de";
                case SystemLanguage.French: return "fr";
                case SystemLanguage.Spanish: return "es";
                case SystemLanguage.Portuguese: return "pt";
                case SystemLanguage.Italian: return "it";
                case SystemLanguage.Polish: return "pl";
                case SystemLanguage.Turkish: return "tr";
                case SystemLanguage.Japanese: return "ja";
                case SystemLanguage.Korean: return "ko";
                case SystemLanguage.ChineseSimplified: return "zh-Hans";
                case SystemLanguage.ChineseTraditional: return "zh-Hant";
                case SystemLanguage.Chinese: return "zh-Hans";
                default: return Fallback;
            }
        }
    }
}
