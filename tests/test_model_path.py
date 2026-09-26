"""Путь модели в сборке (`PLAN-9.0`, Ш3; `ADR-0023`).

Модель строит колоду из сплошного текста: один запрос на колоду — и на три
варианта тоже, — проверки Ш4 на весь ответ, обложка — только название, пункты
оглавления собирает код, тип слайда — довод среди видов, которые содержание
способно заполнить. Любой другой исход — путь без модели, **тот же план, что и
без флага**, и слова о том, почему. Живой модели не нужно: поддельный сервер
(`tests/fake_model.py`) отвечает колодой по заказу.
"""

from __future__ import annotations

import json
import os
import shutil
import socket

import pytest

from mimeo import cli
from mimeo.analyze import analyze_template
from mimeo.plan import cache, client, outline
from mimeo.plan.content import ContentBlock, ContentDoc, ContentSection
from mimeo.plan.deterministic import _forced_parts, _positional, plan_deck, split
from mimeo.plan.matching import Match, preferred_kinds
from mimeo.plan.prompt import Mode
from mimeo.plan.prose import load_content
from tests.fake_model import FakeModel
from tests.fixtures.build_fixture import EXTRA_SLIDES, _text_sp, build_multi
from tests.fixtures.build_fixture import _slide as _fixture_slide
from tests.test_slide_captions import _agenda_slide

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(ROOT, "contracts", "outline.schema.json")
PNG = os.path.join(ROOT, "examples", "img", "pipeline-stages.png")

#: Проза со списком: `ContentDoc.origin` списка не держит, и тест видит, что
#: модели ушёл файл целиком (`WORKLOG/2026-09-25-z57-sh3-baseline.md`, § 1).
TEXT = (
    "Сделай презентацию про наш движок mimeo. Мы разбираем шаблон и достаём из него "
    "цвета, шрифты и размеры. Раскладок в живых шаблонах от 5 до 49 штук.\n\n"
    "Проверку вёрстки делает настоящий PowerPoint, схема стадий — img/stages.png.\n\n"
    "Что проверяем:\n"
    "- переполнение текста\n"
    "- заслонение картинкой\n\n"
    "Просим доступ к инференсу."
)


def _slide(heading, kind, theses=(), images=(), idea=""):
    return {"heading": heading, "kind": kind, "role": "прочее", "theses": list(theses),
            "image_idea": idea, "images": list(images)}


def _answer() -> dict:
    """Ответ, который проходит все проверки в обоих режимах."""
    return {"slides": [
        _slide("Движок mimeo", "cover", ["движок mimeo"]),
        _slide("Содержание", "agenda", ["пересказ модели", "его заменит код"]),
        _slide("Разбор шаблона", "bullets", ["Мы разбираем шаблон", "цвета, шрифты и размеры"]),
        _slide("Раскладки", "text", ["Раскладок в живых шаблонах от 5 до 49 штук"]),
        _slide("Проверка вёрстки", "text", [
            "Проверку вёрстки делает настоящий PowerPoint",
            "переполнение текста", "заслонение картинкой"], ["img/stages.png"], "Схема стадий"),
        _slide("Просьба", "closing", ["Просим доступ к инференсу"]),
    ], "missing_roles": []}


#: Пункты оглавления, которые соберёт код: заголовки всех слайдов, кроме
#: обложки и оглавления. Четыре — ровно столько мест у макета оглавления фикстуры.
AGENDA = ["Разбор шаблона", "Раскладки", "Проверка вёрстки", "Просьба"]


@pytest.fixture
def prose(tmp_path) -> str:
    folder = tmp_path / "text"
    (folder / "img").mkdir(parents=True)
    shutil.copy(PNG, folder / "img" / "stages.png")
    path = folder / "text.md"
    path.write_text(TEXT, encoding="utf-8")
    return str(path)


@pytest.fixture(scope="module")
def agenda_template(tmp_path_factory) -> str:
    """Шаблон `multi` с макетом оглавления: список из трёх пунктов влезает и в
    карточки, и в оглавление (`test_slide_captions`)."""
    slides = EXTRA_SLIDES[:6] + [_agenda_slide()] + EXTRA_SLIDES[6:]
    return build_multi(tmp_path_factory.mktemp("agenda") / "agenda.pptx", slides)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _config(tmp_path, base_url=None, access=client.Access.ON, **endpoint) -> client.ClientConfig:
    ep = client.Endpoint(base_url=base_url or f"http://127.0.0.1:{_free_port()}/v1",
                         timeout_sec=10, budget_sec=20, **endpoint)
    return client.ClientConfig(access=access, endpoint=ep, extra_body={},
                               cache_root=str(tmp_path / "cache"),
                               tz_cache_root=str(tmp_path / "tz-cache"))


def _cli(monkeypatch, config, *argv) -> int:
    monkeypatch.setattr(cli, "load_model_config", lambda: config)
    args = cli.build_parser().parse_args(list(argv))
    return args.func(args)


def _validate(record: dict) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    with open(SCHEMA, encoding="utf-8") as fh:
        jsonschema.validate(record, json.load(fh))


def _read(path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --- колода модели сквозь plan ---------------------------------------------


def test_accepted_answer_becomes_the_deck(prose, agenda_template, tmp_path, monkeypatch):
    """Одна обложка — только название; пункты оглавления — заголовки колоды;
    заказанное оглавление встаёт на свой макет; модели ушёл файл целиком."""
    out = tmp_path / "out"
    with FakeModel(answer=_answer()) as fake:
        code = _cli(monkeypatch, _config(tmp_path, fake.base_url),
                    "plan", agenda_template, prose, "-o", str(out), "--llm", "on", "-q")
    assert code == 0
    assert len(fake.requests) == 1
    sent = fake.requests[0]
    assert sent["messages"][1]["content"] == TEXT, "модели — файл целиком, со списком"
    assert sent["response_format"]["json_schema"]["name"] == "deck"

    record = _read(out / "outline.json")
    _validate(record)
    assert record["status"] == "accepted" and record["answer_source"] == "model"
    assert record["deck"][1]["theses"] == AGENDA

    plan = _read(out / "deck-plan.json")
    assert plan["diagnostics"]["planner"] == "mixed"
    kinds = {p.id: p.kind for p in analyze_template(agenda_template).patterns.patterns}
    slides = plan["slides"]
    assert kinds[slides[0]["pattern_id"]] == "cover"
    assert [s["origin"]["section"] for s in slides].count("m01:cover") == 1
    agenda = [s for s in slides if s["origin"]["section"] == "m02"]
    assert [kinds[s["pattern_id"]] for s in agenda] == ["agenda"], "заказанное оглавление — на своём макете"
    assert "не заказывали" not in agenda[0]["reason"], "обоснование видит эксперт — оно не врёт"
    texts = [f.get("text") for s in slides for f in s["fills"]] + \
            [i for s in slides for f in s["fills"] for i in (f.get("items") or [])]
    assert "движок mimeo" not in texts, "подзаголовок обложки на слайд не идёт"
    assert any(w.startswith("Модель on") for w in plan["diagnostics"]["warnings"])


def test_cached_answer_needs_no_server(prose, tmp_path):
    """Ответ из кэша строит ту же колоду, что и вызов, — сети нет (`ADR-0021`)."""
    fallback = load_content(prose)
    with FakeModel(answer=_answer()) as fake:
        first = outline.run(prose, fallback, config=_config(tmp_path, fake.base_url))
    again = outline.run(prose, fallback, config=_config(tmp_path, access=client.Access.CACHE))
    assert (first.status, again.status) == ("accepted", "accepted")
    assert again.record["answer_source"] == "cache" and again.record["seconds"] is None
    assert again.doc.sections == first.doc.sections


def _refusal(kind, tmp_path):
    """Сценарий отказа: (конфиг, план ответов сервера, ответ, ждём статус)."""
    bad = _answer()
    bad["slides"][2]["theses"].append("шаблонов 60")        # число, которого нет в тексте
    return {
        "off": (dict(access=client.Access.OFF), (), None, "off"),
        "miss": (dict(access=client.Access.CACHE), (), None, "no_answer"),
        "down": (dict(), None, None, "no_answer"),
        "invented": (dict(), ("ok",), bad, "rejected"),
        "unparsed": (dict(), ("текст:это не JSON",), None, "unparsed"),
        "too_long": (dict(context_tokens=4100), ("ok",), None, "too_long"),
    }[kind]


@pytest.mark.parametrize("kind", ["off", "miss", "down", "invented", "unparsed", "too_long"])
def test_every_refusal_builds_the_plan_without_the_model(kind, prose, multi_template_path, tmp_path):
    """Любой отказ — тот же план, что без модели, и причина словами."""
    overrides, steps, answer, status = _refusal(kind, tmp_path)
    fallback = load_content(prose)
    a = analyze_template(multi_template_path)
    if steps is None:                                        # сервера нет вовсе
        got = outline.run(prose, fallback, config=_config(tmp_path, **overrides))
        requests = []
    else:
        with FakeModel(*steps, answer=answer or _answer()) as fake:
            access = overrides.pop("access", client.Access.ON)
            got = outline.run(prose, fallback,
                              config=_config(tmp_path, fake.base_url, access=access, **overrides))
        requests = fake.requests
    assert got.status == status
    assert "без модели" in got.line
    _validate(got.record)
    if kind in ("off", "miss", "too_long"):
        assert requests == [], "модель не звали"
    if kind == "invented":
        assert "«числа»" in got.line
    if kind in ("invented", "unparsed"):
        assert len(requests) == 2 and got.record["retry"]["answer"] is not None, "повтор — один"
    sha = a.design_system.source.sha256
    assert plan_deck(got.doc, a.patterns, sha).slides == plan_deck(fallback, a.patterns, sha).slides
    assert got.doc.notes[-1].startswith("Модель ")


def test_rejected_answer_is_retried_with_what_failed(prose, tmp_path):
    """Отказ проверок — один повтор: модели уходят её ответ и перечень
    нарушений; годный повтор строит колоду, и кэш повторяет оба ответа."""
    bad = _answer()
    bad["slides"][3]["theses"] = ["Раскладок в живых шаблонах много"]    # потеряны 5 и 49
    fallback = load_content(prose)
    with FakeModel("текст:" + json.dumps(bad, ensure_ascii=False), "ok", answer=_answer()) as fake:
        got = outline.run(prose, fallback, config=_config(tmp_path, fake.base_url))
    assert got.status == "accepted" and len(fake.requests) == 2
    retry = fake.requests[1]["messages"]
    assert [m["role"] for m in retry] == ["system", "user", "assistant", "user"]
    assert json.loads(retry[2]["content"]) == bad, "модели — её же прошлый ответ"
    assert "числа" in retry[3]["content"] and "'49'" in retry[3]["content"]
    assert "со второй попытки" in got.line and "«числа»" in got.line
    record = got.record
    _validate(record)
    assert not all(c["ok"] for c in record["checks"]), "первая попытка записана как была"
    assert all(c["ok"] for c in record["retry"]["checks"]) and record["deck"]
    assert record["retry"]["key"] != record["key"]

    again = outline.run(prose, fallback, config=_config(tmp_path, access=client.Access.CACHE))
    assert again.status == "accepted" and again.record["retry"]["answer_source"] == "cache"
    assert again.doc.sections == got.doc.sections


def test_markdown_needs_no_request(tmp_path):
    md = tmp_path / "demo.md"
    md.write_text("# Заголовок\n\nАбзац про движок.\n\n## Раздел\n\n- пункт\n", encoding="utf-8")
    with FakeModel(answer=_answer()) as fake:
        got = outline.run(str(md), load_content(str(md)), config=_config(tmp_path, fake.base_url))
    assert got.status == "markup" and fake.requests == []
    _validate(got.record)


def test_three_variants_take_one_answer(prose, multi_template_path, tmp_path, monkeypatch):
    """Три вёрстки одного содержания: запрос от политики варианта не зависит
    (`ADR-0023`, «Следствия»), и вызов один — не три."""
    out = tmp_path / "out"
    report = tmp_path / "report.json"
    with FakeModel(answer=_answer()) as fake:
        _cli(monkeypatch, _config(tmp_path, fake.base_url), "build", multi_template_path, prose,
             "-o", str(out), "--output", str(out / "deck.pptx"), "--variants", "3",
             "--llm", "on", "--report", str(report), "-q")
    assert len(fake.requests) == 1
    assert _read(out / "outline.json")["status"] == "accepted"
    warnings = _read(report)["diagnostics"]["warnings"]
    assert any(w.startswith("Модель on") and "колоду построила модель" in w for w in warnings), \
        "веб видит путь модели через --report"


def test_record_is_rewritten_by_the_next_build(prose, multi_template_path, tmp_path, monkeypatch):
    """`outline.json` пишется всегда: иначе файл прошлой сборки соврал бы про эту."""
    out = tmp_path / "out"
    config = _config(tmp_path)
    _cli(monkeypatch, config, "plan", multi_template_path, prose, "-o", str(out), "--llm", "off", "-q")
    assert _read(out / "outline.json")["status"] == "off"
    md = tmp_path / "demo.md"
    md.write_text("# Заголовок\n\nАбзац.\n", encoding="utf-8")
    _cli(monkeypatch, config, "plan", multi_template_path, str(md), "-o", str(out), "-q")
    assert _read(out / "outline.json")["status"] == "markup"


@pytest.mark.parametrize("text_in_tz", [False, True])
def test_cache_folder_follows_the_text_not_the_template(text_in_tz, tmp_path, monkeypatch,
                                                        multi_template_path):
    """Шаблон из-под `tz/` в запрос не входит и кэш в `tz/` не переводит; текст
    из-под `tz/` — переводит (`ADR-0023`, п. 2)."""
    monkeypatch.setattr(cache, "repo_root", lambda: str(tmp_path))
    (tmp_path / "tz").mkdir()
    template = tmp_path / "tz" / "t.pptx"
    shutil.copy(multi_template_path, template)
    folder = tmp_path / ("tz" if text_in_tz else "examples") / "texts"
    (folder / "img").mkdir(parents=True)
    shutil.copy(PNG, folder / "img" / "stages.png")
    text = folder / "text.md"
    text.write_text(TEXT, encoding="utf-8")
    with FakeModel(answer=_answer()) as fake:
        config = client.ClientConfig(access=client.Access.ON, extra_body={},
                                     endpoint=client.Endpoint(base_url=fake.base_url),
                                     cache_root="cache/llm", tz_cache_root="tz/cache/llm")
        _cli(monkeypatch, config, "plan", str(template), str(text), "-o", str(tmp_path / "out"), "-q")
    open_cache = list((tmp_path / "cache" / "llm").glob("*.json"))
    closed = list((tmp_path / "tz" / "cache" / "llm").glob("*.json"))
    assert (len(open_cache), len(closed)) == ((0, 1) if text_in_tz else (1, 0))


# --- колода из ответа --------------------------------------------------------


def test_image_path_is_found_from_the_text_folder(prose, tmp_path, monkeypatch):
    """Путь в тексте записан от каталога текста, а сборку зовут откуда угодно."""
    monkeypatch.chdir(tmp_path)
    answer = _answer()
    answer["slides"][2]["images"] = ["img/нет.png"]
    doc, notes, deck = outline.to_doc(answer, TEXT, prose, "t")
    refs = [b.ref for s in doc.sections for b in s.blocks if b.kind == "image"]
    assert len(refs) == 1 and os.path.isfile(refs[0])
    assert deck[4]["images"] == refs
    assert any("img/нет.png" in n and "нет на диске" in n for n in notes)


def test_quote_becomes_a_quote_block():
    """Иначе вид «quote» не вошёл бы в пригодные никогда: тезис — абзац."""
    answer = {"slides": [_slide("Главное", "quote", ["с ростом потока время растёт"])],
              "missing_roles": []}
    doc, _, _ = outline.to_doc(answer, "", "t.md", "t")
    section = doc.sections[0]
    assert [b.kind for b in section.blocks] == ["quote"]
    assert preferred_kinds(section)[0] == "quote"


def test_empty_agenda_is_dropped():
    answer = {"slides": [_slide("Тема", "cover"), _slide("Содержание", "agenda", ["x"])],
              "missing_roles": []}
    doc, _, _ = outline.to_doc(answer, "", "t.md", "t")
    assert [s.kind for s in doc.sections] == ["cover"]


# --- тип слайда в ранге --------------------------------------------------------


def _list_section(kind=None) -> ContentSection:
    items = ("переполнение текста", "заслонение картинкой", "порядок чтения")
    block = ContentBlock(id="b01", kind="list", items=items, text=" ".join(items))
    return ContentSection(id="s", heading="Что проверяем", blocks=(block,), kind=kind)


DERIVED = ("cards", "bullets", "two_column", "text")     # три коротких пункта


@pytest.mark.parametrize("kind, wanted", [
    (None, DERIVED),                                          # путь без модели — как было
    ("cards", DERIVED),                                       # тип модели уже первый
    ("bullets", ("bullets", "cards", "two_column", "text")),  # среди пригодных — первым
    ("table", DERIVED),                                       # таблицы в содержании нет
    ("metric", DERIVED),                                      # плашки под число нет
    ("agenda", ("agenda",) + DERIVED),                        # оглавление собирает код
])
def test_model_kind_leads_only_within_what_content_can_fill(kind, wanted):
    assert preferred_kinds(_list_section(kind)) == wanted


def _match(pid: str, kind: str, score: float) -> Match:
    return Match(pattern_id=pid, kind=kind, fills=(), score=score, reason="r", leftover=())


def test_agenda_layout_is_spared_only_for_the_ordered_agenda():
    matches = [_match("p01", "agenda", 1.0), _match("p02", "text", 1.0)]
    assert _positional(matches, False, False)[0].pattern_id == "p02"
    assert _positional(matches, False, False, agenda=True)[0].pattern_id == "p01"


def _deck_from(answer, template) -> tuple[list[str], list[str]]:
    """План колоды из ответа: (разделы-источники слайдов, предупреждения)."""
    doc, _, _ = outline.to_doc(answer, TEXT, "t.md", "t")
    a = analyze_template(template)
    plan = plan_deck(doc, a.patterns, a.design_system.source.sha256)
    return [s.origin_section for s in plan.slides], list(plan.warnings)


def test_agenda_without_its_layout_is_dropped_and_said(multi_template_path):
    """Растр Ш3: на карточках пункты оглавления читаются как иерархия. Нет макета
    оглавления — оглавления нет, и предупреждение это называет."""
    sections, warnings = _deck_from(_answer(), multi_template_path)
    assert "m02" not in sections and "m03" in sections
    assert any("Оглавление, заказанное моделью (пунктов: 4)" in w and "нет макета оглавления" in w
               for w in warnings)


def test_agenda_that_leaves_empty_places_is_dropped(agenda_template):
    """Три пункта на макете с четырьмя местами — пустое место (у WorkSpace это
    пустая номерная плашка), и оглавления нет."""
    answer = _answer()
    del answer["slides"][3]
    sections, warnings = _deck_from(answer, agenda_template)
    assert "m02" not in sections
    assert any("без пустых мест" in w for w in warnings)


def test_agenda_goes_only_to_its_layout(agenda_template, monkeypatch):
    """Макет оглавления принял его целиком — встаёт он, даже если обычная
    раскладка выигрывает по очкам."""
    from mimeo.model import Fill
    from mimeo.plan import deterministic

    patterns = analyze_template(agenda_template).patterns.patterns
    agenda = next(p for p in patterns if p.kind == "agenda")
    cards = next(p for p in patterns if p.kind == "cards")
    fills = tuple(Fill(slot_id=s.id, kind="text", text="x") for s in agenda.slots)
    fake = [Match(pattern_id=cards.id, kind="cards", fills=(), score=3.0, reason="r", leftover=()),
            Match(pattern_id=agenda.id, kind="agenda", fills=fills, score=1.0, reason="r", leftover=())]
    monkeypatch.setattr(deterministic, "rank", lambda *a, **k: list(fake))
    placed, parts = deterministic._place(_list_section("agenda"), patterns, False, False)
    assert [m.pattern_id for _, m in placed] == [agenda.id] and parts == 1


def test_ordered_agenda_keeps_its_kind_when_split():
    parts = split(_list_section("agenda"), 2)
    assert len(parts) == 2 and {p.kind for p in parts} == {"agenda"}


@pytest.fixture(scope="module")
def two_agenda_template(tmp_path_factory) -> str:
    """Два макета оглавления — на четыре пункта и на два: половинки оглавления
    встают на второй целиком, и добор мог бы их наделать."""
    items = "".join(_text_sp(20 + i, f"I{i}", 838200 + i * 2700000, 2286000, 2400000,
                             1500000, 1800, f"Пункт {i + 1}") for i in range(2))
    small = _fixture_slide(_text_sp(2, "T", 838200, 457200, 10515600, 1325563, 4400,
                                    "Содержание") + items)
    slides = EXTRA_SLIDES[:6] + [_agenda_slide(), small] + EXTRA_SLIDES[6:]
    return build_multi(tmp_path_factory.mktemp("agenda2") / "agenda2.pptx", slides)


@pytest.mark.parametrize("kind, forced", [(None, {"a": 2}), ("agenda", {})])
def test_volume_top_up_does_not_split_the_agenda(kind, forced, two_agenda_template):
    """Добор объёма делит самый крупный раздел — у колоды модели это часто
    оглавление; два слайда «Содержание» подряд хуже недобора. Здесь половинки
    встали бы на макет оглавления на два пункта целиком, и без типа тот же
    раздел добор делит — значит, держит именно тип."""
    patterns = analyze_template(two_agenda_template).patterns.patterns
    items = tuple(f"Пункт {i}" for i in range(4))
    agenda = ContentSection(id="a", heading="Содержание", kind=kind,
                            blocks=(ContentBlock(id="b1", kind="list", items=items, text=""),))
    small = ContentSection(id="s", heading="Раздел", blocks=(
        ContentBlock(id="b2", kind="list", items=("один", "два"), text=""),))
    assert _forced_parts([agenda, small], patterns, (6, 8)) == forced


# --- проверки видят только то, что встанет на слайд ---------------------------


def test_number_only_on_the_cover_is_lost():
    answer = _answer()
    answer["slides"][0]["theses"] = ["от 5 до 49 штук"]
    answer["slides"][3]["theses"] = ["Раскладок в живых шаблонах много"]
    failed = {c.name for c in outline.check_outline(TEXT, answer, "improve", 15) if not c.ok}
    assert "числа" in failed


def test_cover_subtitle_is_not_judged():
    """Подзаголовок обложки на слайд не идёт — и дословностью «оставить» не
    судится: «Презентация для инвесторов» не повод отвергнуть колоду."""
    answer = _answer()
    answer["slides"][0]["theses"] = ["Презентация для инвесторов"]
    assert outline.accepted(outline.check_outline(TEXT, answer, "keep", 15))


def test_image_only_on_the_cover_is_not_placed():
    answer = _answer()
    answer["slides"][0]["images"] = answer["slides"][4]["images"]
    answer["slides"][4]["images"] = []
    failed = {c.name for c in outline.check_outline(TEXT, answer, "keep", 15) if not c.ok}
    assert failed == {"картинки"}


# --- запрос -------------------------------------------------------------------


def test_request_carries_mode_frames_and_names():
    req = outline.request(TEXT, "improve", (8, 12))
    assert "(от 8 до 12;" in req.system and outline._THESES["improve"] in req.system
    assert req.user == TEXT
    body = client.chat_body(req, client.ClientConfig())
    assert body["response_format"]["json_schema"]["name"] == "deck"
    tool = client.chat_body(outline.request(TEXT, "keep", (10, 15), Mode.TOOL_CALL), client.ClientConfig())
    assert tool["tools"][0]["function"]["name"] == tool["tool_choice"]["function"]["name"] == "build_deck"
    free = outline.request(TEXT, "keep", (10, 15), Mode.FREE_TEXT)
    assert free.user.startswith(TEXT) and free.user.endswith(json.dumps(outline.RESPONSE_SCHEMA, ensure_ascii=False))


def test_flags_reach_the_request(prose, tmp_path):
    """`--slides` — рамки промпта, `--text` — блок тезисов режима."""
    with FakeModel(answer=_answer()) as fake:
        got = outline.run(prose, load_content(prose), text_mode="improve", target=(8, 12),
                          config=_config(tmp_path, fake.base_url))
    system = fake.requests[0]["messages"][0]["content"]
    assert system == outline.system_prompt("improve", 8, 12)
    assert got.record["frames"] == [8, 12] and got.record["frames_source"] == "--slides"


def test_chars_per_token_comes_from_config(tmp_path):
    """Порог — свойство токенизатора: число из `config/model.json`, а без файла —
    то же встроенное."""
    assert client.load_config().endpoint.chars_per_token == client.Endpoint().chars_per_token
    path = tmp_path / "model.json"
    path.write_text(json.dumps({"endpoint": {"chars_per_token": 3.0}}), encoding="utf-8")
    assert client.load_config(str(path)).endpoint.chars_per_token == 3.0


def test_threshold_is_the_context_minus_the_answer():
    """8192 − 4000 токенов при 2.3 знака на токен: промпт 1767 знаков оставляет
    тексту 7874 — порог ровно на границе (`config/model.json`)."""
    ep = client.Endpoint()
    system = "с" * 1767
    assert outline.too_long(system, "т" * 7874, ep) is None
    reason = outline.too_long(system, "т" * 7875, ep)
    assert reason and "7875 знаков длиннее порога 7874" in reason


def test_tz_frames_are_the_cli_ones():
    assert cli.parse_slides(cli.TZ_SLIDES) == outline.TZ_FRAMES


def test_empty_doc_is_markup():
    got = outline.run("c.md", ContentDoc(name="c.md"))
    assert got.status == "markup" and got.doc.notes[-1].startswith("Модель не нужна")
