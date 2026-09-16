"""Ремонт плана шкалой кегля: монотонность, предел читаемости, честность.

PowerPoint не запускается. Проверяется то, что нашли логические проверки плана
(`PLAN-4.0`, шаг 4) и два дефекта, найденных при прогоне:

* движок отдаёт **видимый** кегль, а не номинальный — предел считается от
  восстановленного номинала, иначе съезжает с каждым раундом;
* присвоение предела не имеет права поднять шкалу обратно.
"""

from __future__ import annotations

from mimeo.analyze.deck import Rect
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
from mimeo.verify import find_defects, repair_plan
from mimeo.verify.metrics import Measurement, ShapeMetric


def make(scale: int | None = None, font: float = 44.0, text_height: float = 120.0):
    """Один слот, одна фигура: план, библиотека, замер."""
    slot = Slot(
        id="s01", role="body", content_type="text", rect=None,
        type_role=None, capacity=None, required=False, shape_id="11",
    )
    lib = PatternLibrary(
        source=PatternSource("t.pptx", "0" * 64),
        patterns=(
            Pattern(
                id="p01", kind="text", donor_part="/ppt/slides/slide1.xml", donor_index=0,
                slots=(slot,), members=(0,), cohesion=None, donor_reason="", source="slides",
            ),
        ),
    )
    plan = DeckPlan(
        source=PlanSource("c.md", "0" * 64, "0" * 64),
        slides=(
            PlannedSlide(
                index=0, pattern_id="p01",
                fills=(Fill(slot_id="s01", kind="text", text="…", font_scale=scale),),
                reason="",
            ),
        ),
    )
    metric = ShapeMetric(
        slide=1, shape_id="11", width=100.0, height=50.0,
        text_width=10.0, text_height=text_height,
        margin_left=0.0, margin_right=0.0, margin_top=0.0, margin_bottom=0.0,
        autofit=0, chars=100, font_size=font,
    )
    measurement = Measurement(deck="d.pptx", status="ok", shapes=(metric,), slides=1)
    return plan, lib, measurement


def run(scale=None, font=44.0, text_height=120.0, **kw):
    plan, lib, m = make(scale, font, text_height)
    ins = find_defects(m, plan, lib, (0,))
    return repair_plan(plan, ins, m, (0,), **kw)


def scale_of(result) -> int | None:
    return result.plan.slides[0].fills[0].font_scale


# --- расчёт шага --------------------------------------------------------


def test_scale_follows_the_square_root_rule():
    """Высота падает примерно как квадрат шкалы: k = 1/sqrt(отношение/цель)."""
    res = run(text_height=200.0)          # отношение x4 при высоте 50
    # 100 / sqrt(4 / 0.9) = 47
    assert scale_of(res) == 47


def test_aim_is_below_the_threshold_not_at_it():
    """Цель 0.9, а не 1.0: иначе слот подползает к порогу бесконечно."""
    at_one = run(text_height=200.0, target=1.0)
    with_margin = run(text_height=200.0, target=0.9)
    assert scale_of(with_margin) < scale_of(at_one)


def test_repeated_repair_starts_from_the_current_scale():
    res = run(scale=60, text_height=60.0)  # отношение x1.2
    assert scale_of(res) < 60


# --- монотонность -------------------------------------------------------


def test_scale_never_grows():
    """Слот с уже мелкой шкалой не должен получить увеличение."""
    res = run(scale=40, text_height=51.0)   # едва переполнен
    assert scale_of(res) <= 40


def test_floor_does_not_raise_the_scale_back():
    """Найдено прогоном: присвоение предела поднимало шкалу обратно к сотне.

    Кегль приходит видимый (12 pt при шкале 30% — это номинал 40), и наивный
    расчёт предела давал шкалу выше текущей.
    """
    res = run(scale=30, font=12.0, text_height=120.0, floor_pt=10.0)
    assert scale_of(res) <= 30


def test_fitting_slot_is_left_alone():
    res = run(text_height=40.0)
    assert scale_of(res) is None
    assert res.repairs == ()


# --- предел читаемости --------------------------------------------------


def test_small_font_hits_the_floor_and_stays_a_defect():
    """12 pt нельзя ужать до 58%: получится 7 pt, читать нечем."""
    res = run(font=12.0, text_height=200.0, floor_pt=10.0)
    (repair,) = res.repairs
    assert repair.at_floor
    assert len(res.unresolved) == 1
    assert scale_of(res) >= 83          # 10/12 -> не ниже 83%


def test_large_font_does_not_hit_the_floor():
    res = run(font=44.0, text_height=200.0, floor_pt=10.0)
    (repair,) = res.repairs
    assert not repair.at_floor
    assert res.unresolved == ()


def test_floored_slot_still_gets_its_scale():
    """Частичное улучшение лучше отказа — если о нём честно сказано."""
    res = run(font=12.0, text_height=200.0, floor_pt=10.0)
    assert scale_of(res) is not None and scale_of(res) < 100
    assert res.unresolved  # и дефект остался в отчёте


def test_unknown_font_size_is_treated_conservatively():
    """PowerPoint отдаёт -2 при разных размерах в фигуре: предел строже."""
    res = run(font=-2.0, text_height=200.0, floor_pt=10.0)
    (repair,) = res.repairs
    assert repair.at_floor


# --- остановка петли ----------------------------------------------------


def test_changed_is_false_when_nothing_moved():
    """Слот на пределе не меняется — петля обязана это увидеть и встать."""
    first = run(font=12.0, text_height=200.0, floor_pt=10.0)
    settled = scale_of(first)
    again = run(scale=settled, font=10.0, text_height=200.0, floor_pt=10.0)
    assert not again.changed


def test_failed_measurement_leaves_the_plan_untouched():
    plan, lib, _ = make()
    broken = Measurement(deck="d.pptx", status="open_failed", note="E_FAIL")
    ins = find_defects(broken, plan, lib, (0,))
    res = repair_plan(plan, ins, broken, (0,))
    assert res.plan is plan and res.repairs == () and not res.changed


# --- группы: ровный кегль у соседей (`PLAN-4.1`, `Z-22`) ----------------


def group_case(
    heights: tuple[float, ...],
    cys: tuple[int, ...],
    roles: tuple[str, ...] | None = None,
    scales: tuple[int | None, ...] | None = None,
    fonts: tuple[float, ...] | None = None,
    shapes: tuple[str, ...] | None = None,
):
    """Слайд из нескольких слотов: план, библиотека, замер.

    `heights` — высота набранного текста на каждый слот: ею и создаётся
    переполнение. `cys` — высота бокса, по ней слоты и группируются.
    """
    n = len(heights)
    roles = roles or ("body",) * n
    scales = scales or (None,) * n
    fonts = fonts or (44.0,) * n
    shapes = shapes or tuple(str(10 + i) for i in range(n))
    slots = tuple(
        Slot(
            id=f"s{i:02d}", role=roles[i], content_type="text",
            rect=Rect(x=0, y=i * 100000, cx=2000000, cy=cys[i]),
            type_role=None, capacity=None, required=False, shape_id=shapes[i],
        )
        for i in range(n)
    )
    lib = PatternLibrary(
        source=PatternSource("t.pptx", "0" * 64),
        patterns=(
            Pattern(
                id="p01", kind="text", donor_part="/ppt/slides/slide1.xml", donor_index=0,
                slots=slots, members=(0,), cohesion=None, donor_reason="", source="slides",
            ),
        ),
    )
    plan = DeckPlan(
        source=PlanSource("c.md", "0" * 64, "0" * 64),
        slides=(
            PlannedSlide(
                index=0, pattern_id="p01",
                fills=tuple(
                    Fill(slot_id=f"s{i:02d}", kind="text", text="…", font_scale=scales[i])
                    for i in range(n)
                ),
                reason="",
            ),
        ),
    )
    measurement = Measurement(
        deck="d.pptx", status="ok", slides=1,
        shapes=tuple(
            ShapeMetric(
                slide=1, shape_id=shapes[i], width=100.0, height=50.0,
                text_width=10.0, text_height=heights[i],
                margin_left=0.0, margin_right=0.0, margin_top=0.0, margin_bottom=0.0,
                autofit=0, chars=100, font_size=fonts[i],
            )
            for i in range(n)
        ),
    )
    return plan, lib, measurement


def group_run(**kw):
    floor_pt = kw.pop("floor_pt", None)
    plan, lib, m = group_case(**kw)
    ins = find_defects(m, plan, lib, (0,))
    extra = {"floor_pt": floor_pt} if floor_pt is not None else {}
    return repair_plan(plan, ins, m, (0,), lib, **extra)


def scales_of(result) -> list[int | None]:
    return [f.font_scale for f in result.plan.slides[0].fills]


def test_neighbours_of_one_group_get_the_same_scale():
    """Три слота одной роли и высоты: переполнены два, шкала у всех одна."""
    res = group_run(heights=(120.0, 90.0, 40.0), cys=(500000, 500000, 500000))
    assert len(set(scales_of(res))) == 1


def test_the_slot_without_a_defect_is_pulled_down_too():
    """Главный вклад в разнобой — сосед на 100%, до которого ремонт не дотягивался.

    Слот, у которого дефекта нет, обязан получить общую шкалу, а не остаться
    нетронутым (`WORKLOG/2026-09-13-group-scale.md`).
    """
    res = group_run(heights=(120.0, 10.0), cys=(500000, 500000))
    assert None not in scales_of(res)
    assert len(set(scales_of(res))) == 1


def test_different_height_is_a_different_group():
    """Блок и подпись под ним — не соседи, даже если роль одна.

    Замер: 20 групп из 29 распадаются, если учесть размер бокса.
    """
    res = group_run(heights=(120.0, 60.0), cys=(500000, 2500000))
    assert scales_of(res)[0] is not None
    assert len(set(scales_of(res))) == 2


def test_different_role_is_a_different_group():
    """Заголовок не обязан быть одного кегля с подписью под ним."""
    res = group_run(
        heights=(120.0, 60.0), cys=(500000, 500000), roles=("title", "body")
    )
    assert len(set(scales_of(res))) == 2


def test_the_group_scale_never_rises():
    """Сосед, ужатый в прошлом раунде, не получает увеличения обратно.

    Иначе слот переполнился бы снова и петля закачалась бы.
    """
    res = group_run(
        heights=(120.0, 10.0), cys=(500000, 500000), scales=(60, 60)
    )
    assert all(s is not None and s <= 60 for s in scales_of(res))


def test_the_group_stops_at_the_floor_of_its_tightest_member():
    """Минимум по группе не имеет права сделать соседа нечитаемым.

    Предел считается от номинала каждой фигуры: законный для 44 pt дал бы
    12-пунктовому соседу нечитаемое.
    """
    res = group_run(
        heights=(400.0, 400.0), cys=(500000, 500000), fonts=(44.0, 12.0), floor_pt=10.0
    )
    got = scales_of(res)
    assert len(set(got)) == 1
    assert got[0] >= 84          # 10 pt от 12 pt — не ниже 84%


def test_a_slot_missing_from_the_measurement_leaves_the_group():
    """Про кегль этой фигуры ничего не известно — догадка утянула бы соседей.

    Группа из двух слотов, один не дошёл до замера: остаётся послотный путь.
    """
    plan, lib, m = group_case(heights=(120.0, 90.0), cys=(500000, 500000))
    thinned = Measurement(
        deck=m.deck, status="ok", slides=1, shapes=(m.shapes[0],)
    )
    ins = find_defects(thinned, plan, lib, (0,))
    res = repair_plan(plan, ins, thinned, (0,), lib)
    assert scales_of(res)[1] is None


def test_without_the_library_the_old_path_is_kept():
    """Без раскладки групп не построить — ремонт обязан работать как раньше."""
    plan, lib, m = group_case(heights=(120.0, 10.0), cys=(500000, 500000))
    ins = find_defects(m, plan, lib, (0,))
    res = repair_plan(plan, ins, m, (0,))
    assert scales_of(res)[1] is None
