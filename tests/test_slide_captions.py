"""Подпись шаблона: оглавление и финал (`Z-58`, `PLAN-9.0`, часть Б).

До правки kind `agenda` не ставило ни одно правило, а `closing` получал только
последний слайд шаблона: из 3 оглавлений и 10 финалов, подписанных самими
шаблонами на 14 образцах, код узнавал 0 и 0, и на выданном VK Tech наш текст
вставал на макеты «Спасибо за внимание!» посреди колоды
(`WORKLOG/2026-09-24-z58-baseline.md`).

Подписи ниже — дословно те, что стоят в шаблонах замера: так тест держит не
придуманный пример, а случаи, ради которых правка делалась.
"""

from __future__ import annotations

import json

import pytest

from mimeo.analyze import analyze_template
from mimeo.analyze import patterns as patterns_module
from mimeo.analyze.captions import builtin, caption_kind, load_config
from mimeo.analyze.deck import Rect
from mimeo.analyze.patterns import _SHORT_TEXT, _classify
from mimeo.analyze.shapes import RunObs, ShapeObs
from mimeo.plan import parse_markdown, plan_deck
from mimeo.plan.deterministic import _avoid_repeat, _positional
from mimeo.plan.matching import DEFAULT_TUNING, Match, Tuning
from tests.fixtures.build_fixture import EXTRA_SLIDES, _slide, _text_sp, build_multi

CX, CY = 12192000, 6858000


def _lists(items: int) -> str:
    """Шесть разделов-списков коротких пунктов: такой текст хочет карточки, и
    макет оглавления из четырёх пунктов в ряд ему по геометрии впору."""
    return "# Колода\n\n" + "".join(
        f"## Раздел {n}\n\n" + "".join(f"- Пункт {n}.{i}\n" for i in range(1, items + 1)) + "\n"
        for n in range(1, 7)
    )


def _shape(text: str = "", x: int = 838200, y: int = 457200, cx: int = 10515600,
           cy: int = 1325563, sid: str = "2", kind: str = "sp",
           has_chart: bool = False) -> ShapeObs:
    runs = ((RunObs(0, sid, text, 0, 0, 18.0, False, False, None, 0.0, None, None,
                    None, None, None, None),) if text else ())
    return ShapeObs(
        slide_index=0, shape_id=sid, name=sid, kind=kind, ph_type=None, ph_idx=None,
        rect=Rect(x, y, cx, cy), rotated=False, hidden=False, geom="rect",
        corner_radius_pct=None, fill_kind=None, fill=None, line=None, line_w_emu=None,
        has_shadow=False, runs=runs, para_count=1 if text else 0, has_chart=has_chart,
    )


# --- конфиг ------------------------------------------------------------


def test_config_file_matches_builtin_values():
    """Встроенные значения — запасные на случай, если файла нет; разойтись с
    файлом молча они не должны."""
    cfg, base = load_config(), builtin()
    assert cfg.loaded
    for kind in ("agenda", "closing"):
        assert [p.pattern for p in cfg.patterns(kind)] == [p.pattern for p in base.patterns(kind)]


@pytest.mark.parametrize("body", [
    None,                                                  # файла нет
    "{не json",
    json.dumps({"agenda": ["("], "closing": ["x"]}),        # негодное выражение
    json.dumps({"agenda": "содержание", "closing": ["x"]}),  # не список
    json.dumps({"agenda": [], "closing": ["x"]}),            # пусто
])
def test_unreadable_config_falls_back_and_says_so(tmp_path, body):
    path = tmp_path / "kinds.json"
    if body is not None:
        path.write_text(body, encoding="utf-8")
    cfg = load_config(str(path))
    assert not cfg.loaded
    assert [p.pattern for p in cfg.closing] == [p.pattern for p in builtin().closing]


def test_library_names_an_unread_config(multi_template_path, monkeypatch):
    """Молчаливые встроенные значения неотличимы от прочитанного конфига."""
    assert not any("Конфиг подписей" in n for n in analyze_template(multi_template_path).patterns.notes)
    monkeypatch.setattr(patterns_module, "load_caption_config", builtin)
    notes = analyze_template(multi_template_path).patterns.notes
    assert any("Конфиг подписей" in n for n in notes)


# --- подпись -----------------------------------------------------------


@pytest.mark.parametrize("text, kind", [
    # Дословно из шаблонов замера: 3 оглавления и 10 финалов.
    ("Clean Business - Agenda", "agenda"),       # shablon-biznes-plana, сл. 3
    ("Содержание", "agenda"),                    # VK Tech, сл. 9, и это не заголовок, а body
    ("Оглавление", "agenda"),                    # WorkSpace, сл. 2
    ("Q&A", "closing"),                          # Performance_Up, сл. 44; WorkSpace, сл. 28
    ("Thank You", "closing"),                    # Performance_Up, сл. 47
    ("THANK YOU", "closing"),                    # business-plan-10, сл. 13
    ("THANKS!", "closing"),                      # svetlaja, сл. 23
    ("Any questions?", "closing"),               # svetlaja, сл. 23
    ("Спасибо за внимание!", "closing"),         # VK Tech, сл. 4–6; VK Education, сл. 52
    ("Спасибо за внимание", "closing"),          # WorkSpace, сл. 29
    # Формы, которых в замере нет, а в языке есть.
    ("Благодарим за внимание", "closing"),
    ("Вопросы и ответы", "closing"),
    ("Повестка дня", "agenda"),
    ("Table of Contents", "agenda"),
])
def test_template_captions_are_recognised(text, kind):
    assert caption_kind([_shape(text)], builtin(), _SHORT_TEXT) == kind


@pytest.mark.parametrize("text", [
    "Итого",                        # три таблицы VK Tech и VK Education
    "Ключевые вопросы",             # расписание VK Education, сл. 40
    "Вопросы",                      # без знака вопроса — рубрика, а не финал
    "Благодаря ИИ — быстрее",       # предлог, а не благодарность
    "Содержательная часть",         # не слово «содержание»
    "Контакты",
    "Планы на квартал",
])
def test_lookalikes_stay_silent(text):
    assert caption_kind([_shape(text)], builtin(), _SHORT_TEXT) is None


def test_caption_must_be_short():
    """Подпись — короткая фигура; слово из абзаца подписью не считается.
    Граница включительная: ровно `_SHORT_TEXT` знаков — ещё подпись."""
    at_limit = "Спасибо".ljust(_SHORT_TEXT, ".")
    over = "Спасибо".ljust(_SHORT_TEXT + 1, ".")
    assert caption_kind([_shape(at_limit)], builtin(), _SHORT_TEXT) == "closing"
    assert caption_kind([_shape(over)], builtin(), _SHORT_TEXT) is None


def test_caption_is_looked_for_in_each_shape_apart():
    """Короткая фигура без подписи и длинная с подписью — не финал: подпись
    не собирается из текста всего слайда."""
    shapes = [_shape("Итоги", sid="2"),
              _shape("Благодарим команду за то, что проект сдан в срок и без переработок", sid="3",
                     y=2286000)]
    assert caption_kind(shapes, builtin(), _SHORT_TEXT) is None


# --- _classify ---------------------------------------------------------


def test_caption_beats_a_full_slide_picture():
    """Финал «Спасибо» на фоне фотографии — не `image_full`."""
    shapes = [_shape(sid="1", kind="pic", x=0, y=0, cx=CX, cy=CY),
              _shape("Спасибо за внимание!", sid="2")]
    assert _classify(shapes, CX, CY, 3, 10, builtin()) == "closing"
    assert _classify(shapes, CX, CY, 3, 10) == "image_full"


def test_caption_beats_a_row_of_cards():
    """Оглавление из четырёх пунктов геометрией неотличимо от карточек."""
    cards = [_shape(f"Пункт {i}", sid=str(10 + i), x=838200 + i * 2700000, y=2286000,
                    cx=2400000, cy=1500000) for i in range(4)]
    shapes = [_shape("Содержание")] + cards
    assert _classify(shapes, CX, CY, 8, 50, builtin()) == "agenda"
    assert _classify(shapes, CX, CY, 8, 50) == "cards"


def test_chart_and_table_still_come_first():
    shapes = [_shape("Спасибо", sid="2"), _shape(sid="3", y=2286000, has_chart=True)]
    assert _classify(shapes, CX, CY, 3, 10, builtin()) == "chart"


def test_positional_closing_stays_the_fallback():
    """Без подписи последний слайд с коротким текстом — по-прежнему финал."""
    shapes = [_shape("До встречи")]
    assert _classify(shapes, CX, CY, 9, 10, builtin()) == "closing"
    assert _classify(shapes, CX, CY, 5, 10, builtin()) == "section"


# --- выбор раскладки ---------------------------------------------------


def _match(pid: str, kind: str, score: float) -> Match:
    return Match(pattern_id=pid, kind=kind, fills=(), score=score, reason="r", leftover=())


@pytest.mark.parametrize("first, last", [(True, False), (False, False), (False, True)])
def test_agenda_layout_pays_on_every_place(first, last):
    """Путь без модели оглавления не заказывает: макет оглавления проигрывает
    равному по совпадению макету на любом месте колоды и говорит почему."""
    ranked = _positional([_match("p01", "agenda", 1.0), _match("p02", "text", 1.0)],
                         first=first, last=last)
    assert [m.pattern_id for m in ranked] == ["p02", "p01"]
    assert "оглавлени" in ranked[1].reason


def test_misplaced_layout_is_worse_than_any_repeat():
    """VK Tech, вариант 3, политика `r0.8-s0.45-o0.9`: хорошая раскладка уже
    стояла в колоде, и голый штраф 0.6 проигрывал повтору 0.8 — «Содержание» и
    «Спасибо» вставали посреди колоды. Числа — из замера."""
    tuning = Tuning(repeat=0.8, slack=0.45, over=0.9)
    for kind in ("agenda", "closing", "cover"):
        ranked = _positional([_match("p10", "bullets", 0.88), _match("p08", kind, 0.70)],
                             first=False, last=False, tuning=tuning)
        ranked = _avoid_repeat(ranked, {"p10": 1}, tuning)
        assert ranked[0].pattern_id == "p10", kind


def test_closing_keeps_its_bonus_last():
    ranked = _positional([_match("p01", "closing", 0.5), _match("p02", "text", 0.9)],
                         first=False, last=True, tuning=DEFAULT_TUNING)
    assert ranked[0].pattern_id == "p01"


# --- сквозь планировщик ------------------------------------------------


def _agenda_slide() -> str:
    """Оглавление: подпись и четыре пункта в ряд — геометрией это карточки."""
    items = "".join(
        _text_sp(20 + i, f"I{i}", 838200 + i * 2700000, 2286000, 2400000, 1500000, 1800,
                 f"Пункт {i + 1}")
        for i in range(4)
    )
    return _slide(_text_sp(2, "T", 838200, 457200, 10515600, 1325563, 4400, "Содержание") + items)


@pytest.fixture(scope="module")
def agenda_analysis(tmp_path_factory):
    """Фикстура `multi` с макетом оглавления посреди шаблона."""
    slides = EXTRA_SLIDES[:6] + [_agenda_slide()] + EXTRA_SLIDES[6:]
    path = build_multi(tmp_path_factory.mktemp("agenda") / "agenda.pptx", slides)
    return analyze_template(path)


def test_fixture_agenda_is_recognised(agenda_analysis):
    kinds = [p.kind for p in agenda_analysis.patterns.patterns]
    assert kinds.count("agenda") == 1


@pytest.mark.parametrize("tuning", [
    DEFAULT_TUNING,
    Tuning(repeat=0.8, slack=0.45, over=0.9),   # политика варианта 3 на VK Tech
])
def test_ordinary_text_avoids_agenda_and_misplaced_layouts(agenda_analysis, tuning):
    """Сквозь `plan_deck`: оглавление не встаёт под обычный текст ни при одной
    политике, финал — только последним, обложка — только первой.

    Списки по три пункта влезают и в карточки, и в оглавление. Замер на этой
    фикстуре: с голым штрафом 0.6 политика `repeat 0.8` ставит оглавление на
    слайды 4 и 6 — карточки к тому времени уже платят за повтор."""
    a = agenda_analysis
    plan = plan_deck(parse_markdown(_lists(3), name="lists"), a.patterns,
                     a.design_system.source.sha256, tuning=tuning)
    kinds = {p.id: p.kind for p in a.patterns.patterns}
    used = [kinds[s.pattern_id] for s in plan.slides]
    assert len(used) == 7 and not plan.unplaced
    assert "agenda" not in used
    assert "closing" not in used[:-1] and "cover" not in used[1:]


def test_agenda_is_a_penalty_not_a_ban(agenda_analysis):
    """Список из четырёх пунктов на этой фикстуре влезает только в макет
    оглавления. Потерять раздел хуже, чем поставить его туда: раздел встаёт, и
    причина выбора это называет."""
    a = agenda_analysis
    plan = plan_deck(parse_markdown(_lists(4), name="lists"), a.patterns,
                     a.design_system.source.sha256)
    kinds = {p.id: p.kind for p in a.patterns.patterns}
    assert not plan.unplaced
    agenda = [s for s in plan.slides if kinds[s.pattern_id] == "agenda"]
    assert agenda and all("оглавлени" in s.reason for s in agenda)
