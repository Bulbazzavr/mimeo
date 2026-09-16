"""Весы для колоды: дешёвый ярус. `PLAN-2.5`, `ADR-0019`.

Тесты делятся на две группы, и обе обязательны.

**Мутации.** Весы, которые не могут поставить плохую оценку, бесполезны и
опаснее их отсутствия — им верят. Каждая мутация портит колоду в одну сторону и
обязана ухудшить **свою** ступень.

**Различение.** Мутации проверяют, что весы не ошибаются, но не проверяют, что
они вообще различают: весы, ставящие всем одинаково, мутации проходят. Это
расхождение нашёл обратный план от ИКР (`PLAN-2.5`), и оно закрывается здесь.
"""

from __future__ import annotations

import dataclasses

import pytest

from mimeo.analyze import analyze_template
from mimeo.plan.content import load as load_markdown
from mimeo.plan.deterministic import plan_deck
from mimeo.plan.quality import (
    DeckScore,
    better,
    is_broken,
    is_tight,
    max_repairable_ratio,
    score_deck,
)

DEMO = "examples/content-demo.md"


@pytest.fixture(scope="module")
def planned(multi_template_path):
    analysis = analyze_template(multi_template_path)
    doc = load_markdown(DEMO)
    plan = plan_deck(doc, analysis.patterns, analysis.design_system.source.sha256)
    return plan, analysis.patterns


# --- граница «сломано безвозвратно» ------------------------------------


def test_slot_at_readability_floor_cannot_be_repaired_at_all():
    """Слот на 10 pt уже стоит на полу: ужимать некуда, предел ниже единицы.

    Это и есть ответ на дефект слайда 3 VK Tech — VERIFY его не починил не
    потому, что не заметил, а потому что не мог.
    """
    assert max_repairable_ratio(10.0) < 1.0


def test_bigger_font_leaves_more_room():
    """Чем крупнее кегль донора, тем больше перебора вытянет ремонт."""
    limits = [max_repairable_ratio(pt) for pt in (10, 12, 14, 16, 20, 24, 44)]
    assert limits == sorted(limits)
    assert limits[0] < limits[-1]


def test_hard_floor_caps_the_limit_for_huge_fonts():
    """У обложечных кеглей предел упирается в жёсткий минимум шкалы, а не растёт вечно."""
    assert max_repairable_ratio(140.0) == pytest.approx(max_repairable_ratio(44.0))


def test_unknown_font_size_is_not_counted_as_broken(planned):
    """«Проверить не смог» — не то же самое, что «чисто». Молчим, а не обвиняем."""
    plan, patterns = planned
    pattern = patterns.patterns[0]
    slot = next(s for s in pattern.slots if s.capacity and s.capacity.max_chars)
    blind = dataclasses.replace(
        slot, capacity=dataclasses.replace(slot.capacity, basis="без указания кегля")
    )
    fill = next(
        f for sl in plan.slides for f in sl.fills if f.slot_id == slot.id and f.text
    )
    assert is_broken(blind, fill) is False


# --- мутации: каждая бьёт в свою ступень -------------------------------


def test_mutation_losing_content_worsens_the_first_step(planned):
    """Выбросить контент — обязана вырасти ступень потерь, и вариант выбывает."""
    plan, patterns = planned
    base = score_deck(plan, patterns)
    hurt = score_deck(dataclasses.replace(plan, unplaced=("b01", "b02")), patterns)
    assert base.lost == 0 and base.admissible
    assert hurt.lost == 2 and not hurt.admissible
    assert better(base, hurt)


def test_mutation_doubling_text_worsens_the_broken_step(planned):
    """Удлинить все тексты — обязана вырасти ступень поломок."""
    plan, patterns = planned
    base = score_deck(plan, patterns)
    slides = tuple(
        dataclasses.replace(
            sl,
            fills=tuple(
                dataclasses.replace(f, text=f.text * 8) if f.text else f
                for f in sl.fills
            ),
        )
        for sl in plan.slides
    )
    hurt = score_deck(dataclasses.replace(plan, slides=slides), patterns)
    assert hurt.broken > base.broken
    assert better(base, hurt)


def test_mutation_one_layout_everywhere_worsens_the_diversity_step(planned):
    """Свести колоду к одной раскладке — обязано упасть разнообразие."""
    plan, patterns = planned
    base = score_deck(plan, patterns)
    assert base.layouts > 1, "фикстура обязана давать разные раскладки"
    same = plan.slides[0].pattern_id
    slides = tuple(dataclasses.replace(sl, pattern_id=same) for sl in plan.slides)
    hurt = score_deck(dataclasses.replace(plan, slides=slides), patterns)
    assert hurt.layouts == 1
    assert better(base, hurt)


def test_mutation_emptying_text_worsens_the_thin_step(planned):
    """Оставить в слотах по одному знаку — обязана вырасти ступень «на донышке»."""
    plan, patterns = planned
    base = score_deck(plan, patterns)
    slides = tuple(
        dataclasses.replace(
            sl,
            fills=tuple(
                dataclasses.replace(f, text=".") if f.text else f for f in sl.fills
            ),
        )
        for sl in plan.slides
    )
    hurt = score_deck(dataclasses.replace(plan, slides=slides), patterns)
    assert hurt.thin > base.thin


def test_mutation_mild_overflow_worsens_the_tight_step(planned):
    """Удлинить тексты умеренно — обязана вырасти ступень «переполнено, поправимо».

    Ступень заведена 16 сентября после сверки с глазом: без неё колода VK
    WorkSpace получала ноль поломок, а на растре три заголовка карточек уезжали
    под плашку (`WORKLOG/2026-09-16-scales-vs-eye.md`).
    """
    plan, patterns = planned
    base = score_deck(plan, patterns)
    slides = tuple(
        dataclasses.replace(
            sl,
            fills=tuple(
                dataclasses.replace(f, text=f.text * 2) if f.text else f
                for f in sl.fills
            ),
        )
        for sl in plan.slides
    )
    hurt = score_deck(dataclasses.replace(plan, slides=slides), patterns)
    assert hurt.tight + hurt.broken > base.tight + base.broken
    assert better(base, hurt)


def test_broken_and_tight_never_count_the_same_slot(planned):
    """Ступени не пересекаются: сломанный слот не считается ещё и тесным.

    Иначе один слот попал бы в обе ступени, и вес его удвоился бы молча.
    """
    plan, patterns = planned
    by_id = {p.id: p for p in patterns.patterns}
    for slide in plan.slides:
        pattern = by_id.get(slide.pattern_id)
        if pattern is None:
            continue
        slots = {s.id: s for s in pattern.slots}
        for fill in slide.fills:
            slot = slots.get(fill.slot_id)
            if slot is None:
                continue
            assert not (is_broken(slot, fill) and is_tight(slot, fill))


def test_a_tight_slot_is_worse_than_a_roomy_one():
    """При прочих равных колода без переполнений лучше, даже если ремонт справится."""
    tight = DeckScore(lost=0, broken=0, tight=3, layouts=7, thin=1, slides=9, fills=26)
    roomy = DeckScore(lost=0, broken=0, tight=0, layouts=7, thin=1, slides=9, fills=26)
    assert better(roomy, tight)


# --- старшинство ступеней ----------------------------------------------


def test_lost_content_outweighs_everything_below():
    """Потеря контента не окупается ни разнообразием, ни отсутствием поломок."""
    perfect_but_lossy = DeckScore(lost=1, broken=0, tight=0, layouts=9, thin=0, slides=9, fills=30)
    ugly_but_whole = DeckScore(lost=0, broken=5, tight=9, layouts=1, thin=20, slides=9, fills=30)
    assert better(ugly_but_whole, perfect_but_lossy)


def test_diversity_never_pays_for_a_broken_slot():
    """Разнообразие стоит ниже поломки: сломанный слот видно, повтор — терпимо.

    Это прямо тот размен, который сейчас делает анти-повтор вслепую
    (`WORKLOG/2026-09-16-rank-capacity.md`): раскладки с нулевым перебором
    вытеснялись ради несхожести, и никто размен не взвешивал.
    """
    diverse_broken = DeckScore(lost=0, broken=1, tight=0, layouts=9, thin=0, slides=9, fills=30)
    dull_whole = DeckScore(lost=0, broken=0, tight=0, layouts=2, thin=0, slides=9, fills=30)
    assert better(dull_whole, diverse_broken)


def test_tie_on_every_step_is_a_tie():
    """Полное равенство обязано быть равенством, а не случайным порядком."""
    one = DeckScore(lost=0, broken=1, tight=3, layouts=4, thin=2, slides=9, fills=30)
    two = DeckScore(lost=0, broken=1, tight=3, layouts=4, thin=2, slides=6, fills=12)
    assert not better(one, two) and not better(two, one)


# --- различение: весы обязаны не только не ошибаться, но и различать ----


def test_score_distinguishes_variants_of_the_same_deck(planned):
    """Четыре мутации одной колоды обязаны дать четыре разных ключа.

    Весы, ставящие всем одинаково, проходят все мутационные тесты выше и при
    этом бесполезны. Найдено обратным планом от ИКР.
    """
    plan, patterns = planned
    same = plan.slides[0].pattern_id
    variants = [
        plan,
        dataclasses.replace(plan, unplaced=("b01",)),
        dataclasses.replace(
            plan, slides=tuple(dataclasses.replace(s, pattern_id=same) for s in plan.slides)
        ),
        dataclasses.replace(
            plan,
            slides=tuple(
                dataclasses.replace(
                    s,
                    fills=tuple(
                        dataclasses.replace(f, text=f.text * 8) if f.text else f
                        for f in s.fills
                    ),
                )
                for s in plan.slides
            ),
        ),
    ]
    keys = [score_deck(v, patterns).key for v in variants]
    assert len(set(keys)) == len(keys), keys


def test_score_is_deterministic(planned):
    """Один и тот же план даёт одну и ту же оценку. Иначе отбор невоспроизводим."""
    plan, patterns = planned
    assert score_deck(plan, patterns) == score_deck(plan, patterns)
