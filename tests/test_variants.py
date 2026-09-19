"""Порождение колод и отбор вариантов. `ADR-0020`, `Z-26`, план `PLAN-6.0`."""

from __future__ import annotations

import dataclasses
import os

import pytest

from mimeo.analyze import analyze_template
from mimeo.model import DeckPlan
from mimeo.plan import load_content
from mimeo.plan.matching import DEFAULT_TUNING, Tuning
from mimeo.plan.variants import (
    DEFAULT_MIN_DISTANCE,
    Policy,
    Variant,
    default_policies,
    distance,
    generate,
    load_policies,
    report,
    select,
)
from mimeo.plan.quality import DeckScore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "samples", "stilnyj-shablon-korporativnoj-prezentacii.pptx")
CONTENT = os.path.join(ROOT, "examples", "content-demo.md")

pytestmark = pytest.mark.skipif(
    not os.path.exists(TEMPLATE), reason="шаблоны не коммитятся: tools/fetch_samples.py"
)


@pytest.fixture(scope="module")
def made():
    analysis = analyze_template(TEMPLATE)
    doc = load_content(CONTENT)
    variants = generate(
        doc, analysis.patterns, analysis.design_system.source.sha256, default_policies()
    )
    return analysis, doc, variants


# --- мера различия: испытание мутацией ---------------------------------
#
# `ADR-0019`, находка 4: прежде чем верить мерилу, сломать то, что оно меряет,
# и убедиться, что оно это видит. Для отбора вариантов мерило — `distance`, и
# без этих трёх проверок оно ничего не стоит.


def test_deck_compared_with_itself_is_zero(made):
    _, _, variants = made
    plan = variants[0].plan
    assert distance(plan, plan) == 0.0


def test_mutation_swapping_one_layout_is_noticed(made):
    """Одна подменённая раскладка обязана дать ненулевое различие."""
    _, _, variants = made
    plan = variants[0].plan
    assert len(plan.slides) >= 2
    other = next(
        s.pattern_id for s in plan.slides[1:] if s.pattern_id != plan.slides[0].pattern_id
    )
    mutated = dataclasses.replace(
        plan,
        slides=(dataclasses.replace(plan.slides[0], pattern_id=other),) + plan.slides[1:],
    )
    assert distance(plan, mutated) > 0.0


def test_mutation_shorter_deck_counts_the_tail_as_difference(made):
    """Колода вдвое короче обязана считаться сильно отличной, а не равной."""
    _, _, variants = made
    plan = variants[0].plan
    half = dataclasses.replace(plan, slides=plan.slides[: len(plan.slides) // 2])
    assert distance(plan, half) >= 0.4


def test_distance_is_symmetric(made):
    _, _, variants = made
    a, b = variants[0].plan, variants[-1].plan
    assert distance(a, b) == distance(b, a)


# --- порождение --------------------------------------------------------


def test_default_grid_has_twenty_seven_named_policies():
    policies = default_policies()
    assert len(policies) == 27
    assert len({p.name for p in policies}) == 27


def test_default_tuning_is_what_the_product_ships():
    """Умолчание обязано совпадать с константами продукта.

    Если оно разъедется, обычная сборка молча сменит поведение, а девять
    сдаточных колод собираются именно ею.
    """
    assert (DEFAULT_TUNING.repeat, DEFAULT_TUNING.slack, DEFAULT_TUNING.over) == (
        0.25,
        0.25,
        0.35,
    )


def test_generation_is_deterministic(made):
    analysis, doc, variants = made
    again = generate(
        doc, analysis.patterns, analysis.design_system.source.sha256, default_policies()
    )
    assert [v.layouts for v in variants] == [v.layouts for v in again]


def test_policies_actually_produce_different_decks(made):
    """Главный риск `ADR-0020` — «мнимое разнообразие». Проверяем прямо."""
    _, _, variants = made
    assert len({v.layouts for v in variants}) >= 2


@pytest.mark.parametrize(
    "knob, low, high",
    [("repeat", 0.0, 0.80), ("slack", 0.10, 0.45), ("over", 0.15, 0.90)],
)
def test_every_tuning_knob_changes_something_somewhere(knob, low, high):
    """Каждый рычаг политики обязан где-нибудь менять колоду.

    **Написан после того, как мутация прошла незамеченной.** 19 сентября я
    подменил `tuning.slack` обратно на константу модуля — то есть выключил
    одну треть политики, — и все восемнадцать тестов остались зелёными.
    Проверка, которая не может провалиться, ничего не стоит (`CLAUDE.md`).

    Утверждение слабое сознательно: «где-нибудь на корпусе», а не «на этом
    шаблоне». Сильнее и не бывает — замер показал, что `slack` меняет колоду
    лишь на 9 сочетаниях шаблона и контента из 28, и требовать от него большего
    значит выдумать требование.
    """
    import glob

    base = Tuning()
    found = []
    for template in sorted(glob.glob(os.path.join(ROOT, "samples", "*.pptx"))):
        analysis = analyze_template(template)
        lib, sha = analysis.patterns, analysis.design_system.source.sha256
        doc = load_content(CONTENT)
        plain = generate(doc, lib, sha, (Policy("base", base),))[0].layouts
        for value in (low, high):
            tuned = dataclasses.replace(base, **{knob: value})
            if generate(doc, lib, sha, (Policy("x", tuned),))[0].layouts != plain:
                found.append(os.path.basename(template))
                break
    assert found, (
        f"рычаг «{knob}» не изменил ни одной колоды на всём корпусе: "
        "он либо не подключён, либо не нужен"
    )


def test_default_tuning_reproduces_the_plain_planner(made):
    """Политика с умолчаниями обязана дать ровно то, что даёт обычная сборка."""
    from mimeo.plan import plan_deck

    analysis, doc, _ = made
    plain = plan_deck(doc, analysis.patterns, analysis.design_system.source.sha256)
    same = generate(
        doc,
        analysis.patterns,
        analysis.design_system.source.sha256,
        (Policy(name="default", tuning=Tuning()),),
    )[0]
    assert tuple(s.pattern_id for s in plain.slides) == same.layouts


# --- отбор -------------------------------------------------------------


def _fake(name: str, layouts: tuple[str, ...], key_broken: int) -> Variant:
    slides = tuple(
        dataclasses.replace(_TEMPLATE_SLIDE, index=i, pattern_id=p)
        for i, p in enumerate(layouts)
    )
    plan = dataclasses.replace(_TEMPLATE_PLAN, slides=slides)
    score = DeckScore(
        lost=0, broken=key_broken, tight=0, layouts=len(set(layouts)), thin=0,
        slides=len(layouts), fills=len(layouts),
    )
    return Variant(policy=Policy(name=name, tuning=Tuning()), plan=plan, score=score)


@pytest.fixture(scope="module", autouse=True)
def _templates(made):
    """Настоящие план и слайд как образцы для подделок: собирать DeckPlan
    вручную значит повторять контракт и разойтись с ним при первой правке."""
    global _TEMPLATE_PLAN, _TEMPLATE_SLIDE
    _, _, variants = made
    _TEMPLATE_PLAN = variants[0].plan
    _TEMPLATE_SLIDE = variants[0].plan.slides[0]


def test_select_takes_the_best_first():
    worse = _fake("worse", ("a", "b", "c", "d"), key_broken=5)
    best = _fake("best", ("e", "f", "g", "h"), key_broken=0)
    chosen, reason = select((worse, best), 2, 0.3)
    assert chosen[0].policy.name == "best"
    assert reason == ""


def test_select_skips_a_near_duplicate():
    """Второй по качеству, но почти такой же, не берётся."""
    best = _fake("best", ("a", "b", "c", "d"), key_broken=0)
    twin = _fake("twin", ("a", "b", "c", "x"), key_broken=1)      # различие 25%
    far = _fake("far", ("p", "q", "r", "s"), key_broken=2)        # различие 100%
    chosen, _ = select((best, twin, far), 2, 0.3)
    assert [v.policy.name for v in chosen] == ["best", "far"]


def test_select_refuses_rather_than_lowering_the_bar():
    """Порог не понижается молча, и причина называется словами (`Z-20`)."""
    best = _fake("best", ("a", "b", "c", "d"), key_broken=0)
    twin = _fake("twin", ("a", "b", "c", "x"), key_broken=1)
    chosen, reason = select((best, twin), 3, 0.3)
    assert len(chosen) == 1
    assert reason
    assert "3" in reason and "1" in reason


def test_select_breaks_ties_by_policy_name():
    """Ничья обязана разрешаться именем, а не порядком в памяти."""
    b = _fake("bbb", ("a", "b", "c", "d"), key_broken=0)
    a = _fake("aaa", ("a", "b", "c", "d"), key_broken=0)
    assert select((b, a), 1, 0.3)[0][0].policy.name == "aaa"
    assert select((a, b), 1, 0.3)[0][0].policy.name == "aaa"


def test_select_on_nothing_says_so():
    chosen, reason = select((), 3, 0.3)
    assert chosen == ()
    assert reason


# --- отчёт -------------------------------------------------------------


def test_report_states_template_compliance(made):
    """Строка про соблюдение правил шаблона обязана быть.

    Она появилась из обратного плана от ИКР (`PLAN-6.0`): преимущество есть по
    построению, но если о нём не сказать, эксперт его не увидит.
    """
    _, _, variants = made
    chosen, reason = select(variants, 3, DEFAULT_MIN_DISTANCE)
    text = "\n".join(report(chosen, reason))
    assert "правила шаблона" in text
    assert "ADR-0004" in text


def test_report_shows_pairwise_distance(made):
    _, _, variants = made
    chosen, reason = select(variants, 3, DEFAULT_MIN_DISTANCE)
    if len(chosen) < 2:
        pytest.skip("на этом шаблоне отобран один вариант")
    assert "различие" in "\n".join(report(chosen, reason))


# --- конфиг ------------------------------------------------------------


def test_config_is_read_and_says_where_from():
    policies, min_distance, source = load_policies()
    assert len(policies) >= 3
    assert 0.0 < min_distance <= 1.0
    assert source.endswith("variants.json"), "конфиг не прочитан, работает встроенная сетка"


def test_missing_config_is_not_an_error_and_is_visible(tmp_path):
    policies, min_distance, source = load_policies(str(tmp_path / "нет.json"))
    assert policies and min_distance == DEFAULT_MIN_DISTANCE
    assert source == "встроенная сетка"
