"""Нормализация контента. Шаг Ш1 плана `PLAN-2.0`."""

from __future__ import annotations

from mimeo.plan.content import is_numeric_line, parse_markdown

SAMPLE = """# Заголовок колоды

Вводный абзац, который объясняет,
о чём вообще пойдёт речь дальше.

## Что умеет система

- Первый пункт
- Второй пункт
* Третий пункт

## Результаты

0.82

Точность прогноза на тестовой выборке

## Цитата

> Мы сократили время реакции втрое

![схема](img/scheme.png)
"""


def test_first_heading_becomes_title():
    doc = parse_markdown(SAMPLE)
    assert doc.title == "Заголовок колоды"
    assert doc.sections[0].heading == "Заголовок колоды"


def test_sections_split_on_headings():
    doc = parse_markdown(SAMPLE)
    assert [s.heading for s in doc.sections] == [
        "Заголовок колоды",
        "Что умеет система",
        "Результаты",
        "Цитата",
    ]


def test_paragraph_lines_are_joined():
    doc = parse_markdown(SAMPLE)
    block = doc.sections[0].blocks[0]
    assert block.kind == "paragraph"
    assert "\n" not in block.text
    assert block.text.startswith("Вводный абзац")


def test_mixed_bullet_markers_form_one_list():
    doc = parse_markdown(SAMPLE)
    lists = [b for b in doc.sections[1].blocks if b.kind == "list"]
    assert len(lists) == 1
    assert lists[0].items == ("Первый пункт", "Второй пункт", "Третий пункт")
    assert lists[0].units == 3


def test_lone_number_with_caption_becomes_metric():
    """Отдельная числовая строка — это плашка с числом, а не абзац."""
    doc = parse_markdown(SAMPLE)
    metrics = [b for b in doc.sections[2].blocks if b.kind == "metric"]
    assert len(metrics) == 1
    assert metrics[0].value == "0.82"
    assert metrics[0].label == "Точность прогноза на тестовой выборке"


def test_quote_and_image_are_recognised():
    doc = parse_markdown(SAMPLE)
    kinds = {b.kind for b in doc.sections[3].blocks}
    assert {"quote", "image"} <= kinds
    image = next(b for b in doc.sections[3].blocks if b.kind == "image")
    assert image.ref == "img/scheme.png"


def test_plain_text_without_headings_still_parses():
    doc = parse_markdown("Просто текст.\n\nВторой абзац.")
    assert len(doc.sections) == 1
    assert doc.sections[0].heading is None
    assert len(doc.sections[0].blocks) == 2


def test_numeric_line_detection():
    assert is_numeric_line("0.82")
    assert is_numeric_line("84%")
    assert is_numeric_line("1 200 000")
    assert not is_numeric_line("2026 год стал переломным")
    assert not is_numeric_line("абв")


def test_empty_input_gives_empty_document():
    doc = parse_markdown("")
    assert doc.sections == []
    assert doc.block_count == 0
