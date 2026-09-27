"""Таблицы и диаграммы (`Z-32`, `PLAN-10.0`, `ADR-0026`).

Данные находит модель, форму и числа проверяет код, место — фигура шаблона,
объект — нативный PowerPoint. Живой модели не нужно: ответ модели здесь —
словарь, собранный руками. Собранный файл читает `python-pptx` — независимый
читатель: он написан не нами.
"""

from __future__ import annotations

import io
import os
import zipfile

import pytest

from mimeo.analyze import analyze_template
from mimeo.compose import build, inspect
from mimeo.compose import visual as compose_visual
from mimeo.plan import outline, parse_markdown, plan_deck, visual
from mimeo.plan.content import ContentBlock, ContentDoc, ContentSection
from mimeo.plan.matching import visual_host


# --- числа текста ------------------------------------------------------------


@pytest.mark.parametrize("text, value", [
    ("41 200", 41200.0), ("41 200", 41200.0), ("3,9", 3.9), ("3.9", 3.9),
    ("14%", 14.0), ("−5", -5.0), ("1,2 млн", 1.2), ("0", 0.0),
])
def test_numbers_are_read_as_written(text, value):
    assert visual.number(text) == value


@pytest.mark.parametrize("text", ["около 40", "пять", "", "4-5", "12 месяцев"])
def test_non_numbers_are_refused(text):
    assert visual.number(text) is None


# --- форма таблицы и диаграммы -----------------------------------------------


def test_table_limits_are_the_customers():
    """Приложение 1 ТЗ: «таблица больше 7 строк или 5 колонок»."""
    ok, why = visual.table_from({"header": ["Регион", "Заказы"], "rows": [["Центр", "22 400"]]})
    assert why is None and ok.rows == (("Центр", "22 400"),)
    assert visual.table_from({"header": list("abcdef"), "rows": [list("abcdef")]})[1]
    assert visual.table_from({"header": ["a", "b"], "rows": [["1", "2"]] * 7})[1]
    assert visual.table_from({"header": ["один"], "rows": [["x"]]})[1], "одна колонка — это список"


def test_short_row_is_padded_long_row_is_refused():
    ok, _ = visual.table_from({"header": ["a", "b", "c"], "rows": [["1", "2"]]})
    assert ok.rows == (("1", "2", ""),)
    assert visual.table_from({"header": ["a", "b"], "rows": [["1", "2", "3"]]})[1]


def test_chart_needs_a_number_for_every_label():
    raw = {"type": "column", "unit": "заказов", "categories": ["январь", "февраль"],
           "series": [{"name": "Заказы", "values": ["41 200", "38 900"]}]}
    ok, why = visual.chart_from(raw)
    assert why is None and ok.series[0].values == (41200.0, 38900.0)
    assert ok.series[0].labels == ("41 200", "38 900"), "подпись — как в тексте"
    bad = dict(raw, series=[{"name": "Заказы", "values": ["41 200"]}])
    assert "значений 1 при 2" in visual.chart_from(bad)[1]
    words = dict(raw, series=[{"name": "Заказы", "values": ["много", "мало"]}])
    assert "не числа" in visual.chart_from(words)[1]
    pie = dict(raw, type="pie", series=raw["series"] * 2)
    assert "ряд один" in visual.chart_from(pie)[1]


def test_markdown_table_becomes_native_and_broken_one_a_list():
    doc = parse_markdown("# Итоги\n\n| Регион | Заказы |\n|---|---|\n| Центр | 22 400 |\n| Урал | 11 800 |\n")
    block = doc.sections[0].blocks[0]
    assert block.kind == "table" and block.data.header == ("Регион", "Заказы")
    assert block.data.rows == (("Центр", "22 400"), ("Урал", "11 800"))
    broken = parse_markdown("# Итоги\n\n| только одна колонка |\n|---|\n| строка |\n")
    kinds = [b.kind for b in broken.sections[0].blocks]
    assert kinds == ["list"], "не складывается в таблицу — список, а не потеря"


# --- проверки ответа модели ----------------------------------------------------

TEXT = ("Выдача по месяцам: январь — 41 200, февраль — 38 900, март — 52 400. "
        "По регионам: Центр 22 400, Урал 11 800. Работает склад в Твери.")


def _deck(*slides):
    base = {"role": "прочее", "image_idea": "", "images": []}
    return {"slides": [dict(base, **s) for s in slides], "missing_roles": []}


def _chart(values=("41 200", "38 900", "52 400")):
    return {"type": "line", "unit": "заказов", "categories": ["январь", "февраль", "март"],
            "series": [{"name": "Выдача", "values": list(values)}]}


def _table():
    return {"header": ["Регион", "Заказы"], "rows": [["Центр", "22 400"], ["Урал", "11 800"]]}


def _failed(answer, mode="improve"):
    return {c.name for c in outline.check_outline(TEXT, answer, mode, 15) if not c.ok}


def _clean():
    return _deck(
        {"heading": "Выдача заказов растёт", "kind": "chart", "theses": [], "chart": _chart()},
        {"heading": "Центр впереди Урала", "kind": "table", "theses": [], "table": _table()},
        {"heading": "Склад работает", "kind": "text", "theses": ["Работает склад в Твери"]},
    )


def test_numbers_in_table_and_chart_are_on_the_slide():
    """Число, стоящее только в таблице или диаграмме, не потеряно."""
    assert _failed(_clean()) == set()


def test_invented_number_in_a_chart_is_invention():
    answer = _clean()
    answer["slides"][0]["chart"] = _chart(("41 200", "38 900", "60 000"))
    assert "числа" in _failed(answer)


def test_broken_chart_is_dropped_and_retry_learns_why():
    """Негодная диаграмма снимается; её числа, которых больше нигде нет, —
    потеряны, и повтор узнаёт причину из заметки (`PLAN-10.0`, проверка 1, п. 1)."""
    answer = _clean()
    answer["slides"][0]["chart"] = _chart(("41 200", "38 900"))       # значений меньше подписей
    checks = outline.check_outline(TEXT, answer, "improve", 15)
    assert {c.name for c in checks if not c.ok} == {"числа"}
    note = next(c for c in checks if c.name == outline.VISUALS)
    assert note.ok and "значений 2 при 3" in note.detail
    retry = outline.retry_history("{}", checks)[1]["content"]
    assert outline.VISUALS in retry and "числа" in retry


def test_visual_of_a_foreign_kind_is_not_taken():
    """Таблица у слайда text не берётся: модель у брифа заполняла её у всех
    слайдов выдуманными рядами (замер 26 сентября)."""
    answer = _clean()
    answer["slides"][2]["table"] = {"header": ["Площадка", "Статус"], "rows": [["1", "работает"]]}
    checks = outline.check_outline(TEXT, answer, "improve", 15)
    assert outline.accepted(checks)
    note = next(c for c in checks if c.name == outline.VISUALS)
    assert "таблица у слайда типа text" in note.detail


def test_neighbour_cells_are_not_one_number():
    """«100», «100», «100» — три числа, а не «100 100 100» с разрядами."""
    text = "Три площадки по 100 заказов: 100, 100 и 100."
    answer = _deck({"heading": "Площадки дают по 100 заказов", "kind": "chart", "theses": [],
                    "chart": {"type": "column", "unit": "", "categories": ["a", "b", "c"],
                              "series": [{"name": "Заказы", "values": ["100", "100", "100"]}]}})
    failed = {c.name for c in outline.check_outline(text, answer, "improve", 15) if not c.ok}
    assert "числа" not in failed


def test_new_names_are_caught_and_sentence_starts_are_not():
    """«в Москве» — название, которого в тексте нет; «Твери» — есть в другом
    падеже; прописная в начале фразы и после двоеточия — не название."""
    assert outline.new_names(["Склад открыт в Москве"], TEXT) == ["Москве"]
    assert outline.new_names(["Склад в Тверь переехал"], TEXT) == []
    assert outline.new_names(["Итоги: Выдача растёт. Центр впереди"], TEXT) == []
    answer = _clean()
    answer["slides"][2]["theses"].append("Склад открыт и в Казани")
    assert "названия" in _failed(answer)


def test_answer_becomes_a_visual_block_first_in_its_section(tmp_path):
    path = tmp_path / "t.md"
    path.write_text(TEXT, encoding="utf-8")
    doc, _notes, deck = outline.to_doc(_clean(), TEXT, str(path), "t")
    chart_block = doc.sections[0].blocks[0]
    assert chart_block.kind == "chart" and chart_block.data.categories == ("январь", "февраль", "март")
    assert doc.sections[1].blocks[0].kind == "table"
    assert deck[0]["chart"]["series"][0]["labels"] == ["41 200", "38 900", "52 400"]


# --- место под таблицу -------------------------------------------------------


def _section(block, heading="Выдача заказов растёт"):
    return ContentSection(id="m01", heading=heading, blocks=(block,), kind=block.kind)


def _chart_block(min_side=None):
    data, _ = visual.chart_from(_chart())
    return ContentBlock(id="m01v", kind="chart", text=" ".join(data.strings()), data=data,
                        min_side=min_side)


def test_host_is_the_largest_free_place(multi_template_path):
    analysis = analyze_template(multi_template_path)
    hosts = [(p.id, visual_host(p, _chart_block())) for p in analysis.patterns.patterns]
    found = [(pid, h) for pid, h in hosts if h is not None]
    assert found, "в шаблоне-фикстуре есть крупное тело слайда"
    for pid, host in found:
        pattern = next(p for p in analysis.patterns.patterns if p.id == pid)
        same_class = [s for s in pattern.slots if s.content_type in ("text", "list")
                      and s.role in ("body", "bullet_list") and not s.occluded]
        if host.content_type in ("text", "list") and same_class:
            assert host.rect.cx * host.rect.cy == max(s.rect.cx * s.rect.cy for s in same_class)


def _slot(sid, role, ctype, x, y, cx, cy, picture_kind=None):
    from mimeo.analyze.deck import Rect
    from mimeo.model import Slot

    return Slot(id=sid, role=role, content_type=ctype, rect=Rect(x, y, cx, cy), type_role=None,
                capacity=None, required=False, shape_id=sid, picture_kind=picture_kind)


def _pattern(*slots):
    from mimeo.model import Pattern

    return Pattern(id="p01", kind="text", donor_part="/ppt/slides/slide1.xml", donor_index=0,
                   slots=tuple(slots), members=(0,), cohesion=None, donor_reason="тест", source="slides")


def test_host_order_donor_data_then_picture_then_the_larger_body():
    """Старшинство места: таблица донора, иллюстрация, крупное тело; внутри
    класса — самое крупное, заголовок не накрывается."""
    title = _slot("s01", "title", "text", 0, 0, 9_000_000, 900_000)
    small = _slot("s02", "body", "text", 0, 1_000_000, 3_000_000, 3_000_000)
    large = _slot("s03", "body", "text", 3_500_000, 1_000_000, 5_000_000, 4_000_000)
    picture = _slot("s04", "image", "image", 0, 1_000_000, 4_000_000, 3_500_000, "illustration")
    donor = _slot("s05", "table", "table", 0, 5_200_000, 3_000_000, 1_500_000)
    block = _chart_block()
    assert visual_host(_pattern(title, small, large), block).id == "s03"
    assert visual_host(_pattern(title, small, large, picture), block).id == "s04"
    assert visual_host(_pattern(title, small, large, picture, donor), block).id == "s05"
    cover = _slot("s06", "body", "text", 0, 0, 9_000_000, 6_000_000)
    assert visual_host(_pattern(title, cover), block, used={"s01"}) is None, \
        "место, накрывающее занятый заголовок, — не место"


def test_text_place_under_the_chart_gets_no_text():
    """Карточка, которую диаграмма накроет на четверть, тезиса не получает —
    он уходит в свободное место (растр WorkSpace, 26 сентября: карточка на 48 %
    над местом диаграммы прошла прежний порог в половину)."""
    from mimeo.plan.matching import match

    title = _slot("s01", "title", "text", 0, 0, 9_000_000, 900_000)
    host = _slot("s02", "image", "image", 4_000_000, 1_500_000, 5_000_000, 4_000_000, "illustration")
    # Накрыта на 37.5 % своей площади: между новым порогом 0.15 и прежним 0.5.
    card = _slot("s03", "body", "text", 7_500_000, 500_000, 2_000_000, 2_000_000)
    free = _slot("s04", "body", "text", 0, 1_500_000, 3_500_000, 3_000_000)
    thesis = ContentBlock(id="t", kind="paragraph", text="Три колоды собираются быстрее лимита")
    section = ContentSection(id="m01", heading="Сборка быстрее лимита",
                             blocks=(_chart_block(), thesis), kind="chart")
    m = match(section, _pattern(title, host, card, free))
    placed = {f.slot_id: f for f in m.fills}
    assert placed["s02"].kind == "chart"
    assert "s03" not in placed and placed["s04"].text == thesis.text


def test_no_host_bigger_than_the_slide(multi_template_path):
    analysis = analyze_template(multi_template_path)
    huge = _chart_block(min_side=10 ** 9)
    assert all(visual_host(p, huge) is None for p in analysis.patterns.patterns)
    doc = ContentDoc(name="t", sections=[_section(huge)])
    doc, notes = visual.without_hosts(doc, lambda b: False)
    block = doc.sections[0].blocks[0]
    assert block.kind == "list" and "январь — 41 200 заказов" in block.items
    assert notes and "списком" in notes[0], "содержание не теряется, и об этом сказано"


# --- сборка ------------------------------------------------------------------


@pytest.fixture(scope="module")
def visual_deck(multi_template_path, tmp_path_factory):
    analysis = analyze_template(multi_template_path)
    table, _ = visual.table_from(_table())
    chart, _ = visual.chart_from(_chart())
    doc = ContentDoc(name="t", title=None, sections=[
        _section(ContentBlock(id="a", kind="table", text="т", data=table), "Центр впереди Урала"),
        _section(ContentBlock(id="b", kind="chart", text="д", data=chart)),
    ])
    plan = plan_deck(doc, analysis.patterns, analysis.design_system.source.sha256)
    out = tmp_path_factory.mktemp("visual")
    path = str(out / "deck.pptx")
    report = build(multi_template_path, plan, analysis.patterns, path)
    return plan, report, path, multi_template_path, analysis


def test_plan_puts_the_data_into_the_fill(visual_deck):
    plan = visual_deck[0]
    kinds = [f.kind for s in plan.slides for f in s.fills if f.data is not None]
    assert sorted(kinds) == ["chart", "table"]
    fill = next(f for s in plan.slides for f in s.fills if f.kind == "chart")
    assert fill.to_json()["data"]["series"][0]["labels"] == ["41 200", "38 900", "52 400"]


def test_independent_reader_sees_native_table_and_chart(visual_deck):
    pptx = pytest.importorskip("pptx")
    _plan, report, path, *_ = visual_deck
    assert not [w for w in report.warnings if "таблица" in w or "диаграмм" in w], report.warnings
    prs = pptx.Presentation(path)
    tables = [sh for sl in prs.slides for sh in sl.shapes if sh.has_table]
    charts = [sh for sl in prs.slides for sh in sl.shapes if sh.has_chart]
    assert len(tables) == 1 and len(charts) == 1
    cells = [[c.text for c in row.cells] for row in tables[0].table.rows]
    assert cells == [["Регион", "Заказы"], ["Центр", "22 400"], ["Урал", "11 800"]]
    plot = charts[0].chart.plots[0]
    assert list(plot.categories) == ["январь", "февраль", "март"]
    assert list(plot.series[0].values) == [41200.0, 38900.0, 52400.0]


def test_chart_data_opens_as_a_workbook(visual_deck):
    """«Изменить данные» в PowerPoint открывает книгу из пакета: она обязана
    быть целой и нести те же числа."""
    path = visual_deck[2]
    with zipfile.ZipFile(path) as z:
        books = [n for n in z.namelist() if n.startswith("ppt/embeddings/mimeo-chart")]
        assert len(books) == 1
        with zipfile.ZipFile(io.BytesIO(z.read(books[0]))) as book:
            sheet = book.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "<v>41200</v>" in sheet and "январь" in sheet


def test_package_with_visuals_is_valid_and_reproducible(visual_deck, tmp_path):
    plan, _report, path, template, analysis = visual_deck
    assert inspect(path) == []
    again = str(tmp_path / "again.pptx")
    build(template, plan, analysis.patterns, again)
    with open(path, "rb") as a, open(again, "rb") as b:
        assert a.read() == b.read(), "сборка с таблицей и диаграммой — байт в байт"


def test_frame_steps_aside_from_our_text():
    """Тезис слева заходит под место таблицы — рамка отступает вправо; текст
    сверху — рамка опускается; текст в стороне — рамка на месте (растр
    VK Tech, 26 сентября: «показал 0» под шапкой таблицы)."""
    host = (4_000_000, 1_000_000, 6_000_000, 4_000_000)
    left_text = (500_000, 1_200_000, 3_700_000, 800_000)
    x, y, cx, cy = compose_visual.clear_of(host, [left_text])
    assert x >= 4_200_000 + compose_visual._GAP and x + cx == 10_000_000 and cy == 4_000_000
    title = (500_000, 400_000, 9_000_000, 800_000)
    x, y, cx, cy = compose_visual.clear_of(host, [title])
    assert y == 1_200_000 + compose_visual._GAP and x == 4_000_000
    assert compose_visual.clear_of(host, [(0, 6_000_000, 100, 100)]) == host


def test_only_builtin_table_styles_are_taken(tmp_path):
    """Свой стиль таблиц из файла шаблона PowerPoint мог не нарисовать вовсе
    (WorkSpace из Google Slides, растр 26 сентября) — берётся только встроенный."""
    from mimeo.compose.package import PackageWriter

    class _Writer:
        def __init__(self, xml):
            self._xml = xml

        def has(self, name):
            return True

        def xml(self, name):
            from xml.etree import ElementTree as ET
            return ET.fromstring(self._xml)

    ns = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    own = f'<a:tblStyleLst {ns} def="{{AAA}}"><a:tblStyle styleId="{{AAA}}" styleName="x"/></a:tblStyleLst>'
    builtin = f'<a:tblStyleLst {ns} def="{{073A0DAA-6AF3-43AB-8588-CEC1D06C72B9}}"/>'
    assert compose_visual.table_style(_Writer(own)) == compose_visual.DEFAULT_TABLE_STYLE
    assert compose_visual.table_style(_Writer(builtin)) == "{073A0DAA-6AF3-43AB-8588-CEC1D06C72B9}"
    assert PackageWriter  # тот же интерфейс: has и xml


def test_table_font_shrinks_to_the_place():
    table, _ = visual.table_from({"header": ["Регион", "Комментарий"],
                                  "rows": [["Центр", "очень длинный комментарий " * 4]] * 6})
    roomy = compose_visual.fit_table(table, 9_000_000, 6_000_000, 1800, 6858000)
    tight = compose_visual.fit_table(table, 9_000_000, 2_000_000, 1800, 6858000)
    assert tight[0] < roomy[0], "в тесном месте кегль меньше"
    assert sum(roomy[1]) == 9_000_000, "колонки делят ширину места без остатка"


@pytest.mark.parametrize("labels, fmt", [
    (("41 200", "38 900"), "#,##0"),
    (("2.8", "3.9"), "0.0"),
    (("4%", "14%"), '0"%"'),
    (("3,25", "4"), "0.00"),
])
def test_number_format_follows_the_text(labels, fmt):
    series = (visual.Series("s", tuple(visual.number(x) for x in labels), labels),)
    assert compose_visual.number_format(series) == fmt
