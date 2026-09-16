"""Промпт-контракт: что уходит в модель и что принимается назад. ADR-0010.

Живого вызова модели здесь нет и до 15 сентября быть не может. Проверяется то,
что от модели не зависит: форма запроса, разбор ответа любой аккуратности,
проверка на смысл и механический ремонт.
"""

from __future__ import annotations

import json

import pytest

from mimeo.analyze import analyze_template
from mimeo.plan import parse_markdown
from mimeo.plan.matching import rank
from mimeo.plan.prompt import MAX_CANDIDATES, Mode, build_request, catalogue
from mimeo.plan.validate import check, extract_json, feedback, repair

CONTENT = "## Что умеет система\n\n- Первое\n- Второе\n- Третье\n"


@pytest.fixture(scope="module")
def context(multi_template_path):
    analysis = analyze_template(multi_template_path)
    section = parse_markdown(CONTENT).sections[0]
    matches = rank(section, analysis.patterns.patterns)
    by_id = {p.id: p for p in analysis.patterns.patterns}
    return section, matches, by_id


# --- запрос ------------------------------------------------------------


def test_catalogue_hides_geometry(context):
    """Модель не видит координат и не должна. ADR-0002."""
    _, matches, by_id = context
    payload = json.dumps(catalogue(matches, by_id))
    assert "rect" not in payload and "emu" not in payload.lower()


def test_catalogue_carries_limits(context):
    _, matches, by_id = context
    for entry in catalogue(matches, by_id):
        for slot in entry["slots"]:
            assert "max_chars" in slot


def test_catalogue_is_capped(context):
    _, matches, by_id = context
    assert len(catalogue(matches, by_id)) <= MAX_CANDIDATES


def test_request_lists_only_offered_patterns(context):
    section, matches, by_id = context
    request = build_request(section, matches, by_id)
    assert request.candidates == tuple(m.pattern_id for m in matches[:MAX_CANDIDATES])
    assert request.section_id == section.id


def test_free_text_mode_spells_out_the_schema(context):
    section, matches, by_id = context
    plain = build_request(section, matches, by_id, mode=Mode.FREE_TEXT)
    strict = build_request(section, matches, by_id, mode=Mode.JSON_SCHEMA)
    assert "pattern_id" in plain.user and len(plain.user) > len(strict.user)


# --- разбор ответа -----------------------------------------------------


def test_extract_json_from_clean_answer():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_from_fenced_answer():
    assert extract_json('Вот результат:\n```json\n{"a": 1}\n```\nГотово') == {"a": 1}


def test_extract_json_from_chatty_answer():
    assert extract_json('Конечно! {"a": {"b": 2}} — надеюсь, подойдёт.') == {"a": {"b": 2}}


def test_extract_json_gives_up_cleanly():
    assert extract_json("никакого json тут нет") is None
    assert extract_json("") is None


# --- проверка ----------------------------------------------------------


def test_invented_pattern_is_fatal(context):
    section, matches, by_id = context
    request = build_request(section, matches, by_id)
    problems = check({"pattern_id": "p99", "fills": []}, request, None)
    assert problems and problems[0].fatal


def test_unknown_slot_is_reported(context):
    section, matches, by_id = context
    request = build_request(section, matches, by_id)
    pattern = by_id[request.candidates[0]]
    payload = {
        "pattern_id": pattern.id,
        "fills": [{"slot_id": "s99", "kind": "text", "text": "нет такого слота"}],
    }
    assert any(p.code == "unknown_slot" for p in check(payload, request, pattern))


def test_overlong_text_is_reported(context):
    section, matches, by_id = context
    request = build_request(section, matches, by_id)
    pattern = by_id[request.candidates[0]]
    slot = next(s for s in pattern.slots if s.capacity and s.content_type == "text")
    payload = {
        "pattern_id": pattern.id,
        "fills": [{"slot_id": slot.id, "kind": "text", "text": "я" * (slot.capacity.max_chars + 50)}],
    }
    assert any(p.code == "too_long" for p in check(payload, request, pattern))


# --- ремонт ------------------------------------------------------------


def test_repair_drops_unknown_slots_and_reports_it(context):
    section, matches, by_id = context
    request = build_request(section, matches, by_id)
    pattern = by_id[request.candidates[0]]
    fills, fixes = repair(
        {"pattern_id": pattern.id, "fills": [{"slot_id": "s99", "kind": "text", "text": "x"}]},
        request,
        pattern,
    )
    assert fills == ()
    assert any("несуществующего" in f for f in fixes)


def test_repair_trims_on_a_word_boundary(context):
    section, matches, by_id = context
    request = build_request(section, matches, by_id)
    pattern = by_id[request.candidates[0]]
    slot = next(s for s in pattern.slots if s.capacity and s.content_type == "text")
    limit = slot.capacity.max_chars
    long_text = " ".join(["слово"] * 60)
    fills, fixes = repair(
        {"pattern_id": pattern.id, "fills": [{"slot_id": slot.id, "kind": "text", "text": long_text}]},
        request,
        pattern,
    )
    assert len(fills[0].text) <= limit
    assert any("подрезан" in f for f in fixes)


def test_repair_coerces_text_into_a_list_slot(context):
    section, matches, by_id = context
    request = build_request(section, matches, by_id)
    pattern = next(
        (by_id[c] for c in request.candidates
         if any(s.content_type == "list" for s in by_id[c].slots)),
        None,
    )
    if pattern is None:
        pytest.skip("в фикстуре нет паттерна со слотом-списком")
    slot = next(s for s in pattern.slots if s.content_type == "list")
    fills, fixes = repair(
        {"pattern_id": pattern.id, "fills": [{"slot_id": slot.id, "kind": "text", "text": "один"}]},
        request,
        pattern,
    )
    assert fills[0].kind == "list" and fills[0].items == ("один",)
    assert any("обёрнут" in f for f in fixes)


def test_feedback_names_the_actual_problem(context):
    section, matches, by_id = context
    request = build_request(section, matches, by_id)
    problems = check({"pattern_id": "p99", "fills": []}, request, None)
    text = feedback(problems)
    assert "p99" in text and "JSON" in text
