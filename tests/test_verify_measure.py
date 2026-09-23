"""Измеритель вёрстки: разбор ответа зонда и арифметика переполнения.

PowerPoint здесь не запускается. Тесты проверяют то, что можно проверить без
него: что вывод зонда разбирается верно, что молчание не превращается в «ноль
дефектов», что порядок детерминирован и что отношение считается по полезной
высоте, а не по габариту бокса.

План — `PLAN-4.0`, шаг 1.
"""

from __future__ import annotations

import pytest

from mimeo.verify import Measurement, MeasurerUnavailable, ShapeMetric, measure
from mimeo.verify.metrics import _collect


def metric(**kw) -> ShapeMetric:
    base = dict(
        slide=1, shape_id="1", width=100.0, height=50.0,
        text_width=80.0, text_height=40.0,
        margin_left=7.2, margin_right=7.2, margin_top=3.6, margin_bottom=3.6,
        autofit=0, chars=10,
    )
    base.update(kw)
    return ShapeMetric(**base)


# --- арифметика ---------------------------------------------------------


def test_usable_height_excludes_margins():
    m = metric(height=50.0, margin_top=3.6, margin_bottom=3.6)
    assert m.usable_height == pytest.approx(42.8)


def test_ratio_is_measured_against_usable_height_not_box():
    """Поля съедают 7.2 pt: текст в 42.8 влезает, в 43 — уже нет."""
    assert metric(text_height=42.8).height_ratio == pytest.approx(1.0)
    assert metric(text_height=85.6).height_ratio == pytest.approx(2.0)


def test_tolerance_forgives_rounding_but_not_real_overflow():
    """Габариты приходят в пунктах с двумя знаками: впритык — не дефект."""
    assert not metric(text_height=43.0).overflows()      # x1.005
    assert metric(text_height=60.0).overflows()          # x1.40


def test_zero_height_does_not_divide_by_zero():
    assert metric(height=0.0, margin_top=0.0, margin_bottom=0.0).usable_height == 1.0


# --- разбор ответа зонда ------------------------------------------------


PROBE_OUT = """version=16.0
deck=0
slides=2
shape slide=1 id=7 w=100 h=50 tw=80 th=120 ml=7.2 mr=7.2 mt=3.6 mb=3.6 fit=1 len=142
shape slide=2 id=3 w=200 h=80 tw=150 th=40 ml=7.2 mr=7.2 mt=3.6 mb=3.6 fit=2 len=17
status=ok
deck=1
status=open_failed
note=E_FAIL
result=OK
exit=clean
"""


def test_probe_output_is_parsed():
    first, second = _collect(PROBE_OUT, ["a.pptx", "b.pptx"])
    assert first.ok and first.slides == 2 and len(first.shapes) == 2
    assert first.shapes[0].text_height == 120.0
    assert first.shapes[0].autofit == 1
    assert first.shapes[1].chars == 17


def test_unopenable_deck_is_reported_not_silently_empty():
    """Самая опасная ошибка измерителя — пустой ответ вместо отказа.

    Файл, который не открылся, обязан выглядеть как отказ, иначе он неотличим
    от колоды без единого дефекта. Так уже было: `WORKLOG/2026-09-12-mce-prefixes.md`.
    """
    _, second = _collect(PROBE_OUT, ["a.pptx", "b.pptx"])
    assert second.status == "open_failed"
    assert not second.ok
    assert second.shapes == ()
    assert "E_FAIL" in second.note


def test_deck_the_probe_never_reached_is_not_measured():
    only_first = "deck=0\nstatus=ok\nresult=OK\n"
    first, second = _collect(only_first, ["a.pptx", "b.pptx"])
    assert first.ok
    assert second.status == "not_measured"


def test_busy_session_is_surfaced_on_every_deck():
    """Зонд отказывается работать, если PowerPoint уже открыт у пользователя."""
    for m in _collect("result=BUSY\n", ["a.pptx", "b.pptx"]):
        assert m.status == "not_measured"
        assert m.note == "BUSY"


def test_stuck_application_is_surfaced():
    out = PROBE_OUT.replace("exit=clean", "exit=stuck")
    first, _ = _collect(out, ["a.pptx", "b.pptx"])
    assert "не завершил" in first.note


def test_shapes_are_sorted_deterministically():
    """Обход фигур в COM идёт по стеку и порядок не гарантирует."""
    shuffled = (
        "deck=0\n"
        "shape slide=2 id=9 w=1 h=1 tw=1 th=1 ml=0 mr=0 mt=0 mb=0 fit=0 len=1\n"
        "shape slide=1 id=5 w=1 h=1 tw=1 th=1 ml=0 mr=0 mt=0 mb=0 fit=0 len=1\n"
        "shape slide=1 id=2 w=1 h=1 tw=1 th=1 ml=0 mr=0 mt=0 mb=0 fit=0 len=1\n"
        "status=ok\nresult=OK\n"
    )
    (only,) = _collect(shuffled, ["a.pptx"])
    assert [(s.slide, s.shape_id) for s in only.shapes] == [(1, "2"), (1, "5"), (2, "9")]


def test_text_corner_and_rotation_are_parsed():
    """Настоящий угол набранного текста и поворот — для мерки по чужому
    тексту (`Z-53`). Угол бокса плюс поле с ним совпадает только у якоря
    «верх»; здесь нарочно не совпадает."""
    out = (
        "deck=0\n"
        "shape slide=6 id=709 w=616.32 h=91.93 tw=481.75 th=77.76 ml=0 mr=0 "
        "mt=0 mb=0 fit=0 sz=36 len=40 x=33.8 y=33.44 anchor=3 "
        "bl=41.5 bt=40.25 rot=359.93\n"
        "status=ok\nresult=OK\n"
    )
    (only,) = _collect(out, ["a.pptx"])
    s = only.shapes[0]
    assert (s.bound_left, s.bound_top) == (41.5, 40.25)
    assert s.rotation == 359.93


def test_missing_text_corner_is_unknown_not_zero():
    """Старый зонд угла не отдаёт, а отказ COM даёт пустое значение. И то и
    другое — «не знаем»: ноль у координаты — это край слайда, и мерка приняла
    бы его за правду."""
    out = (
        "deck=0\n"
        "shape slide=1 id=7 w=100 h=50 tw=80 th=40 ml=0 mr=0 mt=0 mb=0 fit=0 len=5\n"
        "shape slide=1 id=8 w=100 h=50 tw=80 th=40 ml=0 mr=0 mt=0 mb=0 fit=0 len=5 "
        "bl= bt= rot=\n"
        "status=ok\nresult=OK\n"
    )
    (only,) = _collect(out, ["a.pptx"])
    for s in only.shapes:
        assert s.bound_left is None and s.bound_top is None
        assert s.rotation == 0.0


def test_skipped_shapes_are_counted_not_hidden():
    out = "deck=0\nskipped=E_FAIL\nstatus=ok\nresult=OK\n"
    (only,) = _collect(out, ["a.pptx"])
    assert only.skipped == ("E_FAIL",)


# --- поведение без измерителя -------------------------------------------


def test_empty_input_needs_no_powerpoint():
    assert measure([]) == ()


def test_missing_measurer_raises_instead_of_returning_nothing(monkeypatch):
    """Стадия должна выключаться осознанно, а не молча давать пустой результат."""
    monkeypatch.setattr("mimeo.verify.metrics.available", lambda: False)
    with pytest.raises(MeasurerUnavailable):
        measure(["deck.pptx"])


def test_measurement_defaults_are_safe():
    m = Measurement(deck="x.pptx", status="not_measured")
    assert not m.ok and m.shapes == () and m.skipped == ()
