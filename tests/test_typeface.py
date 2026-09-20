"""Гарнитура слота, не предназначенная для прозы. `Z-43`, `PLAN-7.3`.

Дефект найден **растром**, а не тестом: правила шаблона не нарушены — Consolas
есть в самом шаблоне VK Tech, — и все наши структурные проверки давали ноль.
Поэтому здесь проверяется не «файл цел», а «проза не легла в слот под код».

Про то, почему по имени гарнитуры, а не по `pitchFamily` или `panose`, —
`DOM-TEXT §11`: оба атрибута OOXML замерены по четырнадцати шаблонам и негодны.
"""

from __future__ import annotations

import os

import pytest

from mimeo.analyze import analyze_template
from mimeo.analyze.typeface import (
    ICON,
    MONO,
    PROSE,
    classify,
    load_config,
    pua_ratio,
    tokens,
)
from mimeo.plan import load_content, plan_deck
from mimeo.plan.matching import DEFAULT_TUNING, Tuning, typeface_penalty

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT = os.path.join(ROOT, "examples", "content-mimeo.md")
#: Единственный шаблон корпуса с пиктограммным слотом, куда проза влезает:
#: донором взята фигура-шпаргалка со всеми глифами шрифта, и ёмкость вышла 184
#: знака вместо одного (`WORKLOG/2026-09-20-z43-baseline.md`, замер 6).
ICONY = os.path.join(ROOT, "samples", "business_plan.pptx")

TEXTY = ("text", "list", "number")


# --- правило опознания -------------------------------------------------


def test_tokens_split_on_camel_case_and_separators():
    """`JetBrainsMono` без пробелов обязан разбираться, иначе список имён
    закрытый по построению."""
    assert tokens("Courier New") == ("courier", "new")
    assert tokens("JetBrainsMono") == ("jet", "brains", "mono")
    assert tokens(None) == ()


@pytest.mark.parametrize("name", [
    "Consolas", "Courier New", "Monaco", "Roboto Mono", "JetBrains Mono",
    "Fira Code", "IBM Plex Mono", "Source Code Pro", "PT Mono", "Terminal",
])
def test_monospace_families_are_recognised(name):
    """Перечислены из них только первые три: правило открытое."""
    assert classify(name, "обычный текст доклада") == MONO


@pytest.mark.parametrize("name", [
    "Monotype Corsiva",   # содержит «mono» подстрокой, но не словом
    "Terminal Dosis",     # рубленый; ради него «terminal» не в токенах
    "Codec Pro", "Encode Sans", "Comic Sans MS", "Play", "Open Sans", "Lato Bold",
])
def test_proportional_families_are_not_mistaken(name):
    """Ложное срабатывание хуже пропуска: оно уводит вёрстку без причины."""
    assert classify(name, "обычный текст доклада") == PROSE


def test_icon_font_is_recognised_by_text_not_by_name():
    """Главное свойство правила: имя шрифта может быть любым."""
    assert classify("СовершенноНеизвестный", "  ") == ICON
    assert classify("Wingdings", "J K L") == ICON  # этот класс — только по имени


def test_empty_shape_is_not_an_icon():
    """Деления на ноль нет, и пустая фигура не объявляется пиктограммной."""
    assert pua_ratio("") == 0.0
    assert pua_ratio("   ") == 0.0
    assert classify("Play", "") == PROSE


def test_single_private_use_char_inside_prose_is_not_enough():
    """Порог половины: служебный знак в прозе не делает слот пиктограммным."""
    assert classify("Play", "обычный текст со значком  внутри") == PROSE


def test_missing_config_falls_back_and_says_so():
    """Отсутствие файла — не ошибка, а состояние, и оно видно по `loaded`."""
    cfg = load_config(os.path.join(ROOT, "config", "нет-такого-файла.json"))
    assert cfg.loaded is False
    assert classify("Consolas", "x", cfg) == MONO  # встроенные значения работают


def test_shipped_config_is_readable():
    cfg = load_config()
    assert cfg.loaded is True
    assert "mono" in cfg.mono_tokens and "consolas" in cfg.mono_names


# --- штраф в ранге -----------------------------------------------------


def test_penalty_outweighs_any_repeat_penalty_of_the_same_policy():
    """Замер показал, зачем привязка: при равных штрафах они гасят друг друга,
    и свежая раскладка с кодовым слотом обыгрывает уже использованную обычную.
    `config/variants.json` гоняет `repeat` до 0.8."""
    for repeat in (0.0, 0.25, 0.8):
        tuning = Tuning(repeat=repeat)
        assert typeface_penalty(tuning) > repeat * 3
    assert typeface_penalty(DEFAULT_TUNING) > typeface_penalty(Tuning(repeat=0.0))


# --- на настоящих шаблонах ---------------------------------------------


@pytest.mark.skipif(not os.path.exists(ICONY), reason="шаблоны не коммитятся: tools/fetch_samples.py")
def test_icon_slots_are_marked_on_a_real_template():
    analysis = analyze_template(ICONY)
    marked = [
        (p.id, s.id) for p in analysis.patterns.patterns for s in p.slots
        if s.typeface_kind == ICON
    ]
    assert marked, "в этом шаблоне есть linecons — хоть один слот обязан быть помечен"


@pytest.mark.skipif(not os.path.exists(ICONY), reason="шаблоны не коммитятся: tools/fetch_samples.py")
def test_prose_no_longer_lands_in_an_icon_slot():
    """Было 8 слайдов по корпусу, стало 0 (`WORKLOG/2026-09-20-z43-result.md`)."""
    analysis = analyze_template(ICONY)
    foreign = {
        p.id: {s.id for s in p.slots
               if s.typeface_kind in (MONO, ICON) and s.content_type in TEXTY}
        for p in analysis.patterns.patterns
    }
    plan = plan_deck(load_content(CONTENT), analysis.patterns, analysis.design_system)
    hits = [
        slide.index for slide in plan.slides
        for fill in slide.fills
        if fill.slot_id in foreign.get(slide.pattern_id, set()) and fill.kind in TEXTY
    ]
    assert hits == [], f"проза легла в слот с чужой гарнитурой на слайдах {hits}"


@pytest.mark.skipif(not os.path.exists(ICONY), reason="шаблоны не коммитятся: tools/fetch_samples.py")
def test_slots_without_a_run_are_undetermined_not_clean():
    """«Проверить не смог» и «проверено и чисто» — разные вещи, и путать их
    нельзя: слоты запасного пути из макетов (`ADR-0006`) прогона не имеют."""
    analysis = analyze_template(ICONY)
    kinds = {s.typeface_kind for p in analysis.patterns.patterns for s in p.slots}
    assert None in kinds, "слоты без прогона обязаны оставаться неопределёнными"
    assert kinds <= {None, PROSE, MONO, ICON}


def test_warning_names_the_slide_when_the_slot_is_used_anyway():
    """Штраф, а не запрет: раскладку берут, когда лучшей нет. Тогда движок
    обязан сказать об этом вслух — молчаливый дефект хуже названного."""
    poor = os.path.join(ROOT, "samples", "2411-Performance_Up.pptx")
    if not os.path.exists(poor):
        pytest.skip("шаблоны не коммитятся: tools/fetch_samples.py")
    analysis = analyze_template(poor)
    plan = plan_deck(load_content(CONTENT), analysis.patterns, analysis.design_system)
    said = [w for w in plan.warnings if "моноширинным шрифтом" in w]
    assert said, "на бедном шаблоне слот всё-таки занят — об этом обязано быть сказано"
    assert "слайд" in said[0]
