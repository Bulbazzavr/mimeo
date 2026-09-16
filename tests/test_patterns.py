"""Библиотека паттернов. ADR-0004, ADR-0006, ADR-0007.

Проверяется на фикстуре с известной разметкой: 0 обложка, 1-2 списки,
3-4 карточки, 5 метрика, 6 раздел, 7 две колонки, 8 финал.
"""

from __future__ import annotations

import json
import os

import pytest

from mimeo.analyze import analyze_template

CONTRACT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "contracts",
    "pattern-library.schema.json",
)


@pytest.fixture(scope="module")
def library(multi_template_path):
    return analyze_template(multi_template_path).patterns


@pytest.fixture(scope="module")
def by_kind(library):
    return {p.kind: p for p in library.patterns}


# --- кластеризация -----------------------------------------------------


def test_repeated_layouts_are_merged(library):
    members = {p.kind: p.members for p in library.patterns}
    assert members["bullets"] == (1, 2)
    assert members["cards"] == (3, 4)


def test_distinct_layouts_are_not_merged(library):
    """Две колонки и три карточки — разные раскладки, а не «похожие»."""
    assert len(library.patterns) == 7
    kinds = [p.kind for p in library.patterns]
    assert kinds.count("cards") == 1
    assert kinds.count("two_column") == 1


def test_cover_and_closing_are_kept_apart_from_section(by_kind):
    """Обложка и финал особенные по месту в колоде, а не по геометрии."""
    assert by_kind["cover"].members == (0,)
    assert by_kind["closing"].members == (8,)
    assert by_kind["section"].members == (6,)


def test_merged_clusters_report_cohesion(by_kind):
    assert by_kind["bullets"].cohesion == 1.0
    assert by_kind["cover"].cohesion is None


def test_donor_is_named_and_explained(library):
    for pattern in library.patterns:
        assert pattern.donor_part.startswith("/ppt/slides/")
        assert pattern.donor_reason
        assert pattern.source == "slides"


# --- слоты -------------------------------------------------------------


def test_decor_is_not_listed_as_a_slot(by_kind):
    """Декор приезжает вместе с клонированным донором. ADR-0004."""
    assert all(s.content_type != "none" for s in by_kind["cards"].slots)


def test_cards_pattern_exposes_one_slot_per_card(by_kind):
    cards = by_kind["cards"]
    assert len(cards.slots) == 4          # заголовок и три карточки
    assert sum(1 for s in cards.slots if s.role == "body") == 3


def test_metric_slot_is_recognised(by_kind):
    roles = {s.role for s in by_kind["metric"].slots}
    assert "metric_value" in roles


def test_slots_carry_capacity_and_type_reference(by_kind):
    for slot in by_kind["bullets"].slots:
        assert slot.capacity is not None
        assert slot.capacity.max_chars >= 1
        if slot.type_role is not None:
            assert slot.type_role.startswith("t")


def test_slot_capacity_never_contradicts_the_donor(by_kind):
    for pattern in by_kind.values():
        for slot in pattern.slots:
            if slot.capacity and slot.capacity.donor_chars:
                assert slot.capacity.max_chars >= slot.capacity.donor_chars


# --- деградация и контракт ---------------------------------------------


def test_two_slide_template_falls_back_to_layouts(template_path):
    """Слайдов меньше трёх — паттерны выводятся из макетов. ADR-0006."""
    library = analyze_template(template_path).patterns
    assert library.patterns
    assert all(p.source == "layouts" for p in library.patterns)
    assert any("ADR-0006" in note for note in library.notes)


def test_artifact_matches_contract(library):
    jsonschema = pytest.importorskip("jsonschema")
    with open(CONTRACT, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.Draft202012Validator(schema).validate(library.to_json())


def test_pattern_library_is_deterministic(multi_template_path):
    first = json.dumps(analyze_template(multi_template_path).patterns.to_json())
    second = json.dumps(analyze_template(multi_template_path).patterns.to_json())
    assert first == second
