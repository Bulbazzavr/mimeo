"""Порядок детей `a:p` по схеме DrawingML: прогоны до `a:endParaRPr`.

Регрессия 12 сентября: наш текст попадал в файл **после** `a:endParaRPr`, и
PowerPoint переставал его показывать — слайд выходил пустым, хотя текст в XML
лежал. Ни `python-pptx`, ни наш разбор дефекта не видели: XML остаётся
синтаксически корректным, нарушается только порядок, заданный схемой
(`a:pPr?`, затем `a:r`/`a:br`/`a:fld`, и лишь потом `a:endParaRPr`).

Страдали слайды, синтезированные из макетов (`ADR-0006`): пустой абзац макета
состоит ровно из одного `a:endParaRPr`, и дописывание в конец ставило прогон за
ним. Подробности — `WORKLOG/2026-09-12-endpararpr.md`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from mimeo.compose.substitute import set_items, set_text
from mimeo.oxml.ns import qn

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def shape(paragraph: str) -> ET.Element:
    return ET.fromstring(
        f'<p:sp xmlns:p="{P}" xmlns:a="{A}"><p:txBody><a:bodyPr/><a:lstStyle/>'
        f"{paragraph}</p:txBody></p:sp>"
    )


def children(shape_el: ET.Element) -> list[str]:
    para = shape_el.find(f"{qn('p:txBody')}/{qn('a:p')}")
    return [child.tag.split("}")[-1] for child in para]


EMPTY_LAYOUT_PARAGRAPH = '<a:p><a:endParaRPr lang="en-US" sz="4400"/></a:p>'


def test_run_is_inserted_before_end_para_props():
    """Ровно тот случай, что давал пустой слайд."""
    sp = shape(EMPTY_LAYOUT_PARAGRAPH)
    assert set_text(sp, "Заголовок")
    assert children(sp) == ["r", "endParaRPr"]


def test_text_actually_lands_in_the_run():
    sp = shape(EMPTY_LAYOUT_PARAGRAPH)
    set_text(sp, "Заголовок")
    assert sp.find(f"{qn('p:txBody')}/{qn('a:p')}/{qn('a:r')}/{qn('a:t')}").text == "Заголовок"


def test_formatting_is_taken_from_end_para_props():
    """`a:endParaRPr` несёт оформление пустого абзаца — его и наследуем."""
    sp = shape(EMPTY_LAYOUT_PARAGRAPH)
    set_text(sp, "Заголовок")
    rpr = sp.find(f"{qn('p:txBody')}/{qn('a:p')}/{qn('a:r')}/{qn('a:rPr')}")
    assert rpr is not None and rpr.get("sz") == "4400"


def test_paragraph_without_end_props_still_works():
    sp = shape("<a:p><a:pPr/></a:p>")
    assert set_text(sp, "Текст")
    assert children(sp) == ["pPr", "r"]


def test_existing_run_keeps_its_place():
    sp = shape('<a:p><a:r><a:t>старое</a:t></a:r><a:endParaRPr lang="en-US"/></a:p>')
    set_text(sp, "новое")
    assert children(sp) == ["r", "endParaRPr"]
    assert sp.find(f"{qn('p:txBody')}/{qn('a:p')}/{qn('a:r')}/{qn('a:t')}").text == "новое"


def test_list_items_keep_the_order_too():
    """Списки идут через тот же `_ensure_run`, значит страдали так же."""
    sp = shape(EMPTY_LAYOUT_PARAGRAPH)
    assert set_items(sp, ("раз", "два"))
    body = sp.find(qn("p:txBody"))
    paragraphs = body.findall(qn("a:p"))
    assert len(paragraphs) == 2
    for para in paragraphs:
        tags = [child.tag.split("}")[-1] for child in para]
        assert tags.index("r") < tags.index("endParaRPr")
