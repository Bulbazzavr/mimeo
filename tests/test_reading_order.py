"""Порядок чтения: тезис стоит там, где зритель прочтёт его этим по счёту.

Задача `Z-31`, план `PLAN-7.6`. Дефект найден растром, а не тестом: колода
читалась задом наперёд при нулях по всем проверкам.

Механизмов было три, и здесь по проверке на каждый:

* порядок слотов в раскладке — сортировка полосой, а не точным `y`;
* обход блоков раздела в порядке документа, а не по видам;
* мера беспорядка, которой платит ранг.
"""

from __future__ import annotations

from mimeo.analyze.deck import Rect
from mimeo.analyze.patterns import _ROW_BAND, _shapes_of
from mimeo.model import Capacity, Pattern, Slot
from mimeo.plan.content import ContentBlock, ContentSection
from mimeo.plan.matching import match


class FakeShape:
    """Фигура ровно в том объёме, в каком её смотрит `_shapes_of`."""

    def __init__(self, shape_id, x, y, cx=1000000, cy=400000):
        self.shape_id = shape_id
        self.rect = Rect(x=x, y=y, cx=cx, cy=cy)
        self.hidden = False
        self.ph_type = None
        self.has_text = True
        self.has_chart = False
        self.has_table = False
        self.runs = ()
        self.text = "текст"
        self.para_count = 1
        self.kind = "sp"
        self.fill_kind = None


class FakeSlide:
    def __init__(self, shapes):
        self.shapes = shapes


def slot(sid, x, y, cap, role="body"):
    return Slot(
        id=sid, role=role, content_type="text",
        rect=Rect(x=x, y=y, cx=2000000, cy=500000),
        type_role=None,
        capacity=Capacity(max_chars=cap, max_lines=3, chars_per_line=cap // 3 or 1,
                          target_chars=cap, max_items=None, donor_chars=None, basis="test"),
        required=False, shape_id=sid,
    )


def pattern(slots, kind="cards"):
    return Pattern(
        id="p01", kind=kind, donor_part="/ppt/slides/slide1.xml", donor_index=0,
        slots=tuple(slots), members=(0,), cohesion=None, donor_reason="", source="slides",
    )


# --- механизм 1: строка карточек не должна рассыпаться -------------------


def test_one_visual_row_sorts_left_to_right_despite_jitter():
    """Соседи по строке отличаются по высоте на десяток тысяч EMU.

    Замер: точный `y` и полоса дают разный порядок на 90 слайдах из 209, и
    вот почему — четыре карточки одной строки шли 7.98 → 4.23 → 4.12 → 8.10
    млн по `x` (`WORKLOG/2026-09-20-z31-baseline.md`, замер 3).
    """
    jitter = [
        FakeShape("a", x=7980891, y=1262987),
        FakeShape("b", x=4233289, y=1275702),
        FakeShape("c", x=4118670, y=1283427),
        FakeShape("d", x=8102073, y=1283428),
    ]
    got = _shapes_of(FakeSlide(jitter), cx=12192000, cy=6858000)
    assert [s.shape_id for s in got] == ["c", "b", "a", "d"], [s.shape_id for s in got]


def test_rows_further_apart_than_the_band_stay_separate():
    """Полоса не должна склеивать настоящие строки: иначе порядок сломается
    в другую сторону."""
    two_rows = [
        FakeShape("low", x=100, y=_ROW_BAND * 4),
        FakeShape("high", x=9000000, y=0),
    ]
    got = _shapes_of(FakeSlide(two_rows), cx=12192000, cy=6858000)
    assert [s.shape_id for s in got] == ["high", "low"]


# --- механизм 2: блоки в порядке документа -------------------------------


def test_paragraph_before_a_list_takes_the_earlier_slot():
    """Раздел «В чём вообще беда»: абзац в тексте первый, список второй.

    До правки `match` шёл по видам — сначала все списки, потом абзацы, — и
    абзац уезжал на третью карточку."""
    section = ContentSection(
        id="sec01",
        heading=None,
        blocks=(
            ContentBlock(id="b01", kind="paragraph", text="Первым идёт абзац, он длинный"),
            ContentBlock(id="b02", kind="list",
                         items=("Вторым пунктом", "Третьим пунктом"),
                         text="Вторым пунктом Третьим пунктом"),
        ),
    )
    p = pattern([slot("s01", 0, 0, 200), slot("s02", 3000000, 0, 200),
                 slot("s03", 6000000, 0, 200)])
    m = match(section, p)
    order = [(f.slot_id, f.text) for f in m.fills]
    assert order[0][0] == "s01" and "абзац" in order[0][1], order
    assert order[1][0] == "s02" and order[2][0] == "s03", order
    assert m.disorder == 0.0


def test_metric_still_claims_its_own_plate_and_does_not_join_the_queue():
    """Ловушка к правке порядка: метрики и картинки обязаны остаться раньше.

    Пущенная в общую очередь метрика заняла бы обычный текстовый слот, а
    плашка под число осталась бы пустой (`PLAN-2.0`)."""
    section = ContentSection(
        id="sec01",
        heading=None,
        blocks=(
            ContentBlock(id="b01", kind="paragraph", text="Абзац идёт в тексте первым"),
            ContentBlock(id="b02", kind="metric", value="0.82", label="Точность",
                         text="0.82"),
        ),
    )
    plate = slot("s01", 0, 0, 8, role="metric_value")
    p = pattern([plate, slot("s02", 3000000, 0, 200), slot("s03", 6000000, 0, 200)])
    m = match(section, p)
    by_slot = {f.slot_id: f for f in m.fills}
    assert "s01" in by_slot and by_slot["s01"].text == "0.82", m.fills
    assert by_slot["s01"].kind == "number"


# --- механизм 3: мера беспорядка ----------------------------------------


def test_disorder_is_zero_when_theses_follow_reading_order():
    section = ContentSection(
        id="sec01", heading=None,
        blocks=(ContentBlock(id="b01", kind="list",
                             items=("раз", "два", "три"), text="раз два три"),),
    )
    p = pattern([slot("s01", 0, 0, 40), slot("s02", 3000000, 0, 40),
                 slot("s03", 6000000, 0, 40)])
    assert match(section, p).disorder == 0.0


def test_disorder_grows_when_capacity_pushes_a_thesis_forward():
    """Ёмкости по чтению идут 10, 10, 400 — длинный тезис перепрыгивает вперёд,
    короткие садятся за ним. Так было на `p09` шаблона VK Tech, пока подсказку
    «Вставить фото» считали слотом. Короткие тезисы — не длиннее ёмкости: с
    `Z-55` мелкое место принимает текст не длиннее себя."""
    section = ContentSection(
        id="sec01", heading=None,
        blocks=(ContentBlock(id="b01", kind="list",
                             items=("это очень длинный тезис на много знаков подряд",
                                    "коротко", "кратко"),
                             text="…"),),
    )
    p = pattern([slot("s01", 0, 0, 10), slot("s02", 3000000, 0, 10),
                 slot("s03", 6000000, 0, 400)])
    m = match(section, p)
    assert m.disorder > 0.0, [f.slot_id for f in m.fills]


def test_the_ordered_layout_outranks_the_jumbled_one():
    """Ради чего вся мера: ранг обязан предпочесть раскладку, где порядок цел.

    Назначение при этом не меняется — полнота важнее порядка, — меняется
    только выбор раскладки."""
    section = ContentSection(
        id="sec01", heading=None,
        blocks=(ContentBlock(id="b01", kind="list",
                             items=("это очень длинный тезис на много знаков подряд",
                                    "коротко", "кратко"),
                             text="…"),),
    )
    jumbled = pattern([slot("s01", 0, 0, 10), slot("s02", 3000000, 0, 10),
                       slot("s03", 6000000, 0, 400)])
    ordered = pattern([slot("s01", 0, 0, 400), slot("s02", 3000000, 0, 400),
                       slot("s03", 6000000, 0, 400)])
    a, b = match(section, jumbled), match(section, ordered)
    assert b.score > a.score, (a.score, a.disorder, b.score, b.disorder)
