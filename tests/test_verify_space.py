"""Доступное место вместо бокса (`PLAN-4.2`, `ADR-0015`).

PowerPoint не запускается: считается геометрия.

Главное, что здесь проверяется, — мерка не должна снимать дефект там, где тексту
действительно некуда деться. Ошибиться в эту сторону хуже, чем в обратную:
призрак ужимает текст зря, а пропущенное наложение остаётся в готовом файле.
"""

from __future__ import annotations

from mimeo.verify.metrics import Box, Measurement, ShapeMetric
from mimeo.verify.space import available_height, height_ratio

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
    m = shape(top=300.0, anchor=3)
    above = neighbour(y=0.0, h=290.0)
    assert height_ratio(m, scene(m, above)) > 1.0, "сосед сверху обязан мешать"
    below = neighbour(y=360.0)
    assert height_ratio(m, scene(m, below)) <= 1.0, "сосед снизу тексту не мешает"


def test_centred_text_needs_room_on_both_sides():
    """Центральный якорь: место ищется и сверху, и снизу."""
    m = shape(top=200.0, anchor=2)
    crowded = scene(m, neighbour(y=190.0, h=10.0), neighbour(y=250.0, shape_id="23"))
    assert height_ratio(m, crowded) > 1.0


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
    """Свойство, на котором стоит безопасность правки: новых срабатываний
    не появится по построению."""
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
    m = shape(top=300.0, height=50.0, text=120.0, anchor=3)
    scn = scene(m, backing(280.0, 100.0), neighbour(0.0, h=10.0))
    assert available_height(m, scn) == 70.0        # 50 бокса + 20 до верха плашки
