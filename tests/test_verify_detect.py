"""Детектор дефектов вёрстки: адресация, область, честность отчёта.

PowerPoint не запускается: `Measurement` собирается вручную. Проверяется то,
что нашли логические проверки плана (`PLAN-4.0`, шаги 2–3):

* адрес дефекта — в координатах **плана**, а не в номерах слайдов файла;
* нумерация не угадывается: слайд, который не собрался, сдвигает файл;
* донорские фигуры сообщаются, но не чинятся;
* отказ измерителя не превращается в «дефектов нет».
"""

from __future__ import annotations

from mimeo.model import (
    DeckPlan,
    Fill,
    Pattern,
    PatternLibrary,
    PatternSource,
    PlannedSlide,
    PlanSource,
    Slot,
)
from mimeo.verify import DONOR_OVERFLOW, OVERFLOW_HEIGHT, OVERFLOW_WIDTH, find_defects
from mimeo.verify.metrics import Measurement, ShapeMetric


def slot(slot_id: str, shape_id: str, role: str = "body") -> Slot:
    return Slot(
        id=slot_id, role=role, content_type="text", rect=None,
        type_role=None, capacity=None, required=False, shape_id=shape_id,
    )


def pattern(pattern_id: str, slots: tuple[Slot, ...]) -> Pattern:
    return Pattern(
        id=pattern_id, kind="text", donor_part="/ppt/slides/slide1.xml", donor_index=0,
        slots=slots, members=(0,), cohesion=None, donor_reason="", source="slides",
    )


def library(*patterns: Pattern) -> PatternLibrary:
    return PatternLibrary(source=PatternSource("t.pptx", "0" * 64), patterns=patterns)


def deck(*slides: PlannedSlide) -> DeckPlan:
    return DeckPlan(source=PlanSource("c.md", "0" * 64, "0" * 64), slides=slides)


def planned(index: int, pattern_id: str, *slot_ids: str) -> PlannedSlide:
    return PlannedSlide(
        index=index, pattern_id=pattern_id,
        fills=tuple(Fill(slot_id=s, kind="text", text="…") for s in slot_ids),
        reason="",
    )


def metric(slide: int, shape_id: str, text_height: float, text_width: float = 10.0) -> ShapeMetric:
    return ShapeMetric(
        slide=slide, shape_id=shape_id, width=100.0, height=50.0,
        text_width=text_width, text_height=text_height,
        margin_left=0.0, margin_right=0.0, margin_top=0.0, margin_bottom=0.0,
        autofit=0, chars=42,
    )


def measurement(*shapes: ShapeMetric, status: str = "ok", note: str = "") -> Measurement:
    return Measurement(deck="d.pptx", status=status, shapes=shapes, slides=2, note=note)


LIB = library(pattern("p01", (slot("s01", "11"), slot("s02", "12", "title"))))
PLAN = deck(planned(0, "p01", "s01"), planned(1, "p01", "s01"))


# --- адресация ----------------------------------------------------------


def test_defect_carries_plan_coordinates():
    ins = find_defects(measurement(metric(1, "11", 120.0)), PLAN, LIB, (0, 1))
    (d,) = ins.defects
    assert d.kind == OVERFLOW_HEIGHT
    assert d.slide_index == 0 and d.slot_id == "s01" and d.repairable


def test_slide_numbering_is_taken_from_what_was_written():
    """Слайд 0 не собрался: в файле один слайд, и он — слайд 1 плана.

    Позиционное соответствие дало бы адрес 0 и починило бы не тот слайд.
    """
    ins = find_defects(measurement(metric(1, "11", 120.0)), PLAN, LIB, (1,))
    (d,) = ins.defects
    assert d.slide_index == 1


def test_checked_slots_counts_only_our_fills():
    ins = find_defects(
        measurement(metric(1, "11", 10.0), metric(1, "999", 10.0)), PLAN, LIB, (0,)
    )
    assert ins.checked_slots == 1


# --- область ------------------------------------------------------------


def test_donor_shape_is_reported_but_not_repairable():
    """В шаблонах переполняется 14% собственных фигур — чинить их нельзя."""
    ins = find_defects(measurement(metric(1, "777", 120.0)), PLAN, LIB, (0,))
    (d,) = ins.defects
    assert d.kind == DONOR_OVERFLOW and not d.repairable and not d.ours
    assert ins.repairable == ()


def test_donor_shape_that_fits_is_not_mentioned():
    ins = find_defects(measurement(metric(1, "777", 10.0)), PLAN, LIB, (0,))
    assert ins.defects == ()


# --- виды дефектов ------------------------------------------------------


def test_width_overflow_is_separate_and_not_repairable():
    ins = find_defects(
        measurement(metric(1, "11", 10.0, text_width=300.0)), PLAN, LIB, (0,)
    )
    (d,) = ins.defects
    assert d.kind == OVERFLOW_WIDTH and not d.repairable


def test_height_overflow_hides_width_overflow():
    """Один слот не должен давать два дефекта: ремонт по высоте уберёт оба."""
    ins = find_defects(
        measurement(metric(1, "11", 120.0, text_width=300.0)), PLAN, LIB, (0,)
    )
    assert [d.kind for d in ins.defects] == [OVERFLOW_HEIGHT]


def test_text_within_tolerance_is_not_a_defect():
    assert find_defects(measurement(metric(1, "11", 50.5)), PLAN, LIB, (0,)).defects == ()


# --- честность отчёта ---------------------------------------------------


def test_failed_measurement_is_not_a_clean_result():
    """Пустой список дефектов при несостоявшемся замере — та же ложь, что
    «0 проблем» у файла, который не открывается (`Z-20`)."""
    ins = find_defects(
        measurement(status="open_failed", note="E_FAIL"), PLAN, LIB, (0, 1)
    )
    assert not ins.measured
    assert ins.defects == ()
    assert "E_FAIL" in ins.note


def test_clean_deck_is_measured_and_empty():
    ins = find_defects(measurement(metric(1, "11", 10.0)), PLAN, LIB, (0,))
    assert ins.measured and ins.defects == ()


def test_defects_are_ordered_deterministically():
    shapes = (
        metric(2, "11", 120.0),
        metric(1, "12", 120.0),
        metric(1, "11", 120.0),
    )
    lib = library(pattern("p01", (slot("s01", "11"), slot("s02", "12", "title"))))
    plan = deck(planned(0, "p01", "s01", "s02"), planned(1, "p01", "s01"))
    ins = find_defects(measurement(*shapes), plan, lib, (0, 1))
    assert [(d.slide_index, d.slot_id) for d in ins.defects] == [
        (0, "s01"), (0, "s02"), (1, "s01"),
    ]
