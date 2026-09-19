"""Доноры, которые нельзя клонировать дважды. `Z-44`, `DOM-PKG §9`, `PLAN-6.1`.

Дефект худшего класса: PowerPoint отказывается открывать файл **целиком**, а
все наши структурные проверки при этом дают ноль. Поэтому здесь проверяется не
«структура непротиворечива», а «донор с исключительной частью не повторён».
"""

from __future__ import annotations

import collections
import dataclasses
import os

import pytest

from mimeo.analyze import analyze_template
from mimeo.analyze.patterns import _EXCLUSIVE_RELS, _SHAREABLE_RELS, _donor_parts
from mimeo.plan import load_content, plan_deck

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: Единственный шаблон корпуса с диаграммой и внедрёнными объектами.
RISKY = os.path.join(ROOT, "samples", "business-plan-ppt-template-10-slides-creative.pptx")
CONTENT = os.path.join(ROOT, "examples", "content-mimeo.md")

pytestmark = pytest.mark.skipif(
    not os.path.exists(RISKY), reason="шаблоны не коммитятся: tools/fetch_samples.py"
)


@pytest.fixture(scope="module")
def analysis():
    return analyze_template(RISKY)


def test_exclusive_and_shareable_lists_do_not_overlap():
    """Тип не может быть одновременно разделяемым и исключительным."""
    assert not (set(_EXCLUSIVE_RELS) & set(_SHAREABLE_RELS))


def test_chart_and_ole_donors_are_marked(analysis):
    """Признак читается из связей донора, а не назначается вручную."""
    marked = {p.id for p in analysis.patterns.patterns if p.exclusive}
    assert marked, "в этом шаблоне есть диаграмма и OLE — хоть один донор обязан быть помечен"
    kinds = {p.kind for p in analysis.patterns.patterns if p.exclusive}
    assert "chart" in kinds


def test_image_only_donor_is_not_marked(analysis):
    """Картинки разделяются законно: замер `p12` ×3 дал целый файл."""
    plain = [p for p in analysis.patterns.patterns if not p.exclusive]
    assert plain, "не может быть, чтобы все доноры шаблона были исключительными"


def test_exclusive_donor_is_never_used_twice(analysis):
    """Главная проверка задачи: повтора нет ни одного."""
    doc = load_content(CONTENT)
    plan = plan_deck(doc, analysis.patterns, analysis.design_system.source.sha256)
    excl = {p.id for p in analysis.patterns.patterns if p.exclusive}
    counts = collections.Counter(s.pattern_id for s in plan.slides)
    repeated = {pid: n for pid, n in counts.items() if pid in excl and n > 1}
    assert not repeated, (
        f"донор с исключительной частью повторён: {repeated}. "
        "PowerPoint не откроет такой файл (Z-44)."
    )


def test_mutation_without_the_ban_the_donor_repeats(analysis):
    """Прежде чем верить проверке выше — убедиться, что она может упасть.

    Снимаем признак и убеждаемся, что повтор **появляется**. Если он не
    появится, проверка выше ничего не стоит: она будет зелёной и при снятом
    запрете, и при работающем.
    """
    doc = load_content(CONTENT)
    off = dataclasses.replace(
        analysis.patterns,
        patterns=tuple(dataclasses.replace(p, exclusive=False)
                       for p in analysis.patterns.patterns),
    )
    plan = plan_deck(doc, off, analysis.design_system.source.sha256)
    excl = {p.id for p in analysis.patterns.patterns if p.exclusive}
    counts = collections.Counter(s.pattern_id for s in plan.slides)
    assert any(counts[pid] > 1 for pid in excl), (
        "без запрета повтора не возникло — значит запрет проверить нечем, "
        "и тест на его отсутствие зелёный по случайности"
    )


def test_the_ban_is_reported_in_words(analysis):
    """Молчаливое изменение поведения — тот же молчаливый ноль (`Z-20`)."""
    doc = load_content(CONTENT)
    plan = plan_deck(doc, analysis.patterns, analysis.design_system.source.sha256)
    said = [w for w in plan.warnings if "Z-44" in w]
    assert said, "запрет сработал, а в плане об этом ни слова"


def test_the_ban_does_not_make_the_deck_worse(analysis):
    """Запрет не должен покупать открываемость ценой качества.

    Замер 19 сентября: на всех четырнадцати шаблонах корпуса весы с запретом
    не хуже, чем без него, а на этом шаблоне даже лучше.
    """
    from mimeo.plan.quality import score_deck

    doc = load_content(CONTENT)
    sha = analysis.design_system.source.sha256
    off = dataclasses.replace(
        analysis.patterns,
        patterns=tuple(dataclasses.replace(p, exclusive=False)
                       for p in analysis.patterns.patterns),
    )
    with_ban = score_deck(plan_deck(doc, analysis.patterns, sha), analysis.patterns)
    without = score_deck(plan_deck(doc, off, sha), off)
    assert with_ban.key <= without.key


def test_unknown_part_types_are_surfaced_not_swallowed(analysis):
    """Список исключительных типов неполон по построению.

    Незнакомая часть обязана давать предупреждение при повторе, а не
    молчание: иначе новый тип проявится неоткрывающимся файлом у эксперта
    (`PLAN-6.1`, обратный план, п. 7).
    """
    doc = load_content(CONTENT)
    sha = analysis.design_system.source.sha256
    # Берём донора, который в этой колоде **действительно повторяется**:
    # подделывать признак у неповторяющегося значит написать тест, который
    # пропустится и ничего не проверит.
    plain = plan_deck(doc, analysis.patterns, sha)
    counts = collections.Counter(s.pattern_id for s in plain.slides)
    excl = {p.id for p in analysis.patterns.patterns if p.exclusive}
    repeated = [pid for pid, n in counts.items() if n > 1 and pid not in excl]
    assert repeated, "в этой колоде нет повторов вовсе — проверять нечего"

    victim = repeated[0]
    lib = dataclasses.replace(
        analysis.patterns,
        patterns=tuple(
            dataclasses.replace(p, unknown_parts=("slicer",)) if p.id == victim else p
            for p in analysis.patterns.patterns
        ),
    )
    plan = plan_deck(doc, lib, sha)
    assert any("slicer" in w for w in plan.warnings), (
        f"донор {victim} повторён, несёт часть незнакомого вида — "
        "и об этом не сказано ни слова"
    )


def test_donor_parts_reads_real_relationships(analysis):
    """Функция должна читать связи, а не гадать по имени файла.

    Проверяется парой: у донора-диаграммы признак стоит, у донора с одними
    картинками — нет. Одного утверждения мало: `True` вернулось бы и от
    функции, которая всегда говорит `True`.
    """
    from mimeo.analyze.deck import load_deck
    from mimeo.opc.package import Package

    chart_donor = next(p for p in analysis.patterns.patterns if p.kind == "chart")
    plain_donor = next(p for p in analysis.patterns.patterns if not p.exclusive)
    with Package(RISKY) as pkg:
        deck = load_deck(pkg)
        assert _donor_parts(deck, chart_donor.donor_part)[0] is True
        assert _donor_parts(deck, plain_donor.donor_part)[0] is False
