#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проверка файлов локализации.

Запускать перед тем, как прислать перевод:

    python tools/lint_locales.py            проверить всё
    python tools/lint_locales.py --new de   создать заготовку для нового языка

Что проверяется:
  * файл — валидный JSON нужной формы;
  * набор ключей совпадает с эталонным en.json;
  * набор плейсхолдеров {...} в каждой строке совпадает с эталоном.

Последнее важнее, чем кажется: лишний или потерянный {path} — это не опечатка
в тексте, а исключение в момент показа сообщения. Оно всплывёт у пользователя
посреди двухчасового экспорта, а не у переводчика.

Ненулевой код возврата означает, что что-то не так — годится для CI.
"""
import argparse
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOCALES = os.path.join(ROOT, "unity", "com.uasset.converter", "Editor", "Locales")
REFERENCE = "en"

# {name} — считаем, {{ }} — экранированная скобка, её пропускаем.
PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def placeholders(text):
    return set(PLACEHOLDER_RE.findall(text or ""))


def read(code):
    path = os.path.join(LOCALES, "%s.json" % code)
    with io.open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def entries_of(data):
    result = {}
    for entry in data.get("entries") or []:
        key = entry.get("k")
        if key:
            result[key] = entry.get("v") or ""
    return result


def locale_codes():
    if not os.path.isdir(LOCALES):
        return []
    return sorted(name[:-5] for name in os.listdir(LOCALES) if name.endswith(".json"))


def check(code, reference):
    """Возвращает список проблем в виде строк."""
    problems = []
    try:
        data = read(code)
    except ValueError as error:
        return ["%s.json: не разбирается как JSON — %s" % (code, error)]
    except OSError as error:
        return ["%s.json: не читается — %s" % (code, error)]

    if not isinstance(data.get("entries"), list):
        problems.append("%s.json: нет массива entries" % code)
        return problems
    if not data.get("name"):
        problems.append("%s.json: не заполнено поле name (как язык подписан в меню)" % code)

    fallback = data.get("fallback") or REFERENCE
    if fallback != code and fallback not in locale_codes():
        problems.append("%s.json: fallback указывает на несуществующую локаль %r"
                        % (code, fallback))

    strings = entries_of(data)

    missing = sorted(set(reference) - set(strings))
    extra = sorted(set(strings) - set(reference))
    empty = sorted(key for key, value in strings.items() if not value.strip())

    for key in missing:
        problems.append("%s: нет перевода ключа %s" % (code, key))
    for key in extra:
        problems.append("%s: ключ %s не существует в %s.json — опечатка или "
                        "остаток от старой версии" % (code, key, REFERENCE))
    for key in empty:
        problems.append("%s: ключ %s переведён пустой строкой" % (code, key))

    for key in sorted(set(reference) & set(strings)):
        expected = placeholders(reference[key])
        actual = placeholders(strings[key])
        if expected != actual:
            problems.append(
                "%s: в ключе %s плейсхолдеры не совпадают — ожидались {%s}, найдены {%s}"
                % (code, key,
                   ", ".join(sorted(expected)) or "—",
                   ", ".join(sorted(actual)) or "—"))
    return problems


def make_template(code):
    """Заготовка нового языка: все ключи эталона с пустыми значениями."""
    path = os.path.join(LOCALES, "%s.json" % code)
    if os.path.exists(path):
        print("%s уже существует — не трогаю" % path)
        return 1

    reference = read(REFERENCE)
    data = {
        "code": code,
        "name": "TODO: как язык называется на самом себе, например Deutsch",
        "fallback": REFERENCE,
        "authors": ["TODO: твоё имя или ник"],
        "entries": [{"k": entry["k"], "v": ""} for entry in reference["entries"]],
    }
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print("создан %s" % path)
    print("Значения на английском лежат в %s.json — переводи по ключам." % REFERENCE)
    print("Плейсхолдеры в фигурных скобках менять нельзя, переставлять — можно.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Check locale files")
    parser.add_argument("--new", metavar="CODE", help="create a template for a new language")
    parser.add_argument("--only", metavar="CODE", help="check just one locale")
    args = parser.parse_args()

    if not os.path.isdir(LOCALES):
        print("нет папки локалей: %s" % LOCALES)
        return 2

    if args.new:
        return make_template(args.new)

    try:
        reference = entries_of(read(REFERENCE))
    except (OSError, ValueError) as error:
        print("эталон %s.json не прочитан: %s" % (REFERENCE, error))
        return 2

    codes = [args.only] if args.only else [c for c in locale_codes() if c != REFERENCE]
    if not codes:
        print("кроме эталона локалей нет — проверять нечего")
        return 0

    total = 0
    for code in codes:
        problems = check(code, reference)
        total += len(problems)
        if problems:
            print("%s: проблем %d" % (code, len(problems)))
            for problem in problems:
                print("  %s" % problem)
        else:
            print("%s: в порядке (%d ключей)" % (code, len(reference)))

    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
