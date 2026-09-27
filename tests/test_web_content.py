"""Контент-пакет в вебе: текст файлом и картинки к нему (`Z-73`).

ТЗ, раздел 2, п. 1: «импорт… контент-пакетов». Картинки ложатся в каталог
прогона рядом с текстом, и движок находит их по имени в тексте. Здесь — то,
что решает сервер: имя, которое движок узнает в прозе, и путь с каталогами,
названный в тексте, — внутри прогона и никуда наружу.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "web"))

import serve                                                   # noqa: E402
from mimeo.plan.content import _PATH_IN_PROSE                  # noqa: E402


def test_uploaded_name_is_one_the_engine_finds_in_prose():
    """Пробел и скобки обрывают путь в прозе — в имени их заменяет «_»."""
    name = serve.safe_image_name("схема стадий (итог).png")
    assert name == "схема_стадий_итог_.png"
    assert _PATH_IN_PROSE.findall(f"Как устроено — на схеме {name}.") == [name]
    assert serve.safe_image_name("../../x.png") == "x.png", "каталоги из имени не берутся"


def test_web_and_engine_agree_on_what_a_picture_path_is():
    """Признак пути продублирован в вебе (`import mimeo` там запрещён) — и
    обязан совпадать с движком."""
    assert serve._PATH_IN_PROSE.pattern == _PATH_IN_PROSE.pattern
    assert serve.ALLOWED_IMAGES == (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


def test_path_named_in_text_gets_the_upload_and_never_leaves_the_run(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "схема.png").write_bytes(b"png")
    (run / "рост.png").write_bytes(b"png2")
    text = ("Схема — img/схема.png, рост — рост.png, чужое — ../../etc/схема.png "
            "и C:/x/схема.png, неизвестное — img/нет.png.")
    placed = serve.place_uploads(text, str(run), ["схема.png", "рост.png"])
    assert placed == ["img/схема.png"]
    assert (run / "img" / "схема.png").read_bytes() == b"png"
    assert not (tmp_path / "etc").exists()
