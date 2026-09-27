"""Аудит готовых слайдов (`Z-34`, `PLAN-11.0`): проверки кодом, разбор ответа
зрения, выбор исправлений. PowerPoint и модель не запускаются: замер, план и
ответ модели собираются вручную.
"""

from __future__ import annotations

import json

from mimeo.audit import checks, fix, vision
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
from mimeo.verify.metrics import Measurement, ShapeMetric


def slot(slot_id: str, role: str = "body") -> Slot:
    return Slot(id=slot_id, role=role, content_type="text", rect=None, type_role=None,
                capacity=None, required=False, shape_id=slot_id)


def pattern(pattern_id: str, kind: str = "text") -> Pattern:
    return Pattern(id=pattern_id, kind=kind, donor_part="/ppt/slides/slide1.xml", donor_index=0,
                   slots=(slot("t", "title"), slot("b"), slot("l", "bullet_list")), members=(0,),
                   cohesion=None, donor_reason="", source="slides")


LIB = PatternLibrary(source=PatternSource("t.pptx", "0" * 64),
                     patterns=(pattern("p"), pattern("c", "cover")))


def planned(index: int, title: str, *, body: str | None = None, items=None, pid="p",
            scale=None) -> PlannedSlide:
    fills = [Fill(slot_id="t", kind="text", text=title, font_scale=scale)]
    if body:
        fills.append(Fill(slot_id="b", kind="text", text=body))
    if items:
        fills.append(Fill(slot_id="l", kind="list", items=tuple(items)))
    return PlannedSlide(index=index, pattern_id=pid, fills=tuple(fills), reason="")


def deck(*slides: PlannedSlide) -> DeckPlan:
    return DeckPlan(source=PlanSource("c.md", "0" * 64, "0" * 64), slides=slides)


def text(slide: int, shape_id: str, left: float, top: float, width: float, height: float,
         size: float = 14.0) -> ShapeMetric:
    return ShapeMetric(slide=slide, shape_id=shape_id, width=width, height=height,
                       text_width=width, text_height=height, margin_left=0.0, margin_right=0.0,
                       margin_top=0.0, margin_bottom=0.0, autofit=0, chars=20, left=left, top=top,
                       font_size=size, bound_left=left, bound_top=top)


def measured(*shapes: ShapeMetric) -> Measurement:
    return Measurement(deck="d.pptx", status="ok", shapes=shapes, slides=1, page=(960.0, 540.0))


# --- проверки кодом ---------------------------------------------------


def test_density_checks_follow_appendix_one():
    """Больше 6 пунктов и пункт длиннее 15 слов — пороги Приложения 1."""
    long_item = " ".join(["слово"] * 16)
    plan = deck(planned(0, "Семь пунктов", items=[f"пункт {k}" for k in range(7)]),
                planned(1, "Длинный пункт", items=[long_item, "короткий"]))
    found = checks.run(checks.views(plan, LIB), None)
    assert [(f["slide"], f["check"]) for f in found] == [(1, "items"), (2, "long_item")]
    assert all(f["kind"] == "deterministic" and f["fix"] == "model" for f in found)


def test_fifteen_words_is_still_fine():
    plan = deck(planned(0, "Ровно пятнадцать", items=[" ".join(["слово"] * 15)]))
    assert checks.run(checks.views(plan, LIB), None) == []


def test_title_only_slide_and_duplicate_title():
    plan = deck(planned(0, "Обложка", pid="c"),
                planned(1, "Один заголовок"),
                planned(2, "Рост выручки", body="текст"),
                planned(3, "Рост  выручки!", body="другой текст"))
    found = {(f["slide"], f["check"]) for f in checks.run(checks.views(plan, LIB), None)}
    # Обложка — заголовок без содержания по замыслу, не находка.
    assert found == {(2, "empty"), (4, "duplicate")}


def test_verify_leftovers_are_reported_with_their_fix():
    plan = deck(planned(0, "А", body="текст"), planned(1, "Б", body="текст"))
    verify = {"unresolved": [{"slide": 0, "ratio": 1.3}],
              "overflow_width": [{"slide": 1, "ratio": 1.03}],
              "occluded": [{"slide": 1}]}
    found = {(f["slide"], f["check"], f["fix"]) for f in checks.run(checks.views(plan, LIB), verify)}
    assert found == {(1, "overflow", None), (2, "too_wide", "layout"), (2, "occluded", None)}


def test_slide_numbers_follow_the_file_not_the_plan():
    """Сборка пропустила слайд плана — номер в колоде по `slides_written`."""
    plan = deck(planned(0, "А", body="т"), planned(1, "Б"), planned(2, "В", body="т"))
    views = checks.views(plan, LIB, slides_written=(0, 2))
    assert [(v.position, v.title) for v in views] == [(1, "А"), (2, "В")]


# --- наезд надписей и край слайда ---------------------------------------


def test_title_running_into_a_caption_is_a_collision():
    """Education, вариант 1, слайд 9 (27 сентября): 34 × 35 пт при кегле 14."""
    m = measured(text(9, "1060", 53.0, 54.5, 641.0, 38.9, 36.0),
                 text(9, "1069", 660.8, 52.2, 213.0, 37.0, 14.0))
    assert [c[1] for c in checks.collisions(m)] == ["collision"]


def test_footnote_star_next_to_text_is_not_a_collision():
    """Звёздочка сноски VK Tech вплотную к тексту: 7 пт по ширине при кегле
    11.7 — замысел дизайнера, и порог «строка» его не зовёт."""
    m = measured(text(53, "2654", 100.0, 200.0, 107.0, 30.0, 11.69),
                 text(53, "2662", 200.0, 185.0, 40.0, 30.0, 11.69))
    assert checks.collisions(m) == []


def test_text_past_the_slide_edge():
    m = measured(text(10, "720", 33.8, 139.6, 300.0, 420.0, 48.0))
    assert [c[1] for c in checks.collisions(m)] == ["off_slide"]


def test_no_measurement_means_no_verdict_not_clean():
    """Замера нет — проверять нечем; пустой список здесь, а «не смог» говорит
    отчёт аудита (`status: partial`)."""
    assert checks.collisions(None) == []
    failed = Measurement(deck="d.pptx", status="error", shapes=(), slides=0)
    assert checks.collisions(failed) == []


# --- разбор ответа зрения ---------------------------------------------


def test_vision_findings_skip_questions_that_do_not_apply():
    """У обложки заголовок — название, а не вывод: такой ответ не находка."""
    answer = {name: True for name, *_ in vision.QUESTIONS}
    answer.update(title_is_conclusion=False, no_service_text=False, problem="«Вставить фото»")
    cover = checks.SlideView(0, 1, "cover", "VK Tech", (), (), 0)
    body = checks.SlideView(1, 2, "text", "Результаты", (), ("т",), 0)
    assert [f["check"] for f in vision.findings(answer, cover)] == ["no_service_text"]
    got = vision.findings(answer, body)
    assert [f["check"] for f in got] == ["title_is_conclusion", "no_service_text"]
    assert all(f["kind"] == "contextual" and f["fix"] == "model" for f in got)
    assert "«Вставить фото»" in got[0]["detail"]


def test_null_picture_answer_is_not_a_finding():
    answer = {name: True for name, *_ in vision.QUESTIONS}
    answer["pictures_on_topic"] = None
    view = checks.SlideView(0, 1, "text", "Т", (), ("т",), 0)
    assert vision.findings(answer, view) == []


def test_vision_prompt_is_read_from_config():
    cfg = vision.load_config()
    assert cfg.loaded and "{n}" in cfg.question and "{findings}" in cfg.revise
    for name, *_ in vision.QUESTIONS:
        assert name in cfg.question, name


# --- исправление выбранного -------------------------------------------


def test_layout_fix_shrinks_only_the_chosen_slide_of_its_variant():
    plan = deck(planned(0, "Первый", body="т"), planned(1, "Второй", body="т", scale=80))
    picks = [{"fix": "layout", "title": "Второй", "variant": 2},
             {"fix": "layout", "title": "Первый", "variant": 1},
             {"fix": "model", "title": "Первый", "variant": 2}]
    titles = fix.layout_titles(picks, 2)
    assert titles == {"Второй"}
    shrunk, hit = fix.shrink(plan, LIB, titles)
    assert hit == 1
    assert [f.font_scale for f in shrunk.slides[0].fills] == [None, None]
    assert [f.font_scale for f in shrunk.slides[1].fills] == [68, 85]


def test_model_fix_lines_name_slides_by_title_once():
    picks = [{"fix": "model", "title": "Рост", "detail": "нет: вывод"},
             {"fix": "model", "title": "Рост", "detail": "нет: вывод"},
             {"fix": "layout", "title": "Рост", "detail": "наезд"}]
    assert fix.revise_lines(picks) == ("слайд «Рост»: нет: вывод",)


def test_fix_all_takes_every_fixable_finding(tmp_path):
    for n, found in ((1, [{"id": "1-1", "fix": "layout"}, {"id": "1-2", "fix": None}]),
                     (2, [{"id": "2-1", "fix": "model"}])):
        (tmp_path / f"audit-{n}.json").write_text(json.dumps({"findings": found}), encoding="utf-8")
    assert [f["id"] for f in fix.load("all", str(tmp_path))] == ["1-1", "2-1"]
    chosen = tmp_path / "fix.json"
    chosen.write_text(json.dumps({"findings": [{"id": "2-1", "fix": "model"}]}), encoding="utf-8")
    assert [f["id"] for f in fix.load(str(chosen), str(tmp_path))] == ["2-1"]
