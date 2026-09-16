"""Петля стадии VERIFY: исходы, честность отчёта и лишний сеанс.

PowerPoint не запускается. Всё интересное в петле — управляющая логика, и
измеритель со сборщиком подменяются швами из сигнатуры `verify_deck`.

Проверяется то, что нашли логические проверки плана (`PLAN-4.0`, шаги 6–9):

* «раунды кончились» отличается от «упёрлись в предел» — первое «сдался»,
  второе «сделал всё, что мог»;
* `after` никогда не берётся из осмотра, сделанного до пересборки;
* контрольный замер не запускается, если колоду не пересобирали;
* отказ измерителя даёт `None`, а не ноль (`Z-20`).
"""

from __future__ import annotations

from mimeo.analyze.deck import Rect
from mimeo.compose.builder import BuildReport
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
from mimeo.verify.loop import (
    STOP_FITS,
    STOP_FLOOR,
    STOP_NOT_MEASURED,
    STOP_ROUNDS,
    STOP_UNAVAILABLE,
    verify_deck,
)
from mimeo.verify.metrics import MeasurerUnavailable, Measurement, ShapeMetric

BOX_HEIGHT = 50.0          # полезная высота бокса, пункты


def scene(font: float = 44.0):
    """План из одного слота, библиотека и первый отчёт сборки."""
    slot = Slot(
        id="s01", role="body", content_type="text",
        rect=Rect(x=0, y=0, cx=2000000, cy=500000),
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
                fills=(Fill(slot_id="s01", kind="text", text="…"),),
                reason="",
            ),
        ),
    )
    report = BuildReport(
        output="out/deck.pptx", slides=1, substituted=1, inherited_shapes=0,
        cleared_slots=0, warnings=(), slides_written=(0,),
    )
    return plan, lib, report, font


class Measurer:
    """Измеритель по сценарию: на каждый вызов — своя высота текста.

    Считает вызовы: лишний контрольный сеанс иначе не поймать.
    """

    def __init__(self, heights, font=44.0, fail_at=None, raise_at=None):
        self.heights = list(heights)
        self.font = font
        self.fail_at = fail_at
        self.raise_at = raise_at
        self.calls = 0

    def __call__(self, decks):
        self.calls += 1
        if self.raise_at == self.calls:
            raise MeasurerUnavailable("нечем мерить")
        if self.fail_at == self.calls:
            return (Measurement(deck=decks[0], status="open_failed", note="E_FAIL"),)
        height = self.heights[min(self.calls, len(self.heights)) - 1]
        metric = ShapeMetric(
            slide=1, shape_id="11", width=100.0, height=BOX_HEIGHT,
            text_width=10.0, text_height=height,
            margin_left=0.0, margin_right=0.0, margin_top=0.0, margin_bottom=0.0,
            autofit=0, chars=100, font_size=self.font,
        )
        return (Measurement(deck=decks[0], status="ok", shapes=(metric,), slides=1),)


class Builder:
    """Сборщик-пустышка. Считает пересборки."""

    def __init__(self, report):
        self.report = report
        self.calls = 0

    def __call__(self, template, plan, library, output):
        self.calls += 1
        return self.report


def run(heights, rounds=3, font=44.0, **kw):
    plan, lib, report, _ = scene()
    m = Measurer(heights, font=font, **kw)
    b = Builder(report)
    outcome = verify_deck("t.pptx", plan, lib, "out/deck.pptx", report,
                          rounds=rounds, measurer=m, builder=b)
    return outcome.report, m, b


# --- исходы петли -------------------------------------------------------


def test_deck_that_already_fits_stops_at_once():
    rep, m, b = run([40.0])
    assert rep.stopped == STOP_FITS
    assert rep.before == 0 and rep.after == 0
    assert b.calls == 0, "нечего чинить — пересобирать незачем"


def test_repaired_deck_reports_what_it_fixed():
    """Первый раунд переполнен, после пересборки влезло."""
    rep, m, b = run([120.0, 40.0])
    assert rep.stopped == STOP_FITS
    assert rep.before == 1 and rep.after == 0
    assert rep.fixed == 1
    assert b.calls == 1


def test_floor_is_not_the_same_as_giving_up():
    """Кегль уже на пределе: ужимать некуда, но это «сделал всё», а не «сдался»."""
    rep, m, b = run([120.0], font=10.0)
    assert rep.stopped == STOP_FLOOR
    assert rep.after == 1 and b.calls == 0


def test_running_out_of_rounds_says_so():
    """Дефект не уходит: два раунда кончились, и отчёт обязан это назвать."""
    rep, m, b = run([120.0] * 6, rounds=2)
    assert rep.stopped == STOP_ROUNDS
    assert rep.before == 1 and rep.after == 1


def test_rounds_that_ran_out_exactly_when_it_fitted_is_success():
    """Последняя пересборка всё вылечила — это не отказ.

    Без поправки честный успех отчитался бы как «сдался».
    """
    rep, m, b = run([120.0, 120.0, 40.0], rounds=2)
    assert rep.stopped == STOP_FITS
    assert rep.after == 0


# --- честность отчёта ---------------------------------------------------


def test_failed_measurement_is_not_zero_defects():
    """Отказ на первом же замере: ноль здесь читался бы как «всё хорошо» (Z-20)."""
    rep, m, b = run([40.0], fail_at=1)
    assert rep.status == "not_measured"
    assert rep.stopped == STOP_NOT_MEASURED
    assert rep.before is None and rep.after is None
    assert rep.fixed is None


def test_failure_after_a_repair_does_not_keep_the_stale_number():
    """Замер отказал после пересборки — «стало» неизвестно, а не «как было».

    Инвариант: `after` не берётся из осмотра, сделанного до пересборки.
    """
    rep, m, b = run([120.0], fail_at=2)
    assert rep.status == "not_measured"
    assert rep.before == 1
    assert rep.after is None, "осмотр устарел — числу неоткуда взяться"
    assert b.calls == 1


def test_measurer_that_refuses_does_not_break_the_build():
    rep, m, b = run([40.0], raise_at=1)
    assert rep.status == "unavailable"
    assert rep.stopped == STOP_UNAVAILABLE
    assert rep.before is None and rep.after is None
    assert rep.note


def test_unresolved_defects_are_listed_not_just_counted():
    rep, m, b = run([120.0], font=10.0)
    assert len(rep.unresolved) == rep.after
    assert rep.unresolved[0].slot_id == "s01"


# --- лишний сеанс -------------------------------------------------------


def test_no_control_measurement_when_nothing_was_rebuilt():
    """Петля встала без пересборки — контрольный замер померил бы тот же файл.

    Сеанс PowerPoint стоит ~4 с из 24 (`WORKLOG/2026-09-13-verify-loop.md`).
    """
    rep, m, b = run([40.0])
    assert m.calls == 1, "лишний сеанс"

    rep, m, b = run([120.0], font=10.0)          # встала по пределу
    assert m.calls == 1, "лишний сеанс после остановки по пределу"


def test_control_measurement_happens_when_the_deck_was_rebuilt():
    """Раунды кончились сразу после пересборки — числа обязаны быть свежими."""
    rep, m, b = run([120.0] * 6, rounds=2)
    assert b.calls == 2
    assert m.calls == 3, "два раунда плюс контрольный замер"


# --- отчёт как данные ---------------------------------------------------


def test_report_json_carries_status_and_stop_reason():
    rep, _, _ = run([120.0, 40.0])
    js = rep.to_json()
    assert js["status"] == "ok"
    assert js["stopped"] == STOP_FITS
    assert js["defects"] == {"before": 1, "after": 0}
    assert js["rounds"][0]["rebuilt"] is True


def test_report_json_keeps_nulls_when_nothing_was_measured():
    rep, _, _ = run([40.0], fail_at=1)
    js = rep.to_json()
    assert js["defects"]["before"] is None
    assert js["defects"]["after"] is None
    assert js["status"] == "not_measured"


# --- контракт -----------------------------------------------------------


def load_schema():
    import json
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    return json.loads((root / "contracts" / "render-report.schema.json").read_text("utf-8"))


def validator():
    import pytest

    jsonschema = pytest.importorskip("jsonschema")
    return jsonschema.Draft202012Validator(load_schema())


def test_a_successful_report_matches_the_schema():
    rep, _, _ = run([120.0, 40.0])
    validator().validate(rep.to_json())


def test_a_failed_report_matches_the_schema():
    rep, _, _ = run([40.0], fail_at=1)
    validator().validate(rep.to_json())


def test_a_report_that_lost_the_measurer_matches_the_schema():
    rep, _, _ = run([40.0], raise_at=1)
    validator().validate(rep.to_json())


def test_a_half_measured_report_keeps_before_and_matches_the_schema():
    """Замер сорвался после первого раунда: «было» известно, «стало» нет."""
    rep, _, _ = run([120.0], fail_at=2)
    assert rep.before == 1 and rep.after is None
    validator().validate(rep.to_json())


def test_the_schema_refuses_a_clean_looking_failure():
    """Схема обязана уметь отвергать. Ноль дефектов у непроверенного файла —
    ровно та ложь, ради которой она написана (`Z-20`)."""
    import pytest

    jsonschema = pytest.importorskip("jsonschema")
    rep, _, _ = run([40.0], fail_at=1)
    lie = rep.to_json()
    lie["defects"] = {"before": 0, "after": 0}
    with pytest.raises(jsonschema.ValidationError):
        validator().validate(lie)


def test_the_schema_refuses_an_unknown_stop_reason():
    import pytest

    jsonschema = pytest.importorskip("jsonschema")
    rep, _, _ = run([120.0, 40.0])
    bad = rep.to_json()
    bad["stopped"] = "по-моему хватит"
    with pytest.raises(jsonschema.ValidationError):
        validator().validate(bad)


def test_cli_and_loop_agree_on_the_default_number_of_rounds():
    """`cli.py` держит своё число, чтобы разбор аргументов не тянул стадию.
    Разойтись им нельзя."""
    from mimeo.cli import VERIFY_ROUNDS
    from mimeo.verify.loop import DEFAULT_ROUNDS

    assert VERIFY_ROUNDS == DEFAULT_ROUNDS
