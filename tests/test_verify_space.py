"""Доступное место вместо бокса (`PLAN-4.2`, `ADR-0015`).

PowerPoint не запускается: считается геометрия.

Главное, что здесь проверяется, — мерка не должна снимать дефект там, где тексту
действительно некуда деться. Ошибиться в эту сторону хуже, чем в обратную:
призрак ужимает текст зря, а пропущенное наложение остаётся в готовом файле.
"""

from __future__ import annotations

import dataclasses

import pytest

from mimeo.verify.metrics import Box, Measurement, ShapeMetric
from mimeo.verify.space import available_height, height_ratio, text_room

PAGE = (720.0, 540.0)          # слайд 10 x 7.5 дюйма в пунктах


def shape(top=100.0, height=50.0, text=120.0, anchor=1, left=100.0, width=200.0):
    """Фигура, чей текст выше бокса: 120 против полезных 50."""
    return ShapeMetric(
        slide=1, shape_id="11", width=width, height=height,
        text_width=10.0, text_height=text,
        margin_left=0.0, margin_right=0.0, margin_top=0.0, margin_bottom=0.0,
        autofit=0, chars=100, font_size=24.0,
        left=left, top=top, anchor=anchor,
    )


def scene(metric, *boxes, page=PAGE):
    """Замер: сама фигура плюс перечисленные соседи."""
    own = Box(slide=1, shape_id=metric.shape_id, x=metric.left, y=metric.top,
              w=metric.width, h=metric.height)
    return Measurement(
        deck="d.pptx", status="ok", shapes=(metric,), slides=1,
        boxes=(own,) + boxes, page=page,
    )


def neighbour(y, h=30.0, x=100.0, w=200.0, visible=True, shape_id="22"):
    return Box(slide=1, shape_id=shape_id, x=x, y=y, w=w, h=h, visible=visible)


# --- мерка снимает призраки -------------------------------------------


def test_text_spilling_into_empty_space_is_not_a_defect():
    """Под фигурой пусто до низа слайда — зритель брака не видит."""
    m = shape()
    assert height_ratio(m, scene(m)) <= 1.0
    assert m.height_ratio > 1.0, "по старой мерке это был дефект"


def test_free_space_is_bounded_by_the_slide_edge():
    """Фигура у самого низа: место кончается краем слайда, а не бесконечно."""
    m = shape(top=480.0, height=50.0, text=120.0)
    assert height_ratio(m, scene(m)) > 1.0


# --- и не снимает настоящие -------------------------------------------


def test_a_neighbour_below_keeps_the_defect():
    m = shape()
    assert height_ratio(m, scene(m, neighbour(y=160.0))) > 1.0


def test_a_neighbour_just_below_leaves_no_room_at_all():
    """Сосед вплотную: доступное место равно полезной высоте бокса."""
    m = shape()
    assert available_height(m, scene(m, neighbour(y=150.0))) == m.usable_height


def test_bottom_anchored_text_looks_upwards():
    """Текст прижат к низу — растёт вверх, и мешает сосед сверху, а не снизу."""
    m = shape(top=300.0, anchor=4)
    above = neighbour(y=0.0, h=290.0)
    assert height_ratio(m, scene(m, above)) > 1.0, "сосед сверху обязан мешать"
    below = neighbour(y=360.0)
    assert height_ratio(m, scene(m, below)) <= 1.0, "сосед снизу тексту не мешает"


def test_centred_text_needs_room_on_both_sides():
    """Центральный якорь: место ищется и сверху, и снизу."""
    m = shape(top=200.0, anchor=3)
    crowded = scene(m, neighbour(y=190.0, h=10.0), neighbour(y=250.0, shape_id="23"))
    assert height_ratio(m, crowded) > 1.0


def test_centred_text_grows_both_ways_equally():
    """Середина растёт вниз на половину прироста: пустое место сверху не
    выручает, если снизу его мало. Сумма сторон (до `Z-54`) давала 50 + 150 +
    20 = 220 пт и молчала; заголовок WorkSpace так лёг на плашку под собой
    (растр девятки 27 сентября)."""
    m = shape(top=150.0, anchor=3)
    below = scene(m, neighbour(y=220.0))
    assert available_height(m, below) == 90.0      # 50 бокса + 2 × 20 снизу
    assert height_ratio(m, below) > 1.0


# --- что помехой не считается -----------------------------------------


def test_a_backdrop_is_not_a_boundary():
    """Фигура, целиком содержащая бокс, — подложка. Правило беспороговое."""
    m = shape()
    backdrop = neighbour(y=0.0, h=540.0, x=0.0, w=720.0)
    assert height_ratio(m, scene(m, backdrop)) <= 1.0


def test_an_invisible_shape_does_not_take_up_room():
    """Клон несёт скрытый мусор донора; зрителю он не мешает."""
    m = shape()
    assert height_ratio(m, scene(m, neighbour(y=160.0, visible=False))) <= 1.0


def test_a_shape_beside_the_text_does_not_take_up_room():
    """Текст растёт вертикально и вбок не заглядывает."""
    m = shape()
    aside = neighbour(y=160.0, x=400.0, w=100.0)
    assert height_ratio(m, scene(m, aside)) <= 1.0


# --- безопасность мерки ------------------------------------------------


def test_available_space_is_never_smaller_than_the_box():
    """Свойство, на котором стояла безопасность `ADR-0015`: соседи **не
    текст** и **не подложка** места у бокса не отнимают.

    Шире оно не верно, и это намеренно: низ подложки (`Z-38`) и чужой набранный
    текст в нашем боксе (`Z-53`) место сужают, и цена каждого измерена. Здесь в
    замере нет ни того ни другого."""
    for anchor in (1, 2, 3, 4, 5):
        for top in (0.0, 100.0, 480.0):
            m = shape(top=top, anchor=anchor)
            for extra in ([], [neighbour(y=top + 50.0)], [neighbour(y=0.0, h=top or 1.0)]):
                got = available_height(m, scene(m, *extra))
                assert got >= m.usable_height - 1e-9


def test_without_boxes_the_old_measure_is_kept():
    """Зонд не дал окружения — откатываемся к боксу, а не объявляем место
    свободным. Тихое снятие дефектов хуже прежнего поведения."""
    m = shape()
    bare = Measurement(deck="d.pptx", status="ok", shapes=(m,), slides=1)
    assert available_height(m, bare) == m.usable_height
    assert height_ratio(m, bare) == m.height_ratio


def test_without_page_size_the_old_measure_is_kept():
    m = shape()
    no_page = Measurement(deck="d.pptx", status="ok", shapes=(m,), slides=1,
                          boxes=(Box(slide=1, shape_id="11", x=100.0, y=100.0,
                                     w=200.0, h=50.0),), page=None)
    assert available_height(m, no_page) == m.usable_height


def test_shapes_from_another_slide_are_not_neighbours():
    m = shape()
    other = Box(slide=2, shape_id="99", x=100.0, y=160.0, w=200.0, h=30.0)
    assert height_ratio(m, scene(m, other)) <= 1.0


# --- подложка: её низ тоже граница (`Z-38`) ----------------------------


def backing(top, height, x=90.0, w=220.0, shape_id="33", visible=True):
    """Плашка, внутри которой лежит текстовый бокс."""
    return Box(slide=1, shape_id=shape_id, x=x, y=top, w=w, h=height, visible=visible)


def test_text_may_not_grow_past_the_bottom_of_its_backing():
    """Главный случай задачи.

    Бокс 100..150 лежит в плашке 60..170. Ближайшая соседняя фигура далеко
    внизу, но плашка кончается на 170 — дальше текст выходит из карточки, и
    зритель это видит. Замер на VK Tech: текст 4.10 дюйма, карточка 4.02.
    """
    m = shape(top=100.0, height=50.0, text=120.0)
    scn = scene(m, backing(60.0, 110.0), neighbour(400.0))
    assert available_height(m, scn) == 70.0        # 50 бокса + 20 до дна плашки
    assert height_ratio(m, scn) > 1.0


def test_without_the_backing_the_same_text_fits():
    """Та же фигура без плашки: места до соседа хватает, дефекта нет.

    Пара нужна, чтобы проверка не оказалась зелёной по любой причине.
    """
    m = shape(top=100.0, height=50.0, text=120.0)
    scn = scene(m, neighbour(400.0))
    assert available_height(m, scn) > 120.0
    assert height_ratio(m, scn) < 1.0


def test_a_narrow_icon_overlapping_the_edge_is_not_a_backing():
    """Узкая иконка, задевшая бокс краем, подложкой не является.

    Иначе она обрезала бы место по своему низу, и мы получили бы ложный
    дефект — `PLAN-6.1`, логическая проверка 1, дыра 1.
    """
    m = shape(top=100.0, height=50.0, text=120.0, left=100.0, width=200.0)
    icon = backing(60.0, 110.0, x=280.0, w=40.0)   # перекрывает 20 из 200
    scn = scene(m, icon, neighbour(400.0))
    assert height_ratio(m, scn) < 1.0


def test_the_nearest_backing_wins():
    """Плашек может быть несколько, вложенных: карточка внутри секции."""
    m = shape(top=100.0, height=50.0, text=200.0)
    scn = scene(m, backing(50.0, 300.0, shape_id="outer"),
                backing(60.0, 110.0, shape_id="inner"), neighbour(500.0))
    assert available_height(m, scn) == 70.0        # по внутренней, не по внешней


def test_a_hidden_backing_does_not_constrain():
    """Невидимая фигура зрителю не мешает — и границей быть не может."""
    m = shape(top=100.0, height=50.0, text=120.0)
    scn = scene(m, backing(60.0, 110.0, visible=False), neighbour(400.0))
    assert height_ratio(m, scn) < 1.0


def test_bottom_anchored_text_is_bounded_by_the_backing_top():
    """Зеркальный случай: текст растёт вверх, границей служит верх плашки."""
    m = shape(top=300.0, height=50.0, text=120.0, anchor=4)
    scn = scene(m, backing(280.0, 100.0), neighbour(0.0, h=10.0))
    assert available_height(m, scn) == 70.0        # 50 бокса + 20 до верха плашки


# --- чужой набранный текст в нашем боксе (`Z-53`, `PLAN-7.12`) -----------
#
# Геометрия — сдаточная колода WorkSpace, вариант 3, слайд 6, как её отдал зонд
# (`WORKLOG/2026-09-23-z53-baseline.md`, § 4). Заголовок 709 переносится на две
# строки, а текст 712 дизайнер поставил в пустой низ его бокса. Отчёт писал
# «18 → 0»: мерка искала помехи только за пределами бокса.

WS_PAGE = (960.0, 540.0)


def text(shape_id, left, top, width, height, tw, th, size, bl=None, bt=None,
         anchor=1, rotation=0.0):
    """Надпись с настоящим углом набранного текста — по умолчанию угол бокса."""
    return ShapeMetric(
        slide=1, shape_id=shape_id, width=width, height=height,
        text_width=tw, text_height=th,
        margin_left=0.0, margin_right=0.0, margin_top=0.0, margin_bottom=0.0,
        autofit=0, chars=40, font_size=size, left=left, top=top, anchor=anchor,
        bound_left=left if bl is None else bl, bound_top=top if bt is None else bt,
        rotation=rotation,
    )


def title(**kw):
    """709: бокс y 33.44…125.37, текст в две строки 77.76 пт, 36 пт."""
    base = dict(shape_id="709", left=33.8, top=33.44, width=616.32, height=91.93,
                tw=481.75, th=77.76, size=36.0)
    base.update(kw)
    return text(**base)


def body(**kw):
    """712: начинается на y 97.89 — внутри бокса заголовка."""
    base = dict(shape_id="712", left=33.96, top=97.89, width=285.83, height=50.73,
                tw=262.25, th=43.2, size=12.0)
    base.update(kw)
    return text(**base)


def slide(*texts, hidden=(), extra=()):
    """Замер: надписи, их боксы и дальний сосед снизу — 711 из того же слайда."""
    boxes = tuple(
        Box(slide=t.slide, shape_id=t.shape_id, x=t.left, y=t.top, w=t.width,
            h=t.height, visible=t.shape_id not in hidden)
        for t in texts
    ) + (Box(slide=1, shape_id="711", x=497.39, y=283.32, w=373.16, h=203.92),) + extra
    return Measurement(deck="d.pptx", status="ok", shapes=texts, slides=1,
                       boxes=boxes, page=WS_PAGE)


def test_text_standing_inside_our_box_ends_our_room():
    """Главный случай задачи. Без правила место — до 711 внизу, 249.9 пт, и
    заголовок «влезает» с отношением 0.31; до текста 712 — 64.45 пт."""
    t = title()
    alone = slide(t)
    assert round(available_height(t, alone), 1) == 249.9
    assert height_ratio(t, alone) < 0.35, "так мерка видела слайд до правки"

    both = slide(t, body())
    assert available_height(t, both) == pytest.approx(97.89 - 33.44)
    assert height_ratio(t, both) > 1.2, "заголовок наезжает — это дефект"


def test_the_text_we_run_into_is_not_blamed():
    """Пара видна с обеих сторон, дефект — один: у того, кто растёт. Для 712
    заголовок начинается выше его начала, это не сторона роста."""
    b = body()
    assert text_room(b, slide(title(), b)) is None


def test_a_neighbour_that_grew_out_of_its_box_is_not_our_boundary():
    """R1'. WorkSpace, слайд 12: подпись с якорем «середина» выросла из бокса
    вверх и наехала на текст над ней. Растёт она — её и ужимать; прототип без
    этого условия уронил шкалу невиновного с 91 % до 63 %."""
    t = title()
    grown = body(top=110.0, bt=97.89)          # текст выше верха своего бокса
    assert text_room(t, slide(t, grown)) is None
    in_place = body(top=97.89, bt=97.89)
    assert text_room(t, slide(t, in_place)) == pytest.approx(64.45)


def test_a_neighbour_beside_our_text_is_not_a_boundary():
    """Набранный текст заголовка кончается на x 515.55. Сосед правее — в боксе,
    но не под текстом, и расти туда нашему тексту незачем."""
    t = title()
    aside = body(left=530.0)
    assert text_room(t, slide(t, aside)) is None
    under = body(left=500.0)                     # 15 пт под текстом
    assert text_room(t, slide(t, under)) is not None


def test_a_neighbour_within_our_first_line_is_left_alone():
    """Наложение, где никто не растёт. Звёздочка сноски VK Tech, слайд 53:
    соседний текст начинается на 2.25 пт ниже её, кегль 11.69. Ужатие этого
    не снимет — первая строка всегда начинается с верха, — а ремонт загнал бы
    текст в предел читаемости."""
    star = text("2654", left=100.0, top=286.11, width=20.0, height=16.0,
                tw=10.0, th=16.84, size=11.69)
    line = text("2662", left=95.0, top=288.36, width=300.0, height=20.0,
                tw=280.0, th=14.4, size=12.0)
    assert text_room(star, slide(star, line)) is None


def test_an_invisible_neighbour_is_not_a_boundary():
    t = title()
    assert text_room(t, slide(t, body(), hidden=("712",))) is None


def test_text_on_another_slide_is_not_a_neighbour():
    """`id` фигур повторяются от слайда к слайду. Сосед видим на своём слайде —
    и всё равно не наш: первая редакция теста клала его бокс на наш слайд, и
    держала его проверка видимости, а не слайда (мутация выжила)."""
    t = title()
    other = dataclasses.replace(body(), slide=2)
    assert text_room(t, slide(t, other)) is None


def test_the_rule_waits_for_the_anchor_fix():
    """Только якорь «верх»: номера 3 и 4 этот модуль читает не так, как их
    отдаёт COM (`Z-54`). Правило для середины и низа — вместе с правкой."""
    for anchor in (2, 3, 4, 5):
        t = title(anchor=anchor)
        assert text_room(t, slide(t, body())) is None, anchor


def test_our_rotated_text_is_left_alone():
    """У повёрнутой надписи «вниз» — не вниз слайда. Почти ноль — ровно:
    зонд отдаёт 359.93 у фигур, которые на растре стоят прямо."""
    tilted = title(rotation=8.21)
    assert text_room(tilted, slide(tilted, body())) is None
    level = title(rotation=359.93)
    assert text_room(level, slide(level, body())) == pytest.approx(64.45)


def test_without_text_corners_nothing_changes():
    """Старый зонд угла не отдаёт. Не зная, где текст, нельзя сказать, на чём
    он стоит, — мерка ведёт себя ровно как до правки."""
    t = dataclasses.replace(title(), bound_left=None, bound_top=None)
    assert text_room(t, slide(t, body())) is None
    b = dataclasses.replace(body(), bound_left=None, bound_top=None)
    assert text_room(title(), slide(title(), b)) is None


def test_the_nearest_text_wins():
    t = title()
    near = body(shape_id="712", top=90.0)
    far = body(shape_id="713", top=110.0)
    assert text_room(t, slide(t, far, near)) == pytest.approx(90.0 - 33.44)


def test_the_room_only_ever_narrows():
    """Правило сужает место и никогда не расширяет: всё, что мерка видела до
    `Z-53`, она видит и теперь."""
    for top in (40.0, 70.0, 97.89, 124.0, 200.0, 400.0):
        for left in (0.0, 33.96, 300.0, 600.0):
            t = title()
            before = available_height(t, slide(t))
            after = available_height(t, slide(t, body(top=top, left=left)))
            assert after <= before + 1e-9, (top, left)
