# -*- coding: utf-8 -*-
"""Пути: тесты импортируют и модули конвертера, и свои хелперы из tests/."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

for path in (ROOT, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)


# Язык тестов фиксируем: эталоны шейдеров содержат локализованные комментарии,
# и они не должны зависеть от системной локали того, кто запускает тесты.
import i18n  # noqa: E402
i18n.set_language("en")
