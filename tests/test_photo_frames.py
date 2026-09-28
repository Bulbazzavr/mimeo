"""Рамка под фото с подсказкой дизайнера (`Z-55`, вариант 2 — выбор пользователя 26 сентября).

На выданном VK Tech белая карточка с «Вставить фото» посередине. Движок писал
тезис в саму подсказку (межстрочный 52 % кегля — фраза ложилась строками друг
на друга), а карточка оставалась пустой. Теперь рамку узнаёт ANALYZE, текст в
подсказку не идёт, а рамку заливает картинка генератора — по сцене, которую
пишет модель. Живой модели и генератора не нужно: генератор — `FakeSD`.
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import replace
from xml.etree import ElementTree as ET

from mimeo.analyze import analyze_template
from mimeo.analyze.deck import load_deck
from mimeo.analyze.picture import FRAME, find_frames
from mimeo.analyze.shapes import analyze_slides
from mimeo.compose import build
from mimeo.compose.substitute import _fill_with_picture
from mimeo.model import Capacity, DeckPlan, Fill, Pattern, PatternLibrary, PatternSource, PlannedSlide, PlanSource, Slot
from mimeo.opc.package import Package
from mimeo.oxml.ns import qn
from mimeo.plan import images, matching
from mimeo.plan.content import ContentBlock, ContentDoc, ContentSection
from mimeo.plan.imagesize import image_size
from tests.fixtures.build_fixture import EXTRA_SLIDES, _slide, _text_sp, build_multi
from tests.test_generated_images import FakeSD, _gen
from tests.test_images import IMG, _Rect

BOX = (838200, 1900000, 5000000, 3200000)       # рамка: слева, под заголовком


def _box(sid: int, x: int, y: int, cx: int, cy: int) -> str:
    """Фигура без текста с заливкой, как белая карточка VK Tech: в `txBody`
    пустой прогон — у настоящей он тоже есть."""
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="Frame"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="roundRect"><a:avLst/></a:prstGeom>'
            f'<a:solidFill><a:schemeClr val="bg1"/></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr>'
            f'<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ru-RU"/><a:t></a:t></a:r>'
            f'</a:p></p:txBody></p:sp>')


def _slide_with(hint: str, dx: int = 0) -> str:
    """Заголовок, рамка, надпись в её центре (или сдвинутая на `dx`) и текст справа."""
    x, y, cx, cy = BOX
    w, h = 900000, 300000
    return _slide(
        _text_sp(2, "T", 838200, 457200, 10515600, 1325563, 4400, "Слайд раздела")
        + _box(20, x, y, cx, cy)
        + _text_sp(21, "Hint", x + cx // 2 - w // 2 + dx, y + cy // 2 - h // 2, w, h, 1000, hint)
        + _text_sp(22, "Body", 6400800, 1900000, 5000000, 3200000, 1800,
                   "Текст рядом с рамкой, достаточно длинный для абзаца")
    )


def _template(tmp_path, slide: str, name: str = "t.pptx") -> str:
    """Девять слайдов фикстуры, седьмой (две колонки) заменён на `slide`."""
    return build_multi(tmp_path / name, slides=EXTRA_SLIDES[:7] + [slide] + EXTRA_SLIDES[8:])


def _pairs(path: str) -> list[tuple[str, str]]:
    with Package(path) as pkg:
        deck = load_deck(pkg)
        slides, _ = analyze_slides(deck)
    shapes = [s for s in slides[7].shapes if s.rect is not None]
    return [(f.shape_id, h.shape_id) for f, h in find_frames(shapes, deck.slide_cx, deck.slide_cy)]


# --- ANALYZE -------------------------------------------------------------------


def test_hint_words_decide_and_geometry_confirms(tmp_path):
    """Замер 26 сентября: «короткая надпись в центре фигуры без текста» — 77
    случаев по 14 шаблонам, подсказок под фото среди них 6. Кнопка с той же
    геометрией рамкой не становится; подсказка не в центре — тоже."""
    assert _pairs(_template(tmp_path, _slide_with("Вставить фото"), "a.pptx")) == [("20", "21")]
    assert _pairs(_template(tmp_path, _slide_with("Use BIG image."), "b.pptx")) == [("20", "21")]
    assert _pairs(_template(tmp_path, _slide_with("Подробнее"), "c.pptx")) == []
    assert _pairs(_template(tmp_path, _slide_with("Вставить фото", dx=1600000), "d.pptx")) == []


def test_frame_becomes_a_picture_place_and_hint_is_not_a_text_place(tmp_path):
    library = analyze_template(_template(tmp_path, _slide_with("Вставить фото"))).patterns
    pattern = next(p for p in library.patterns if any(s.shape_id == "20" for s in p.slots))
    frame = next(s for s in pattern.slots if s.shape_id == "20")
    hint = next(s for s in pattern.slots if s.shape_id == "21")
    assert (frame.content_type, frame.picture_kind, frame.required, frame.capacity) == (
        "image", FRAME, True, None), "рамка — место под картинку, и пустой её видно"
    assert (hint.role, hint.content_type, hint.required) == ("decor", "none", False), (
        "подсказка — не место под текст, но слот: сборка сотрёт «Вставить фото»")


# --- PLAN: ранг ------------------------------------------------------------------


def _pattern() -> Pattern:
    cap = Capacity(max_chars=200, max_lines=4, chars_per_line=50, target_chars=120,
                   max_items=None, donor_chars=None, basis="test")
    small = replace(cap, max_chars=18, target_chars=18)
    return Pattern(
        id="p01", kind="image_text", donor_part="/ppt/slides/slide1.xml", donor_index=1,
        slots=(
            Slot(id="s01", role="title", content_type="text", rect=_Rect(0, 0, 6000000, 800000),
                 type_role=None, capacity=cap, required=True),
            Slot(id="s02", role="image", content_type="image", rect=_Rect(0, 900000, 4000000, 2400000),
                 type_role=None, capacity=None, required=True, picture_kind=FRAME),
            Slot(id="s03", role="decor", content_type="none", rect=_Rect(1500000, 1900000, 900000, 300000),
                 type_role=None, capacity=small, required=False),
            Slot(id="s04", role="body", content_type="text", rect=_Rect(4500000, 900000, 4000000, 2400000),
                 type_role=None, capacity=cap, required=True),
        ),
        members=(1,), cohesion=None, donor_reason="тест", source="test")


def test_hint_never_takes_text_and_empty_frame_counts_as_empty():
    """Короткий тезис влез бы в подсказку по ёмкости — и не идёт туда. Пустая
    рамка — пустое место, ровно как до `Z-55` пустая подсказка в ней: ранг и
    планы колод прежние."""
    short = [ContentBlock(id=f"b{i}", kind="paragraph", text=t) for i, t in enumerate(("Кратко", "Ещё"))]
    section = ContentSection(id="sec", heading="Заголовок", blocks=tuple(short))
    got = matching.match(section, _pattern())
    assert "s03" not in {f.slot_id for f in got.fills}
    assert [s.id for s in matching.empty_places(_pattern(), {f.slot_id for f in got.fills})] == ["s02"]


def test_frame_takes_only_a_generated_picture():
    """Заготовка генератора рисуется в пропорции рамки; картинка автора легла
    бы заливкой фигуры и растянулась — в рамку она не идёт."""
    body = ContentBlock(id="b1", kind="paragraph", text="Генераторы делают слайды по своим правилам")
    drawn = ContentBlock(id=images.GENERATED_PREFIX + "sec", kind="image", ref="out/images/sec.png",
                         min_side=2000000)
    section = ContentSection(id="sec", heading="Заголовок", blocks=(body, drawn))
    placed = matching.match(section, _pattern())
    assert any(f.slot_id == "s02" and f.kind == "image" for f in placed.fills)
    assert matching.empty_places(_pattern(), {f.slot_id for f in placed.fills}) == []

    author = replace(drawn, id="b2", ref=IMG, min_side=None)
    kept_out = matching.match(replace(section, blocks=(body, author)), _pattern())
    assert not any(f.kind == "image" for f in kept_out.fills)
    assert kept_out.dropped_images == (IMG,)


# --- PLAN: генератор заливает пустые рамки -----------------------------------------


def _library(side: int = 2400000) -> PatternLibrary:
    pattern = _pattern()
    slots = tuple(replace(s, rect=_Rect(0, 900000, 2 * side, side)) if s.id == "s02" else s
                  for s in pattern.slots)
    return PatternLibrary(source=PatternSource(filename="t.pptx", sha256="0"),
                          patterns=(replace(pattern, slots=slots),))


def _plan(*origins) -> DeckPlan:
    return DeckPlan(source=PlanSource(content="t.md", design_system_sha256="0", patterns_sha256="0"),
                    slides=tuple(PlannedSlide(index=n, pattern_id="p01", reason="тест",
                                              fills=(Fill(slot_id="s01", kind="text", text="Заголовок"),),
                                              origin_section=sec, origin_part=part)
                                 for n, (sec, part) in enumerate(origins)))


def _frame_doc(planner="mixed") -> ContentDoc:
    return ContentDoc(name="t.md", planner=planner, sections=[
        ContentSection(id="m02", heading="Движок работает на стандартной библиотеке", blocks=(),
                       kind="bullets"),
        ContentSection(id="m03", heading="Генераторы не переносят стиль", blocks=(), kind="text",
                       image_idea="Человек устало двигает блоки на слайде"),
        # На крупном числе модель просит график — это не сюжет фотографии.
        ContentSection(id="m04", heading="Переполнений стало ноль", blocks=(), kind="metric",
                       image_idea="График переполнений по неделям"),
    ])


def test_empty_frames_get_pictures_of_scenes_written_by_the_model(tmp_path):
    """Сюжет — идея модели, а без неё заголовок; сцены — одним вызовом на все
    рамки; картинка — в пропорции рамки; две части одного раздела — разные
    `seed`, иначе на соседних слайдах стояла бы одна картинка."""
    asked: list[list[str]] = []

    def rewrite(subjects, _sections=()):
        asked.append(list(subjects))
        return [f"Сцена {i}" for i, _ in enumerate(subjects)], "от модели"

    with FakeSD() as sd:
        painter = images.Painter(_gen(tmp_path, sd.base_url), {}, slide_size=(12192000, 6858000),
                                 rewrite=rewrite, folder=str(tmp_path / "images"))
        plan = painter.frames(_plan(("m02", 1), ("m02", 2), ("m03", 1), ("m04", 1)), _library(),
                              _frame_doc())
        assert asked == [["Движок работает на стандартной библиотеке",
                          "Человек устало двигает блоки на слайде", "Переполнений стало ноль"]]
        assert [r["prompt"].split(".")[0] for r in sd.requests] == [
            "Сцена 0", "Сцена 0", "Сцена 1", "Сцена 2"]
        assert sd.requests[0]["seed"] != sd.requests[1]["seed"]
    fills = [f for s in plan.slides for f in s.fills if f.slot_id == "s02"]
    assert len(fills) == 4 and all(os.path.isfile(f.ref) for f in fills)
    width, height = image_size(fills[0].ref)
    assert abs(width / height - 2.0) < 0.1, "картинка в пропорции рамки"
    assert "В рамки под фото шаблона — 4 из 4" in painter.note()


def test_frames_stay_as_they_are_when_they_cannot_be_drawn(tmp_path):
    """Колоду строила не модель, сцены не написаны, рамка мельче порога — план
    не меняется, и сказано почему."""
    plan = _plan(("m02", 1))
    painter = images.Painter(_gen(tmp_path), {}, slide_size=(12192000, 6858000),
                             rewrite=lambda xs, _sections=(): (None, "ответ не по форме"), folder=str(tmp_path))
    assert painter.frames(plan, _library(), _frame_doc(planner="deterministic")) == plan
    assert "колоду строила не модель" in painter.note()

    with FakeSD() as sd:
        painter = images.Painter(_gen(tmp_path, sd.base_url), {}, slide_size=(12192000, 6858000),
                                 rewrite=lambda xs, _sections=(): (None, "ответ не по форме"), folder=str(tmp_path))
        assert painter.frames(plan, _library(), _frame_doc()) == plan
        assert "выдуманные буквы" in painter.note() and sd.requests == []

        painter = images.Painter(_gen(tmp_path, sd.base_url), {}, slide_size=(12192000, 6858000),
                                 rewrite=lambda xs, _sections=(): (["Сцена"] * len(xs), "от модели"),
                                 folder=str(tmp_path))
        assert painter.frames(plan, _library(side=1000000), _frame_doc()) == plan
        assert sd.requests == [] and "осталось пустыми 1" in painter.note()


def test_build_fills_frames_after_the_placeholders():
    """Сборка зовёт заливку рамок после заготовок — и в одной колоде, и в
    каждом варианте (`cli._painted`)."""
    from mimeo import cli

    calls = []

    class Stub:
        def prepare(self, plans, library, doc):
            calls.append("prepare")

        def paint(self, plan, library):
            calls.append("paint")
            return plan, set()

        def frames(self, plan, library, doc):
            calls.append("frames")
            return "с рамками"

    assert cli._painted("план", Stub(), "док", "библиотека", replan=None) == "с рамками"
    assert calls == ["prepare", "paint", "frames"]


def test_one_scene_request_for_all_pictures_of_the_deck(tmp_path):
    """Сцены заготовки по идее и пустой рамки — одним запросом к модели, с
    разделом каждой картинки: модель видит набор целиком и делает его разным.
    Рамки после рисования модель второй раз не зовут."""
    asked = []

    def rewrite(subjects, sections=()):
        asked.append((list(subjects), [s.id for s in sections]))
        return [f"Сцена {i}" for i, _ in enumerate(subjects)], "от модели"

    ref = str(tmp_path / "images" / "m03.png")
    plan = _plan(("m02", 1), ("m03", 1))
    slides = list(plan.slides)
    slides[1] = replace(slides[1], fills=slides[1].fills + (Fill(slot_id="s02", kind="image", ref=ref),))
    plan = replace(plan, slides=tuple(slides))
    with FakeSD() as sd:
        painter = images.Painter(_gen(tmp_path, sd.base_url), {ref: "Идея третьего слайда"},
                                 slide_size=(12192000, 6858000), rewrite=rewrite,
                                 folder=str(tmp_path / "images"))
        painter.prepare([plan], _library(), _frame_doc())
        assert asked == [(["Идея третьего слайда", "Движок работает на стандартной библиотеке"],
                          ["m03", "m02"])]
        painted, failed = painter.paint(plan, _library())
        painter.frames(painted, _library(), _frame_doc())
        assert not failed and len(asked) == 1, "рамки не зовут модель второй раз"
        assert sorted(r["prompt"].split(".")[0] for r in sd.requests) == ["Сцена 0", "Сцена 1"]
    assert "Сцены картинок написала модель по всей колоде" in painter.note()


def test_rejected_picture_goes_back_to_the_model_with_the_reason(tmp_path):
    """Зрение забраковало картинку — модель получает прежнюю сцену и причину
    и пишет другую; рисуется новая сцена, а не прежняя с другим зерном. Новая
    сцена заменяет прежнюю и для следующих вариантов вёрстки."""
    asked = []

    def rewrite(subjects, sections=(), rejected=None):
        asked.append((list(subjects), rejected))
        return (["Сцена без букв"] if rejected else ["Сцена с буквами"]), "от модели"

    ref = str(tmp_path / "images" / "m03.png")
    plan = _plan(("m03", 1))
    slides = [replace(plan.slides[0], fills=plan.slides[0].fills + (Fill(slot_id="s02", kind="image", ref=ref),))]
    plan = replace(plan, slides=tuple(slides))
    with FakeSD() as sd:
        painter = images.Painter(_gen(tmp_path, sd.base_url), {ref: "Идея"}, slide_size=(12192000, 6858000),
                                 rewrite=rewrite, folder=str(tmp_path / "images"),
                                 check=lambda png: "буквы или надписи" if len(sd.requests) == 1 else None)
        painter.prepare([plan], _library(), _frame_doc())
        painted, failed = painter.paint(plan, _library())
        assert not failed
        assert asked[1] == (["Идея"], {"scene": "Сцена с буквами", "why": "буквы или надписи"})
        assert [r["prompt"] for r in sd.requests] == ["Сцена с буквами", "Сцена без букв"]
        assert [r["seed"] for r in sd.requests] == [42, 42], "не новое зерно, а новая сцена"
    assert painter._scenes["Идея"] == "Сцена без букв" and painter.revised == 1
    assert "модель переписала сцену с причиной брака: 1" in painter.note()


# --- COMPOSE ---------------------------------------------------------------------


def test_picture_fills_the_frame_shape_and_the_hint_is_wiped(tmp_path):
    """Картинка встаёт заливкой самой фигуры: скругление и обводка дизайнера
    остаются; «Вставить фото» со слайда уходит."""
    template = _template(tmp_path, _slide_with("Вставить фото"))
    library = analyze_template(template).patterns
    pattern = next(p for p in library.patterns if any(s.shape_id == "20" for s in p.slots))
    frame = next(s for s in pattern.slots if s.shape_id == "20")
    title = next(s for s in pattern.slots if s.role == "title")
    plan = DeckPlan(
        source=PlanSource(content="t.md", design_system_sha256="0", patterns_sha256=library.source.sha256),
        slides=(PlannedSlide(index=0, pattern_id=pattern.id, reason="тест", fills=(
            Fill(slot_id=title.id, kind="text", text="Заголовок слайда"),
            Fill(slot_id=frame.id, kind="image", ref=IMG))),))
    out = tmp_path / "deck.pptx"
    report = build(template, plan, library, str(out))
    assert not report.warnings
    with zipfile.ZipFile(out) as z:
        xml = z.read("ppt/slides/slide1.xml").decode("utf-8")
        rels = z.read("ppt/slides/_rels/slide1.xml.rels").decode("utf-8")
        media = [n for n in z.namelist() if n.startswith("ppt/media/mimeo")]
    sp = re.search(r'<p:sp>(?:(?!</p:sp>).)*?<p:cNvPr id="20".*?</p:sp>', xml, re.S).group(0)
    rid = re.search(r'<a:blipFill[^>]*><a:blip r:embed="(rId\d+)"', sp).group(1)
    assert "solidFill" not in sp.split("</p:spPr>")[0] and "roundRect" in sp
    assert re.search(rf'Id="{rid}"[^>]*Target="\.\./media/mimeo1\.png"', rels) and media
    assert "Вставить" not in xml, "подсказку сборка стёрла"


def test_picture_fill_goes_after_the_geometry_when_the_shape_had_none():
    sp_pr = ET.fromstring(
        '<p:spPr xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:xfrm/>'
        '<a:prstGeom prst="rect"/><a:ln/></p:spPr>')
    blip = _fill_with_picture(sp_pr)
    assert [c.tag for c in sp_pr] == [qn("a:xfrm"), qn("a:prstGeom"), qn("a:blipFill"), qn("a:ln")]
    assert blip.tag == qn("a:blip")
