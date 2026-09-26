"""Таблица и диаграмма донора на слайд не попадают (`Z-62`).

Данных таблиц и диаграмм движок не подменяет (`Z-12`), и незаполненное место
сохраняло их целиком: на девятке 26 сентября — таблица «Акцент 15 10, Строка
14 4» на двух колодах Education. Числа донора — числа, которых нет во входе
(Приложение 1 ТЗ, вопрос 4). Ранг такие раскладки обходит, сборка — страхует.
"""

from __future__ import annotations

import zipfile

from mimeo.analyze import analyze_template
from mimeo.compose import build
from mimeo.model import DeckPlan, Fill, PlannedSlide, PlanSource
from mimeo.plan import matching
from mimeo.plan.content import ContentBlock, ContentSection
from tests.fixtures.build_fixture import _slide, _text_sp, build_multi

TABLE = (
    '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="5" name="Table 5"/><p:cNvGraphicFramePr>'
    '<a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/></p:nvGraphicFramePr>'
    '<p:xfrm><a:off x="838200" y="2057400"/><a:ext cx="6000000" cy="2000000"/></p:xfrm>'
    '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
    '<a:tbl><a:tblPr firstRow="1"/><a:tblGrid><a:gridCol w="3000000"/><a:gridCol w="3000000"/>'
    '</a:tblGrid><a:tr h="500000">'
    + "".join(f'<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ru-RU"/><a:t>{t}</a:t>'
              '</a:r></a:p></a:txBody><a:tcPr/></a:tc>' for t in ("Акцент", "15"))
    + '</a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>'
)


def _template(tmp_path) -> str:
    """Три слайда: при меньшем числе раскладки выводятся из макетов (`ADR-0006`)."""
    title = _slide(_text_sp(2, "T", 838200, 457200, 10515600, 1325563, 4400, "Обложка"))
    with_table = _slide(_text_sp(2, "T", 838200, 457200, 10515600, 1325563, 4400, "Итоги квартала")
                        + TABLE)
    text = _slide(_text_sp(2, "T", 838200, 457200, 10515600, 1325563, 4400, "Раздел")
                  + _text_sp(3, "B", 838200, 2057400, 10515600, 3602038, 1800, "Абзац текста"))
    return build_multi(tmp_path / "t.pptx", [title, with_table, text])


def test_table_slot_costs_the_layout(tmp_path):
    """Место под таблицу штрафуется, заполняй его или нет: там будут чужие числа."""
    patterns = analyze_template(_template(tmp_path)).patterns.patterns
    table = next(p for p in patterns if any(s.content_type == "table" for s in p.slots))
    bare = type(table)(**{**table.__dict__, "slots": tuple(s for s in table.slots
                                                           if s.content_type != "table")})
    section = ContentSection(id="s", heading="Итоги квартала",
                             blocks=(ContentBlock(id="b", kind="paragraph", text="Выручка растёт"),))
    gap = matching.match(section, bare).score - matching.match(section, table).score
    assert round(gap, 4) == matching._PENALTY_DONOR_DATA


def test_donor_table_is_removed_and_said(tmp_path):
    template = _template(tmp_path)
    a = analyze_template(template)
    table = next(p for p in a.patterns.patterns if any(s.content_type == "table" for s in p.slots))
    title = next(s for s in table.slots if s.content_type == "text")
    plan = DeckPlan(source=PlanSource("t.md", a.design_system.source.sha256, a.patterns.source.sha256, ()),
                    slides=(PlannedSlide(index=0, pattern_id=table.id, reason="тест",
                                         fills=(Fill(slot_id=title.id, kind="text", text="Наш заголовок"),)),))
    out = tmp_path / "deck.pptx"
    report = build(template, plan, a.patterns, str(out))
    with zipfile.ZipFile(out) as z:
        xml = z.read("ppt/slides/slide1.xml").decode("utf-8")
    assert "<a:tbl>" not in xml and "Акцент" not in xml, "чужая таблица уехала на слайд"
    assert "Наш заголовок" in xml
    assert any("таблица донора убрана" in w and "Z-62" in w for w in report.warnings)
