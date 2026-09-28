"""Картинки по идеям модели (`Z-28`, `mimeo/plan/images.py`).

Живой модели и живого генератора не нужно: модель — `tests/fake_model.py`,
генератор — заглушка `sd-server` ниже, отвечающая PNG заказанного размера.
Проверяется главное: картинка встаёт в место под иллюстрацию и нарисована
в его пропорции; генератор не отвечает — колода собирается без картинки и
говорит почему; повторная сборка берёт картинку из кэша.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import threading
import zipfile
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mimeo import cli
from mimeo.plan import client, images
from mimeo.plan.content import ContentBlock, ContentDoc, ContentSection
from mimeo.plan.imagesize import image_size
from tests.fake_model import FakeModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VK = os.path.join(ROOT, "tz", "templates", "2_Датасет VK Tech шаблон.pptx")


def _png(width: int, height: int) -> bytes:
    """Серый PNG заказанного размера — ровно столько, сколько нужно проверке."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + b"\x80" * (3 * width) for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))


class FakeSD:
    """Заглушка `sd-server`: `/sdapi/v1/options` и `/sdapi/v1/txt2img`."""

    def __init__(self):
        fake = self
        self.requests: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):              # noqa: N802
                self._send({"samples_format": "png"})

            def do_POST(self):             # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                fake.requests.append(body)
                png = _png(body["width"], body["height"])
                self._send({"images": [base64.b64encode(png).decode("ascii")], "info": ""})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()


#: Тексты для моделей — только из `config/generator.json` (`Z-72`). Читаются
#: при импорте: тесты подменяют `images.load_config` на `_gen`.
_SHIPPED = images.load_config()


def _gen(tmp_path, base_url="http://127.0.0.1:9", **kw) -> images.GeneratorConfig:
    kw = {"scene_system": _SHIPPED.scene_system, "prompt_suffix": _SHIPPED.prompt_suffix,
          "check_system": _SHIPPED.check_system, "check_question": _SHIPPED.check_question, **kw}
    return images.GeneratorConfig(base_url=base_url, cache_root=str(tmp_path / "img-cache"),
                                  llm_sleep_wait_sec=0, **kw)


def _doc(ideas: int, kind="bullets") -> ContentDoc:
    sections = [ContentSection(id="m01", heading="Тема", blocks=(), kind="cover")]
    for i in range(2, 2 + 8):
        block = ContentBlock(id=f"b{i}", kind="paragraph", text=f"Тезис слайда {i}")
        idea = f"Сюжет {i}" if i - 2 < ideas else ""
        sections.append(ContentSection(id=f"m{i:02d}", heading=f"Слайд {i}", blocks=(block,),
                                       kind=kind, image_idea=idea))
    return ContentDoc(name="t.md", sections=sections, planner="mixed")


# --- размер и отбор ----------------------------------------------------------


@pytest.mark.parametrize("aspect, expected", [
    (1.0, (1024, 1024)), (4 / 3, (1152, 896)), (0.43, (640, 1536)), (0.1, (512, 1984)),
])
def test_size_follows_the_place_and_stays_near_a_megapixel(aspect, expected, tmp_path):
    width, height = images.size_for(aspect, _gen(tmp_path))
    assert (width, height) == expected
    assert width % 64 == 0 and height % 64 == 0


def test_placeholders_follow_the_idea_rules(tmp_path):
    """Не больше одной идеи на три раздела, по порядку; файла заготовки нет —
    план ставит картинку в место под иллюстрацию, не зная пропорции."""
    with FakeSD() as sd:
        doc, wanted, note = images.add_placeholders(_doc(ideas=5), str(tmp_path), _gen(tmp_path, sd.base_url))
    assert note is None and len(wanted) == 9 // 3
    refs = [b.ref for s in doc.sections for b in s.blocks if b.kind == "image"]
    assert refs == list(wanted) and not any(os.path.exists(r) for r in refs)
    assert all(b.id.startswith(images.GENERATED_PREFIX) for s in doc.sections for b in s.blocks
               if b.kind == "image")


def test_no_generator_no_placeholders_and_said(tmp_path):
    doc, wanted, note = images.add_placeholders(_doc(ideas=2), str(tmp_path), _gen(tmp_path))
    assert wanted == {} and "генератор картинок не отвечает" in note
    off, wanted, note = images.add_placeholders(_doc(ideas=2), str(tmp_path), _gen(tmp_path, access="off"))
    assert wanted == {} and "генератор выключен" in note
    plain = _doc(ideas=0)
    assert images.add_placeholders(plain, str(tmp_path), _gen(tmp_path)) == (plain, {}, None)


def test_ideas_are_rewritten_into_scenes_without_text(tmp_path):
    """Сюжет с текстом переписывает модель; ответ не по форме — рисуется идея."""
    ideas = ["Человек двигает блоки текста на слайде", "Водитель читает сообщение на телефоне"]
    good = {"scenes": ["Человек переставляет деревянные кубики", "Водитель у грузовика на рассвете"]}
    config = client.ClientConfig(access=client.Access.ON, extra_body={},
                                 cache_root=str(tmp_path / "llm"), tz_cache_root=str(tmp_path / "tz"))
    from dataclasses import replace
    with FakeModel(answer=good) as fake:
        got, how = images.scenes(ideas, _gen(tmp_path), replace(config, endpoint=client.Endpoint(
            base_url=fake.base_url)))
        # Стиль модель не выбрала — к сцене дописан стиль по умолчанию из конфига.
        default = _SHIPPED.prompt_suffix.strip(" .")
        assert got == [f"{s}. {default}." for s in good["scenes"]] and how == "от модели"
        assert json.loads(fake.requests[0]["messages"][1]["content"]) == ideas
    with FakeModel(answer={"scenes": ["одна"]}) as fake:
        got, how = images.scenes(ideas[:1] + ["другая"], _gen(tmp_path), replace(
            config, endpoint=client.Endpoint(base_url=fake.base_url)))
        assert got is None and "не по форме" in how



def test_scene_translation_reaches_the_picture(tmp_path):
    """Сцена для генератора — по-английски, её перевод модель даёт тем же
    ответом, и он доходит до готового файла: человек видит обе версии. Без
    годного перевода сцены всё равно берутся."""
    ideas = ["Человек двигает блоки текста на слайде"]
    answer = {"scenes": ["A man stacks wooden blocks on a desk"],
              "ru": ["Мужчина складывает деревянные кубики на столе"]}
    config = client.ClientConfig(access=client.Access.ON, extra_body={},
                                 cache_root=str(tmp_path / "llm"), tz_cache_root=str(tmp_path / "tz"))
    from dataclasses import replace
    translations: dict[str, str] = {}
    with FakeModel(answer=answer) as fake:
        got, _how = images.scenes(ideas, _gen(tmp_path), replace(config, endpoint=client.Endpoint(
            base_url=fake.base_url)), translations=translations)
    assert [g.split(".")[0] for g in got] == answer["scenes"]
    assert translations == dict(zip(got, answer["ru"]))

    painter = images.Painter(_gen(tmp_path), {}, translations=translations)
    target = str(tmp_path / "out" / "p.png")
    prompt = got[0].rstrip(" .") + painter.gen.prompt_suffix    # художник не знает, что сцена со стилем
    cached = painter._cache_path(painter._key(prompt, 64, 64))
    os.makedirs(os.path.dirname(cached))
    with open(cached, "wb") as fh:
        fh.write(_png(64, 64))
    assert painter.picture(got[0], 64, 64, target)
    assert painter.prompts[target] == prompt and painter.ru[target] == answer["ru"][0]

    with FakeModel(answer={"scenes": answer["scenes"], "ru": []}) as fake:
        lost: dict[str, str] = {}
        got, _how = images.scenes(ideas, _gen(tmp_path), replace(config, cache_root=str(tmp_path / "llm2"),
                                  endpoint=client.Endpoint(base_url=fake.base_url)), translations=lost)
    assert [g.split(".")[0] for g in got] == answer["scenes"] and lost == {}


def test_scene_request_carries_the_whole_deck(tmp_path):
    """Сцену модель пишет, видя всю презентацию — заголовки и тезисы всех
    слайдов по порядку — и номер слайда каждой картинки, а не одну фразу идеи."""
    doc = _doc(ideas=3)
    picked = [s for s in doc.sections if s.image_idea][:2]
    config = client.ClientConfig(access=client.Access.ON, extra_body={},
                                 cache_root=str(tmp_path / "llm"), tz_cache_root=str(tmp_path / "tz"))
    from dataclasses import replace
    with FakeModel(answer={"style": "Flat vector illustration", "ru": ["Сцена А", "Сцена Б"],
                           "scenes": ["Scene A", "Scene B"]}) as fake:
        got, _how = images.scenes([s.image_idea for s in picked], _gen(tmp_path), replace(
            config, endpoint=client.Endpoint(base_url=fake.base_url)), deck=doc, sections=picked,
            taken=("A carpenter planes a board",))
        sent = json.loads(fake.requests[0]["messages"][1]["content"])
        schema = fake.requests[0]["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["scenes"]["maxItems"] == 2 == schema["properties"]["scenes"]["minItems"], (
        "сцен ровно столько, сколько картинок, — схемой: иначе модель пишет на все слайды колоды")
    assert got == ["Scene A. Flat vector illustration.", "Scene B. Flat vector illustration."], (
        "стиль колоды выбирает модель, код дописывает его к каждой сцене")
    assert sent["taken"] == ["A carpenter planes a board"], "написанное раньше модель видит и не повторяет"
    assert [p["heading"] for p in sent["presentation"]] == [s.heading for s in doc.sections]
    assert sent["presentation"][1]["theses"] == ["Тезис слайда 2"]
    assert sent["pictures"] == [{"slide": 2, "idea": "Сюжет 2"}, {"slide": 3, "idea": "Сюжет 3"}]


def test_picture_with_letters_is_redrawn_and_then_dropped(tmp_path):
    """Зрение нашло буквы — картинка перерисовывается с другим зерном; брак
    на всех попытках — картинки нет, и сказано почему. Зрение не ответило —
    не брак."""
    with FakeSD() as sd:
        verdicts = iter(["буквы или надписи", None])
        painter = images.Painter(_gen(tmp_path, sd.base_url), {}, check=lambda png: next(verdicts))
        assert painter.picture("Сцена", 64, 64, str(tmp_path / "a.png"))
        assert [r["seed"] for r in sd.requests] == [42, 1042], "вторая попытка — другое зерно"
        assert painter.redrawn == 1 and os.path.isfile(tmp_path / "a.png")

        painter = images.Painter(_gen(tmp_path, sd.base_url), {}, check=lambda png: "буквы или надписи")
        assert not painter.picture("Другая сцена", 64, 64, str(tmp_path / "b.png"))
        assert not os.path.exists(tmp_path / "b.png")
        assert "Снято после 3 попыток — 1" in painter.note()

    config = client.ClientConfig(access=client.Access.ON, extra_body={},
                                 cache_root=str(tmp_path / "llm"), tz_cache_root=str(tmp_path / "tz"),
                                 endpoint=client.Endpoint(base_url="http://127.0.0.1:9"))
    assert images.picture_check(_gen(tmp_path), config)(_png(8, 8)) is None, "не ответило — не брак"


def test_deck_without_model_gets_no_pictures(tmp_path):
    doc = _doc(ideas=3)
    doc = ContentDoc(name=doc.name, sections=doc.sections, planner="deterministic")
    assert images.add_placeholders(doc, str(tmp_path), _gen(tmp_path))[1] == {}


def test_generated_picture_does_not_buy_a_layout():
    """Заготовка генератора — украшение: невставленная не штрафуется и в полноту
    не входит; вставленная даёт раскладке лишь малую премию; место мельче
    `min_side` её не принимает. Замер 26 сентября: со штрафом, как у картинки
    автора, ранг брал ради картинки тесную раскладку — 5 переполнений на VK Tech."""
    from dataclasses import replace

    from mimeo.model import Capacity, Pattern, Slot
    from mimeo.plan import matching
    from tests.test_images import ILLUSTRATION, _Rect

    def text(sid, y):
        return Slot(id=sid, role="body", content_type="text", rect=_Rect(0, y, 6000000, 800000),
                    type_role="body", required=True,
                    capacity=Capacity(max_chars=200, max_lines=4, chars_per_line=50, target_chars=120,
                                      max_items=None, donor_chars=None, basis="test"))

    def picture(side):
        return Slot(id="s09", role="image", content_type="image", rect=_Rect(6500000, 0, side, side),
                    type_role=None, capacity=None, required=False, picture_kind=ILLUSTRATION)

    def pattern(*slots):
        return Pattern(id="p01", kind="image_text", donor_part="/ppt/slides/slide1.xml", donor_index=1,
                       slots=(text("s00", 0), text("s01", 900000)) + slots, members=(1,),
                       cohesion=None, donor_reason="тест", source="test")

    body = ContentBlock(id="b1", kind="paragraph", text="Генераторы делают слайды по своим правилам")
    placeholder = ContentBlock(id=images.GENERATED_PREFIX + "sec", kind="image",
                               ref="out/images/sec.png", min_side=2000000)
    section = ContentSection(id="sec", heading="Генераторы не переносят стиль", blocks=(body, placeholder))

    no_place, big, small = pattern(), pattern(picture(3000000)), pattern(picture(1000000))
    placed, dropped = matching.match(section, big), matching.match(section, no_place)
    assert any(f.kind == "image" and f.slot_id == "s09" for f in placed.fills)
    assert dropped.dropped_images == ("out/images/sec.png",)
    # Встала — только премия; не встала — ни штрафа, ни потери полноты.
    assert round(placed.score - dropped.score, 4) == matching._BONUS_GENERATED_IMAGE

    author = replace(placeholder, id="b2", min_side=None)
    with_author = replace(section, blocks=(body, author))
    gap = matching.match(with_author, big).score - matching.match(with_author, no_place).score
    assert gap > matching._BONUS_GENERATED_IMAGE, "картинку автора ранг по-прежнему бережёт"

    assert not any(f.kind == "image" for f in matching.match(section, small).fills)


def test_access_variable_overrides_the_config(monkeypatch):
    monkeypatch.setenv("MIMEO_IMAGES_ACCESS", "off")
    assert images.load_config().access == "off"
    monkeypatch.delenv("MIMEO_IMAGES_ACCESS")
    shipped = images.load_config()
    # Умолчание с 28 сентября — off, решение пользователя «пока временно».
    assert shipped.loaded and shipped.access == "off" and shipped.model == images.GeneratorConfig().model


# --- сквозь сборку -------------------------------------------------------------


#: Проза, а не разметка: короче порога сегментатора текст считается
#: размеченным, и модель не зовётся вовсе (`outline.run`, статус `markup`).
TEXT = ("Расскажи правлению, как мы перевели рабочую связь складов в один канал. Раньше "
        "переписка жила в личных чатах, в почте и в звонках диспетчеру, и водители "
        "узнавали новый адрес с опозданием.\n\n"
        "Диспетчер теперь отвечает быстрее, и водители не теряют адреса: всё рабочее "
        "лежит в одном месте, с правами доступа и историей.\n\n"
        "Пилот шёл на одном складе, потом на всех. Главный урок пилота простой: работает "
        "не приложение, а правило, и старые чаты надо закрывать сразу.")


def _answer() -> dict:
    def slide(heading, kind, theses=(), idea=""):
        return {"heading": heading, "kind": kind, "role": "прочее", "theses": list(theses),
                "image_idea": idea, "images": []}
    return {"slides": [
        slide("Один канал связи", "cover"),
        slide("Связь складов собрана в один канал", "bullets",
              ["Мы перевели рабочую связь складов в один канал"],
              "Водитель у грузовика читает сообщение на телефоне"),
        slide("Диспетчер отвечает быстрее", "text", ["Диспетчер теперь отвечает быстрее"]),
        slide("Водители не теряют адреса", "text", ["водители не теряют адреса"]),
        slide("Пилот прошёл на одном складе", "text", ["Пилот шёл на одном складе, потом на всех"]),
    ], "missing_roles": []}


def _build(monkeypatch, tmp_path, fake, sd_url, name="out"):
    text = tmp_path / "text.md"
    text.write_text(TEXT, encoding="utf-8")
    config = client.ClientConfig(
        access=client.Access.ON, extra_body={},
        endpoint=client.Endpoint(base_url=fake.base_url, timeout_sec=10, budget_sec=20),
        cache_root=str(tmp_path / "llm-cache"), tz_cache_root=str(tmp_path / "tz-cache"))
    monkeypatch.setattr(cli, "load_model_config", lambda: config)
    monkeypatch.setattr(images, "load_config", lambda path=None: _gen(tmp_path, sd_url))
    out = tmp_path / name
    report = tmp_path / f"{name}.json"
    args = cli.build_parser().parse_args(
        ["build", VK, str(text), "-o", str(out), "--output", str(out / "deck.pptx"),
         "--report", str(report), "-q"])
    assert args.func(args) in (0, 2)
    with open(report, encoding="utf-8") as fh:
        return out, json.load(fh)


@pytest.mark.skipif(not os.path.exists(VK), reason="материалы ТЗ не коммитятся")
def test_picture_is_drawn_for_its_place_and_lands_in_the_deck(monkeypatch, tmp_path):
    with FakeModel(answer=_answer()) as fake, FakeSD() as sd:
        out, report = _build(monkeypatch, tmp_path, fake, sd.base_url)
        assert len(sd.requests) == 1
        asked = sd.requests[0]
        assert asked["prompt"].startswith("Водитель у грузовика читает сообщение на телефоне")
        assert asked["prompt"].endswith(images.GeneratorConfig().prompt_suffix)
        drawn = [os.path.join(out, "images", n) for n in os.listdir(out / "images") if "x" in n]
        assert len(drawn) == 1 and image_size(drawn[0]) == (asked["width"], asked["height"])
        with zipfile.ZipFile(out / "deck.pptx") as z:
            ours = [z.read(n) for n in z.namelist() if "/media/mimeo" in n]
        with open(drawn[0], "rb") as fh:
            assert fh.read() in ours, "нарисованная картинка — в колоде"
        assert any(w.startswith("Картинки по идеям модели") and "нарисовано 1" in w
                   for w in report["diagnostics"]["warnings"])
        # Промпт картинки — в отчёте, у своего слайда: веб показывает его человеку.
        pictures = report["decks"][0]["pictures"]
        assert [p["prompt"] for p in pictures] == [asked["prompt"]]
        assert pictures[0]["slide"] >= 2, "обложка идеи не получает"

        again, report = _build(monkeypatch, tmp_path, fake, sd.base_url, name="again")
        assert len(sd.requests) == 1, "повторная сборка — из кэша, генератор не зовётся"
        assert any("из кэша 1" in w for w in report["diagnostics"]["warnings"])
        assert report["decks"][0]["pictures"] == pictures, "картинка из кэша — с тем же промптом"


@pytest.mark.skipif(not os.path.exists(VK), reason="материалы ТЗ не коммитятся")
def test_generator_down_builds_without_the_picture_and_says_so(monkeypatch, tmp_path):
    with FakeModel(answer=_answer()) as fake:
        out, report = _build(monkeypatch, tmp_path, fake, "http://127.0.0.1:9")
    assert not os.path.exists(out / "images")
    assert any("генератор картинок не отвечает" in w for w in report["diagnostics"]["warnings"])
