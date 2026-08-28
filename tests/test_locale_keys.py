# -*- coding: utf-8 -*-
"""
Ключи локализации, которые использует C#-сторона, обязаны существовать.

Опечатка в Loc.T("verify.summry") не ломает сборку и не видна в тестах на
Python — она всплывает только предупреждением в консоли Unity у пользователя.
Этот тест закрывает дыру со стороны, где её можно закрыть дёшево: разбирает
вызовы регуляркой и сверяет с эталонной локалью.

Он же проверяет второе, что тут легко перепутать, — что каждому плейсхолдеру
в строке передан аргумент. Забытая пара оставит в логе буквальный `{count}`.
"""
import io
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNITY = os.path.join(ROOT, "unity")
REFERENCE = os.path.join(UNITY, "com.uasset.converter", "Editor", "Locales", "en.json")

# Loc.T("key") и Loc.T("key", "name", value, ...) — забираем ключ и хвост до
# закрывающей скобки того же уровня. Вложенные скобки в аргументах бывают
# (например $"{a} {b}" и тернарники), поэтому баланс считаем вручную.
CALL_RE = re.compile(r'Loc\.T\(\s*"([^"]+)"')
PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def cs_files():
    found = []
    for folder, _dirs, names in os.walk(UNITY):
        for name in names:
            if name.endswith(".cs"):
                found.append(os.path.join(folder, name))
    return sorted(found)


def reference_strings():
    with io.open(REFERENCE, encoding="utf-8") as fh:
        data = json.load(fh)
    return {entry["k"]: entry["v"] for entry in data["entries"]}


def call_arguments(text, start):
    """
    Текст аргументов вызова, начиная от открывающей скобки Loc.T.
    Считаем баланс скобок, чтобы не оборваться на вложенном вызове.
    """
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:index]
    return ""


def calls():
    """[(файл, ключ, текст аргументов), ...] по всем .cs в пакете."""
    result = []
    for path in cs_files():
        text = io.open(path, encoding="utf-8").read()
        for match in CALL_RE.finditer(text):
            opening = text.index("(", match.start())
            result.append((os.path.relpath(path, ROOT), match.group(1),
                           call_arguments(text, opening)))
    return result


ALL_CALLS = calls()


def test_calls_are_found():
    """Страховка от самого теста: сломанная регулярка не должна тихо пройти."""
    assert len(ALL_CALLS) > 50, "вызовов Loc.T найдено подозрительно мало"


@pytest.mark.parametrize("path,key", sorted({(c[0], c[1]) for c in ALL_CALLS}))
def test_key_exists(path, key):
    strings = reference_strings()
    assert key in strings, "%s: ключа %r нет в en.json" % (path, key)


def test_placeholders_have_arguments():
    """
    Каждому {плейсхолдеру} обязан соответствовать аргумент с таким именем.

    Имена аргументов ищем строковыми литералами в хвосте вызова — именно так
    их и передаёт Loc.T(key, "name", value, ...). Значения при этом могут быть
    какими угодно выражениями, и разбирать их не нужно.
    """
    strings = reference_strings()
    problems = []

    for path, key, arguments in ALL_CALLS:
        if key not in strings:
            continue                      # об этом ругается тест выше
        expected = set(PLACEHOLDER_RE.findall(strings[key]))
        if not expected:
            continue
        passed = set(re.findall(r'"([a-zA-Z_][a-zA-Z0-9_]*)"', arguments))
        missing = expected - passed
        if missing:
            problems.append("%s: %s — не передано %s"
                            % (path, key, ", ".join(sorted(missing))))

    assert not problems, "\n".join(problems)
