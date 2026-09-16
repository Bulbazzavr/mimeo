"""Подбор раскладок и планировщик без модели. ADR-0009.

Проверяется на фикстуре с известной разметкой: шаблон из девяти слайдов даёт
паттерны cover / bullets / cards / metric / section / two_column / closing.
"""

from __future__ import annotations

import json
import os

import pytest

from mimeo.analyze import analyze_template
from mimeo.plan import parse_markdown, plan_deck
from mimeo.plan.deterministic import split
from mimeo.plan.matching import preferred_kinds, rank

CONTRACT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "contracts",
    "deck-plan.schema.json",
)

CONTENT = """# Сервис прогнозирования

Вводный абзац о том, зачем вообще нужен этот сервис и какую боль он снимает.

## Что умеет система

- Прогнозирует деградацию датчиков
- Оценивает риск задымления
- Выявляет аномальный доступ

## Результаты

0.82

Точность прогноза

## Спасибо
"""


@pytest.fixture(scope="module")
def analysis(multi_template_path):
    return analyze_template(multi_template_path)


@pytest.fixture(scope="module")
def doc():
    return parse_markdown(CONTENT, name="test-content")


@pytest.fixture(scope="module")
def plan(doc, analysis):
    return plan_deck(doc, analysis.patterns, analysis.design_system.source.sha256)


# --- ожидаемый вид раскладки -------------------------------------------


def test_short_list_asks_for_cards():
    doc = parse_markdown("## Три пункта\n\n- Раз\n- Два\n- Три\n")
    assert preferred_kinds(doc.sections[0])[0] == "cards"


def test_number_asks_for_metric():
    doc = parse_markdown("## Итог\n\n84%\n\nДоля успеха\n")
    assert preferred_kinds(doc.sections[0])[0] == "metric"


def test_bare_heading_asks_for_section():
    doc = parse_markdown("## Часть вторая\n")
    assert preferred_kinds(doc.sections[0])[0] == "section"


# --- подбор ------------------------------------------------------------


def test_ranking_is_deterministic_and_ordered(doc, analysis):
    ranked = rank(doc.sections[1], analysis.patterns.patterns)
    assert ranked
    assert [m.score for m in ranked] == sorted((m.score for m in ranked), reverse=True)
    again = rank(doc.sections[1], analysis.patterns.patterns)
    assert [m.pattern_id for m in again] == [m.pattern_id for m in ranked]


def test_every_fill_names_an_existing_slot(doc, analysis):
    by_id = {p.id: p for p in analysis.patterns.patterns}
    for section in doc.sections:
        for m in rank(section, analysis.patterns.patterns):
            slots = {s.id for s in by_id[m.pattern_id].slots}
            assert {f.slot_id for f in m.fills} <= slots


# --- дробление ---------------------------------------------------------


def test_split_breaks_a_list_into_parts(doc):
    section = doc.sections[1]
    parts = split(section, 2)
    assert len(parts) == 2
    restored = [i for p in parts for b in p.blocks for i in b.items]
    assert restored == list(section.blocks[0].items)


def test_split_keeps_the_heading_on_every_part(doc):
    for part in split(doc.sections[1], 3):
        assert part.heading == doc.sections[1].heading


# --- планирование ------------------------------------------------------


def test_plan_places_everything(plan):
    assert plan.slides
    assert plan.unplaced == ()


def test_plan_runs_without_a_model(plan):
    """Главное свойство: план строится офлайн. ADR-0009."""
    assert plan.planner == "deterministic"


def test_first_slide_prefers_a_cover(plan, analysis):
    kinds = {p.id: p.kind for p in analysis.patterns.patterns}
    assert kinds[plan.slides[0].pattern_id] == "cover"


def test_last_slide_prefers_a_closing(plan, analysis):
    kinds = {p.id: p.kind for p in analysis.patterns.patterns}
    assert kinds[plan.slides[-1].pattern_id] == "closing"


def test_positional_layouts_are_not_used_in_the_middle(plan, analysis):
    kinds = {p.id: p.kind for p in analysis.patterns.patterns}
    middle = [kinds[s.pattern_id] for s in plan.slides[1:-1]]
    assert "cover" not in middle and "closing" not in middle


def test_every_slide_explains_itself(plan):
    """Причина выбора — не отладка, её показывают эксперту."""
    for slide in plan.slides:
        assert len(slide.reason) > 10


def test_indices_are_contiguous(plan):
    assert [s.index for s in plan.slides] == list(range(len(plan.slides)))


def test_plan_is_bound_to_the_artifacts(plan, analysis):
    """Без хешей COMPOSE не заметит, что шаблон переразобрали."""
    assert plan.source.design_system_sha256 == analysis.design_system.source.sha256
    assert plan.source.patterns_sha256 == analysis.patterns.source.sha256


def test_empty_library_degrades_without_crashing(doc, analysis):
    from mimeo.model import PatternLibrary

    empty = PatternLibrary(source=analysis.patterns.source, patterns=())
    result = plan_deck(doc, empty, "0" * 64)
    assert result.slides == ()
    assert result.unplaced
    assert result.warnings


# --- контракт ----------------------------------------------------------


def test_artifact_matches_contract(plan):
    jsonschema = pytest.importorskip("jsonschema")
    with open(CONTRACT, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.Draft202012Validator(schema).validate(plan.to_json())


def test_plan_is_deterministic(doc, analysis):
    a = json.dumps(plan_deck(doc, analysis.patterns, "0" * 64).to_json(), ensure_ascii=False)
    b = json.dumps(plan_deck(doc, analysis.patterns, "0" * 64).to_json(), ensure_ascii=False)
    assert a == b
