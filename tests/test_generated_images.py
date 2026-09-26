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


def _gen(tmp_path, base_url="http://127.0.0.1:9", **kw) -> images.GeneratorConfig:
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
        assert got == good["scenes"] and how == "от модели"
        assert json.loads(fake.requests[0]["messages"][1]["content"]) == ideas
    with FakeModel(answer={"scenes": ["одна"]}) as fake:
        got, how = images.scenes(ideas[:1] + ["другая"], _gen(tmp_path), replace(
            config, endpoint=client.Endpoint(base_url=fake.base_url)))
        assert got is None and "не по форме" in how

    with FakeSD() as sd:
        doc, wanted, note = images.add_placeholders(
            _doc(ideas=3), str(tmp_path), _gen(tmp_path, sd.base_url),
            rewrite=lambda xs: ([f"Сцена {i}" for i, _ in enumerate(xs)], "от модели"))
    assert list(wanted.values()) == ["Сцена 0", "Сцена 1", "Сцена 2"]
    assert note.startswith("Сюжеты картинок переписаны моделью без текста (от модели)")


def test_deck_without_model_gets_no_pictures(tmp_path):
    doc = _doc(ideas=3)
    doc = ContentDoc(name=doc.name, sections=doc.sections, planner="deterministic")
    assert images.add_placeholders(doc, str(tmp_path), _gen(tmp_path))[1] == {}


def test_access_variable_overrides_the_config(monkeypatch):
    monkeypatch.setenv("MIMEO_IMAGES_ACCESS", "off")
    assert images.load_config().access == "off"
    monkeypatch.delenv("MIMEO_IMAGES_ACCESS")
    shipped = images.load_config()
    assert shipped.loaded and shipped.access == "on" and shipped.model == images.GeneratorConfig().model


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

        again, report = _build(monkeypatch, tmp_path, fake, sd.base_url, name="again")
        assert len(sd.requests) == 1, "повторная сборка — из кэша, генератор не зовётся"
        assert any("из кэша 1" in w for w in report["diagnostics"]["warnings"])


@pytest.mark.skipif(not os.path.exists(VK), reason="материалы ТЗ не коммитятся")
def test_generator_down_builds_without_the_picture_and_says_so(monkeypatch, tmp_path):
    with FakeModel(answer=_answer()) as fake:
        out, report = _build(monkeypatch, tmp_path, fake, "http://127.0.0.1:9")
    assert not os.path.exists(out / "images")
    assert any("генератор картинок не отвечает" in w for w in report["diagnostics"]["warnings"])
