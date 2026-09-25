"""Объём колоды: 10–15 слайдов или сколько задали. `Z-35`, план `PLAN-2.3`.

Тесты стерегут три вещи, каждая из которых однажды ломалась при разработке:
без цели поведение прежнее, подгонка не рождает пустых и одинаковых слайдов,
а недостижимая цель называется словами, а не молча округляется.
"""

from __future__ import annotations

import pytest

from mimeo.cli import parse_slides
from mimeo.plan.content import load as load_markdown
from mimeo.plan.deterministic import plan_deck
from mimeo.plan.prose import (
    _Topic,
    fit_topic_count,
    load_config,
    restructure,
)

CFG = load_config()
PROSE = "examples/content-prose.md"
DEMO = "examples/content-demo.md"


def _doc(path, target=None):
    return restructure(load_markdown(path), CFG, target)


def _atoms(doc):
    return sum(b.units for s in doc.sections for b in s.blocks)


def _signature(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


# --- разбор аргумента --------------------------------------------------


def test_slides_argument_accepts_number_and_range():
    assert parse_slides("12") == (12, 12)
    assert parse_slides("10-15") == (10, 15)
    assert parse_slides("10–15") == (10, 15)      # длинное тире из копипасты
    assert parse_slides(None) is None
    assert parse_slides("") is None


@pytest.mark.parametrize("bad", ["abc", "0", "15-10", "1-2-3"])
def test_slides_argument_rejects_nonsense(bad):
    with pytest.raises(SystemExit):
        parse_slides(bad)


# --- подгонка числа тем ------------------------------------------------


def _topics(sizes, strengths=None):
    strengths = strengths or [1.0] * len(sizes)
    return [
        _Topic(label=None, theses=[f"тезис {i}-{k}" for k in range(n)], strength=s)
        for i, (n, s) in enumerate(zip(sizes, strengths))
    ]


def test_merging_starts_from_the_weakest_seam():
    """Метка автора — самый крепкий шов: он рвётся последним."""
    topics = _topics([2, 2, 2], strengths=[1.0, 3.0, 0.5])
    out, shortfall = fit_topic_count(topics, (2, 2), CFG)
    assert shortfall is None
    assert len(out) == 2
    # слабый шов был перед третьей темой — она и слилась со второй
    assert [len(t.theses) for t in out] == [2, 4]


def test_splitting_takes_the_largest_topic():
    topics = _topics([1, 6, 1])
    out, _ = fit_topic_count(topics, (4, 4), CFG)
    assert len(out) == 4
    assert max(len(t.theses) for t in out) < 6


def test_unreachable_target_is_named_not_silently_rounded():
    """Из трёх тем по одному тезису десяти не сделать, и это надо сказать."""
    out, shortfall = fit_topic_count(_topics([1, 1, 1]), (10, 10), CFG)
    assert len(out) == 3
    assert shortfall and "хватает на 3" in shortfall


def test_fit_does_not_lose_theses():
    for target in ((2, 2), (4, 4), (12, 12)):
        out, _ = fit_topic_count(_topics([3, 4, 1]), target, CFG)
        assert sum(len(t.theses) for t in out) == 8, target


# --- по всему пути -----------------------------------------------------


def test_target_changes_the_number_of_sections():
    assert len(_doc(PROSE, (12, 12)).sections) == 12
    assert len(_doc(PROSE, (5, 5)).sections) == 5


def test_text_survives_any_target():
    """Куски результата встречаются в исходнике дословно и по порядку, какой бы
    ни была цель. Подгонка объёма перекладывает текст, а не переписывает."""
    source = _signature(open(PROSE, encoding="utf-8").read())
    for target in (None, (5, 5), (10, 15), (12, 12), (20, 20)):
        doc = _doc(PROSE, target)
        at = 0
        for section in doc.sections:
            for block in section.blocks:
                for piece in (block.items if block.kind == "list" else [block.text]):
                    found = source.find(_signature(piece), at)
                    assert found >= 0, f"цель {target}: в исходнике нет куска «{piece}»"
                    at = found + len(_signature(piece))


def test_markdown_input_is_not_segmented_even_with_a_target():
    """Цель не включает сегментацию там, где структура уже есть."""
    doc = load_markdown(DEMO)
    assert restructure(doc, CFG, (10, 15)) is doc


def test_same_target_gives_the_same_structure():
    first, second = _doc(PROSE, (11, 11)), _doc(PROSE, (11, 11))
    assert [(s.id, s.heading, s.blocks) for s in first.sections] == [
        (s.id, s.heading, s.blocks) for s in second.sections
    ]


# --- добор на стадии планирования --------------------------------------


@pytest.fixture(scope="module")
def library(multi_template_path):
    from mimeo.analyze import analyze_template

    return analyze_template(multi_template_path)


def test_no_target_means_the_previous_behaviour(library):
    doc = _doc(PROSE)
    sha = library.design_system.source.sha256
    assert len(plan_deck(doc, library.patterns, sha).slides) == len(
        plan_deck(doc, library.patterns, sha, target=None).slides
    )


def test_plan_reports_what_happened_with_the_volume(library):
    sha = library.design_system.source.sha256
    plan = plan_deck(_doc(PROSE, (10, 15)), library.patterns, sha, target=(10, 15))
    assert any("Целевой объём" in w for w in plan.warnings)


def test_elective_split_floor_is_two_units_per_part():
    """Пол по содержательности как правило, а не как побочный эффект.

    Проверяется прямо на нём: через полный путь эта ветка достижима не на всяком
    шаблоне, и проверка получилась бы непроваливаемой.
    """
    from mimeo.plan.content import ContentBlock, ContentSection
    from mimeo.plan.deterministic import _elective_limit

    def section(units: int) -> ContentSection:
        return ContentSection(
            id="s",
            heading="Тема",
            blocks=(ContentBlock(id="b01", kind="list",
                                 items=tuple(f"пункт {i}" for i in range(units)),
                                 text=""),),
        )

    assert _elective_limit(section(1)) == 0      # одну единицу не делят вовсе
    assert _elective_limit(section(2)) == 1      # две — на одну часть, то есть никак
    assert _elective_limit(section(4)) == 2      # четыре — надвое, а не начетверо
    assert _elective_limit(section(7)) == 3


def _roomy_pattern(pattern_id: str, items: int = 9):
    """Раскладка, в которую влезает что угодно: заголовок и просторный список.

    Нужна, чтобы проверить потолок. На фикстурных шаблонах добор не срабатывает
    вовсе — раздел и так расходится на части по тесноте, — и проверка потолка
    через них была бы непроваливаемой. `items` — сколько пунктов берёт список:
    тесная раскладка нужна тестам причины деления (Ш5).
    """
    from mimeo.analyze.deck import Rect
    from mimeo.model import Capacity, Pattern, Slot

    def slot(sid, role, content_type, items):
        return Slot(
            id=sid, role=role, content_type=content_type,
            rect=Rect(0, 0, 9144000, 1371600), type_role=role,
            capacity=Capacity(max_chars=400, max_lines=8, chars_per_line=50,
                              target_chars=300, max_items=items, donor_chars=100,
                              basis="тест"),
            required=True, shape_id="1",
        )

    return Pattern(
        id=pattern_id, kind="bullets", donor_part=f"/ppt/slides/{pattern_id}.xml",
        donor_index=0,
        slots=(slot("s01", "title", "text", None), slot("s02", "bullet_list", "list", items)),
        members=(0,), cohesion=None, donor_reason="тест", source="slides",
    )


def test_top_up_stops_at_the_number_of_layouts():
    """Слайдов не больше, чем раскладок: «два слайда дублируют друг друга» —
    дефект из Приложения 1 ТЗ, и добор не должен его плодить."""
    from mimeo.plan.deterministic import _count_slides, _forced_parts

    from mimeo.plan.content import ContentBlock, ContentSection

    sections = [
        ContentSection(
            id=f"sec{i:02d}",
            heading=f"Тема {i}",
            blocks=(ContentBlock(id=f"b{i:02d}", kind="list",
                                 items=tuple(f"тезис {i}-{k}" for k in range(6)),
                                 text=" "),),
        )
        for i in range(3)
    ]
    roomy = tuple(_roomy_pattern(f"p{i:02d}") for i in range(30))
    natural = _count_slides(sections, roomy, {})
    unbounded = _count_slides(sections, roomy, _forced_parts(sections, roomy, (40, 40), ceiling=99))
    assert unbounded > natural, "без потолка добор обязан что-то прибавить"

    limited = _count_slides(
        sections, roomy, _forced_parts(sections, roomy, (40, 40), ceiling=natural + 2)
    )
    assert limited <= natural + 2 < unbounded, (
        f"потолок не сработал: естественно {natural}, с потолком {limited}, "
        f"без потолка {unbounded}"
    )


def test_elective_split_never_makes_a_one_atom_slide(library):
    """Пол по содержательности: дробление ради объёма оставляет минимум две
    единицы на часть. Без него демо-контент давал четыре слайда подряд с
    одинаковым заголовком (`WORKLOG/2026-09-15-deck-volume.md`)."""
    sha = library.design_system.source.sha256
    doc = load_markdown(DEMO)
    plan = plan_deck(doc, library.patterns, sha, target=(15, 15))
    headings = [
        next((f.text for f in s.fills if f.kind == "text" and f.text), "")
        for s in plan.slides
    ]
    repeated = len(headings) - len(set(headings))
    assert repeated <= 1, f"одинаковых заголовков {repeated}: {headings}"


# --- колода модели и причина деления (`PLAN-9.0`, Ш5) -----------------------


def _section(sid: str, units: int):
    from mimeo.plan.content import ContentBlock, ContentSection

    return ContentSection(
        id=sid, heading=f"Тема {sid}",
        blocks=(ContentBlock(id=f"b{sid}", kind="list",
                             items=tuple(f"тезис {sid}-{k}" for k in range(units)), text=" "),),
    )


def _plan(sections, patterns, target=None, planner="deterministic"):
    """План по разделам без шаблона: `plan_deck` берёт от библиотеки только
    раскладки и хэш источника."""
    from types import SimpleNamespace

    from mimeo.plan.content import ContentDoc

    doc = ContentDoc(name="t", sections=list(sections), planner=planner)
    library = SimpleNamespace(patterns=tuple(patterns), source=SimpleNamespace(sha256="t"))
    return plan_deck(doc, library, "t", target=target)


def _split_notes(plan) -> list[str]:
    return [w for w in plan.warnings if w.startswith("Раздел «")]


ROOMY = tuple(_roomy_pattern(f"p{i:02d}") for i in range(30))


def test_model_deck_is_not_topped_up():
    """Колоду модели код ради объёма не добирает: число слайдов решила она, а
    добор резал её таблицу — шесть месяцев на три слайда по два, и растр на
    всех трёх выданных шаблонах хуже колоды без добора (решение пользователя
    25 сентября; `WORKLOG/2026-09-25-z57-sh5-baseline.md`). Тот же документ без
    модели добор делит — держит именно признак колоды модели."""
    sections = [_section("a", 6), _section("b", 2)]
    by_code = _plan(sections, ROOMY, (4, 6))
    by_model = _plan(sections, ROOMY, (4, 6), planner="mixed")

    assert len(by_code.slides) == 4 and any(s.origin_of for s in by_code.slides)
    assert len(by_model.slides) == 2 and not any(s.origin_of for s in by_model.slides)
    assert not _split_notes(by_model)
    volume = [w for w in by_model.warnings if w.startswith("Целевой объём")]
    assert volume and "не достигнут: вышло 2" in volume[0]
    assert "построила модель" in volume[0], volume


def test_model_deck_still_splits_what_does_not_fit():
    """Вынужденное деление у колоды модели остаётся: без него раздел не встал
    бы никуда (`rank` отдаёт только пригодные раскладки)."""
    plan = _plan([_section("a", 12)], ROOMY, (1, 6), planner="mixed")
    assert [s.origin_of for s in plan.slides] == [2, 2]
    assert _split_notes(plan) == [
        "Раздел «Тема a» разбит на 2 слайда: целиком он не помещался ни в одну раскладку."
    ]


def _exclusive_and_tight():
    """Просторная раскладка с исключительным донором (`Z-44`: в колоде один раз)
    и тесная — на два пункта. Добор велит две части, вторая уже не встаёт в
    занятую просторную, а в тесную — только по два пункта."""
    from dataclasses import replace

    return (replace(_roomy_pattern("p00"), exclusive=True), _roomy_pattern("p01", items=2))


@pytest.mark.parametrize("sections, patterns, target, note", [
    ([_section("a", 12)], ROOMY, None,
     "Раздел «Тема a» разбит на 2 слайда: целиком он не помещался ни в одну раскладку."),
    ([_section("a", 6), _section("b", 2)], ROOMY, (4, 6),
     "Раздел «Тема a» разбит на 3 слайда ради объёма: без деления слайдов выходило меньше 4."),
    ([_section("a", 6)], _exclusive_and_tight(), (3, 5),
     "Раздел «Тема a» разбит на 3 слайда: добор объёма просил 2 части, но для частей "
     "такого размера раскладки не нашлось."),
], ids=["fit", "top-up", "top-up-and-fit"])
def test_split_note_names_its_own_reason(sections, patterns, target, note):
    """Предупреждение о делении называет свою причину (`PLAN-9.0`, Ш5; приёмка 8).
    До Ш5 «целиком он не помещался» стояло при любом делении, и при доборе это
    была неправда: без цели тот же раздел вставал на один слайд."""
    assert _split_notes(_plan(sections, patterns, target)) == [note]


def test_plural_of_slides_and_parts():
    from mimeo.plan.deterministic import _plural

    forms = ("слайд", "слайда", "слайдов")
    assert [_plural(n, *forms) for n in (1, 2, 4, 5, 6, 11, 12, 21, 22, 25)] == [
        "слайд", "слайда", "слайда", "слайдов", "слайдов", "слайдов", "слайдов",
        "слайд", "слайда", "слайдов",
    ]
