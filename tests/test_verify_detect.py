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
from mimeo.verify import (
    DONOR_OVERFLOW,
    OCCLUDED,
    OVERFLOW_HEIGHT,
    OVERFLOW_WIDTH,
    find_defects,
)
from mimeo.verify.metrics import Box, Measurement, ShapeMetric


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


def test_width_and_donor_each_go_to_their_own_list():
    """Наша ширина и донорская фигура — разные вещи, и списки у них разные.

    До 22 сентября обе жили только в остатке `defects - ours - occluded`, и
    сводка звала весь остаток «донорским». На корпусе он целиком был нашей
    шириной — 27 из 27 (`Z-52`)."""
    shapes = (
        metric(1, "11", 10.0, text_width=300.0),   # наш слот, текст шире места
        metric(1, "777", 120.0),                   # донорская фигура
        metric(2, "11", 120.0),                    # наш слот, выше места
    )
    ins = find_defects(measurement(*shapes), PLAN, LIB, (0, 1))
    assert [(d.slide_index, d.slot_id) for d in ins.overflow_width] == [(0, "s01")]
    assert [d.shape_id for d in ins.donor_overflow] == ["777"]
    assert [(d.slide_index, d.slot_id) for d in ins.repairable] == [(1, "s01")]
    # Ничего не потеряно и ничего не посчитано дважды.
    parts = ins.overflow_width + ins.donor_overflow + ins.repairable + ins.occluded
    assert sorted(id(d) for d in parts) == sorted(id(d) for d in ins.defects)


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


# --- Z-47: текст, закрытый фигурой поверх него --------------------------
#
# Дефект найден растром, а числа его не видели вовсе. Мерка строилась в пять
# заходов, и четыре первых были опровергнуты — отсюда три ловушки ниже:
# карточка под текстом, прозрачная рамка и чужой текст рядом.


def occl_metric(shape_id: str, left: float, top: float, tw: float, th: float) -> ShapeMetric:
    return ShapeMetric(
        slide=1, shape_id=shape_id, width=tw, height=th,
        text_width=tw, text_height=th,
        margin_left=0.0, margin_right=0.0, margin_top=0.0, margin_bottom=0.0,
        autofit=0, chars=42, left=left, top=top,
    )


def box(shape_id: str, x: float, y: float, w: float, h: float,
        z: int, opacity: float = 1.0, shape_type: int = 1) -> Box:
    return Box(slide=1, shape_id=shape_id, x=x, y=y, w=w, h=h,
               z=z, opacity=opacity, shape_type=shape_type)


def occl_measurement(*boxes: Box, shapes=()) -> Measurement:
    return Measurement(deck="d.pptx", status="ok", shapes=tuple(shapes),
                       slides=2, boxes=tuple(boxes))


def test_picture_over_the_text_is_reported():
    """Ровно случай VK Tech: декоративная картинка лежит поверх надписи."""
    text = occl_metric("11", left=0, top=0, tw=100, th=20)
    m = occl_measurement(
        box("11", 0, 0, 100, 20, z=8000),
        box("99", 50, 0, 100, 20, z=9000, shape_type=13),
        shapes=(text,),
    )
    (d,) = [x for x in find_defects(m, PLAN, LIB, (0, 1)).defects if x.kind == OCCLUDED]
    assert d.slot_id == "s01" and d.repairable is False
    assert 0.45 < d.ratio < 0.55, d.ratio


def test_the_card_under_the_text_is_not_occlusion():
    """Главная ловушка: карточка накрывает свой текст на 100%, и это норма.

    Без порядка отрисовки мерка давала 86 ложных срабатываний именно на этом.
    """
    text = occl_metric("11", left=10, top=10, tw=80, th=20)
    m = occl_measurement(
        box("11", 10, 10, 80, 20, z=9000),
        box("77", 0, 0, 200, 100, z=1000),          # карточка ПОД текстом
        shapes=(text,),
    )
    assert not [x for x in find_defects(m, PLAN, LIB, (0, 1)).defects if x.kind == OCCLUDED]


def test_a_transparent_shape_above_hides_nothing():
    """Вторая ловушка, найденная растром: текст накрыт сверху на 100% и
    прекрасно читается — накрывающая фигура прозрачна."""
    text = occl_metric("11", left=0, top=0, tw=100, th=20)
    m = occl_measurement(
        box("11", 0, 0, 100, 20, z=8000),
        box("88", 0, 0, 100, 20, z=9000, opacity=0.0),
        shapes=(text,),
    )
    assert not [x for x in find_defects(m, PLAN, LIB, (0, 1)).defects if x.kind == OCCLUDED]


def test_a_neighbouring_text_is_not_occlusion():
    """Третья ловушка: чужой текст рядом — это соседство, и разбирается оно
    переполнением, а не заслонением."""
    mine = occl_metric("11", left=0, top=0, tw=100, th=20)
    neighbour = occl_metric("12", left=50, top=0, tw=100, th=20)
    m = occl_measurement(
        box("11", 0, 0, 100, 20, z=8000),
        box("12", 50, 0, 100, 20, z=9000),
        shapes=(mine, neighbour),
    )
    assert not [x for x in find_defects(m, PLAN, LIB, (0, 1)).defects if x.kind == OCCLUDED]


def test_without_a_box_there_is_no_verdict():
    """«Проверить не смог» — не «чисто». Нет бокса у самой надписи — нет и
    суждения: иначе её `z` считался бы нулём и поверх неё оказалось бы всё
    подряд. Первая редакция так и дала 13 заслонений вместо одного."""
    text = occl_metric("11", left=0, top=0, tw=100, th=20)
    m = occl_measurement(
        box("99", 50, 0, 100, 20, z=9000, shape_type=13),   # бокса «11» нет
        shapes=(text,),
    )
    assert not [x for x in find_defects(m, PLAN, LIB, (0, 1)).defects if x.kind == OCCLUDED]


def test_occlusion_does_not_enter_the_repair_count():
    """Числа переполнений обязаны не шевельнуться: дефект неремонтируемый."""
    text = occl_metric("11", left=0, top=0, tw=100, th=20)
    m = occl_measurement(
        box("11", 0, 0, 100, 20, z=8000),
        box("99", 0, 0, 100, 20, z=9000, shape_type=13),
        shapes=(text,),
    )
    ins = find_defects(m, PLAN, LIB, (0, 1))
    assert len(ins.occluded) == 1
    assert not ins.repairable


# --- Z-53: заголовок, выросший на текст в своём же боксе -----------------
#
# Сдаточная колода WorkSpace, вариант 3, слайд 6, как её отдал зонд. Дизайнер
# поставил текст 712 в пустой низ бокса заголовка 709; наш заголовок
# переносится на две строки и ложится на него. Отчёт писал «18 → 0»
# (`WORKLOG/2026-09-23-z53-baseline.md`).


def ws_metric(shape_id: str, left: float, top: float, width: float, height: float,
              tw: float, th: float, size: float) -> ShapeMetric:
    return ShapeMetric(
        slide=1, shape_id=shape_id, width=width, height=height,
        text_width=tw, text_height=th,
        margin_left=0.0, margin_right=0.0, margin_top=0.0, margin_bottom=0.0,
        autofit=0, chars=40, font_size=size, left=left, top=top, anchor=1,
        bound_left=left, bound_top=top,
    )


WS_LIB = library(pattern("p06", (slot("s01", "709", "title"), slot("s02", "712"))))
WS_PLAN = deck(planned(5, "p06", "s01", "s02"))
WS_TITLE = ws_metric("709", 33.8, 33.44, 616.32, 91.93, 481.75, 77.76, 36.0)
WS_BODY = ws_metric("712", 33.96, 97.89, 285.83, 50.73, 262.25, 43.2, 12.0)


def ws_measurement(*shapes: ShapeMetric) -> Measurement:
    boxes = tuple(Box(slide=1, shape_id=m.shape_id, x=m.left, y=m.top, w=m.width,
                      h=m.height) for m in (WS_TITLE, WS_BODY))
    return Measurement(deck="d.pptx", status="ok", shapes=shapes, slides=1,
                       boxes=boxes, page=(960.0, 540.0))


def test_title_grown_onto_the_text_in_its_box_is_a_repairable_overflow():
    """Дефект один — у заголовка, который растёт, — и его берёт ремонт: это
    нехватка высоты, а не новый вид дефекта."""
    ins = find_defects(ws_measurement(WS_TITLE, WS_BODY), WS_PLAN, WS_LIB, (5,))
    (d,) = ins.defects
    assert d.kind == OVERFLOW_HEIGHT and d.repairable
    assert (d.slide_index, d.slot_id) == (5, "s01")
    assert 1.15 < d.ratio < 1.25, d.ratio          # 77.76 / 64.45


def test_an_empty_box_in_the_title_box_leaves_the_title_alone():
    """Пара к предыдущему: тот же бокс 712 без текста — слот не заполнили, и
    его текст стёрт (`PLAN-3.1`). Границей служит набранный текст, а не бокс:
    пустой бокс заголовку не мешает, и дефекта нет."""
    ins = find_defects(ws_measurement(WS_TITLE), WS_PLAN, WS_LIB, (5,))
    assert ins.defects == ()
