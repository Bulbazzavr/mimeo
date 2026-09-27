"""Картинки донора с чужим содержимым (`Z-62`, `mimeo/plan/donor.py`).

Зрение модели решает, содержимое ли картинка донора — диаграмма, снимок
экрана, надпись — или оформление; содержимое план помечает, сборка убирает.
Живой модели не нужно: поддельный сервер отвечает приговором по заказу.
"""

from __future__ import annotations

import base64
import json
import struct
import zlib

from mimeo.model import DeckPlan, PlannedSlide, PlanSource
from mimeo.plan import client, donor
from tests.fake_model import FakeModel


def _rgba_png(pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    height, width = len(pixels), len(pixels[0])
    raw = b"".join(b"\x00" + bytes(v for px in row for v in px) for row in pixels)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _rgb_rows(png: bytes) -> list[bytes]:
    """Строки RGB из PNG, который пишет `flatten` (фильтр 0)."""
    i, idat, width = 8, b"", 0
    while i < len(png):
        length, kind = struct.unpack(">I4s", png[i:i + 8])
        body = png[i + 8:i + 8 + length]
        if kind == b"IHDR":
            width = struct.unpack(">I", body[:4])[0]
        elif kind == b"IDAT":
            idat += body
        i += 12 + length
    raw = zlib.decompress(idat)
    stride = 1 + width * 3
    return [raw[r * stride + 1:(r + 1) * stride] for r in range(len(raw) // stride)]


def test_transparent_picture_is_laid_on_grey():
    """Сервер модели прозрачность отбрасывает, и чёрный текст на прозрачном
    становится чёрным на чёрном: «VK WorkSpace» модель не видела. На сером —
    видит."""
    black, clear = (0, 0, 0, 255), (0, 0, 0, 0)
    rows = _rgb_rows(donor.flatten(_rgba_png([[black, clear], [clear, black]])))
    assert rows == [bytes([0, 0, 0, 128, 128, 128]), bytes([128, 128, 128, 0, 0, 0])]


def test_opaque_and_foreign_formats_are_left_as_they_are():
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 20
    assert donor.flatten(jpeg) == jpeg


def _plan() -> DeckPlan:
    return DeckPlan(source=PlanSource("t.md", "0" * 64, "0" * 64, ()),
                    slides=(PlannedSlide(index=0, pattern_id="p01", fills=(), reason="тест"),))


def _config(fake, access=client.Access.ON, tmp_path=None) -> client.ClientConfig:
    return client.ClientConfig(access=access, extra_body={},
                               endpoint=client.Endpoint(base_url=fake.base_url, timeout_sec=10, budget_sec=20),
                               cache_root=str(tmp_path / "llm"), tz_cache_root=str(tmp_path / "tz"))


def test_content_picture_is_marked_and_decor_is_kept(tmp_path, monkeypatch):
    chart = _rgba_png([[(255, 0, 0, 255)]])
    frame = _rgba_png([[(0, 0, 255, 255)]])
    monkeypatch.setattr(donor, "donor_pictures",
                        lambda plan, library, template, cfg, size=None: {(0, "7"): chart, (0, "8"): frame})

    def verdict(request):
        url = request["messages"][1]["content"][0]["image_url"]["url"]
        data = base64.b64decode(url.split(",", 1)[1])
        answer = "content" if donor.flatten(chart) == data else "decor"
        return "текст:" + json.dumps({"verdict": answer, "what": "проба"})

    with FakeModel(verdict) as fake:
        judge = donor.Judge(donor.load_config(), _config(fake, tmp_path=tmp_path), "t.pptx")
        plan = judge.mark(_plan(), library=None)
    assert plan.slides[0].dropped_pictures == ("7",)
    assert plan.slides[0].to_json()["dropped_pictures"] == ["7"]
    assert len(fake.requests) == 2, "каждая картинка — один вопрос"
    assert "содержимое — 1" in judge.note()


def _three_pictures(monkeypatch) -> None:
    pictures = {(0, str(n)): _rgba_png([[(n, 0, 0, 255)]]) for n in (7, 8, 9)}
    monkeypatch.setattr(donor, "donor_pictures", lambda *a, **k: pictures)


def _dead_server(monkeypatch) -> list[str]:
    """Сервер лежит: каждый вызов транспорта — отказ сети, и он же считается."""
    calls: list[str] = []

    def dead(url, body, timeout):
        calls.append(url)
        raise client.ModelError("сеть", "нет связи с сервером")

    monkeypatch.setattr(client, "post_chat", dead)
    return calls


def _dead_config(tmp_path) -> client.ClientConfig:
    return client.ClientConfig(access=client.Access.ON, extra_body={},
                               endpoint=client.Endpoint(base_url="http://127.0.0.1:9/v1"),
                               cache_root=str(tmp_path / "llm"), tz_cache_root=str(tmp_path / "tz"))


def test_dead_server_is_asked_once_not_for_every_picture(tmp_path, monkeypatch):
    """Вычитка 26 сентября: новый клиент на каждую картинку — лежащий сервер
    спрашивался заново каждой (2 с на отказ), зависший держал каждую до
    таймаута. Клиент один на сборку и отказ помнит."""
    _three_pictures(monkeypatch)
    calls = _dead_server(monkeypatch)
    judge = donor.Judge(donor.load_config(), _dead_config(tmp_path), "t.pptx")
    plan = judge.mark(_plan(), library=None)
    assert len(calls) == 1, f"три картинки — один стук в лежащий сервер, а было {len(calls)}"
    assert plan.slides[0].dropped_pictures == (), "модель не ответила — картинки остаются"
    assert "Модель не ответила" in judge.note()


def test_outage_on_the_deck_spares_the_pictures(tmp_path, monkeypatch):
    """Отказ общий на сборку: сервер лёг на запросе колоды — зрение о картинках
    донора не стучится вовсе (`client.Outage`)."""
    from mimeo.plan.prompt import Mode, Request

    _three_pictures(monkeypatch)
    calls = _dead_server(monkeypatch)
    outage = client.Outage()
    config = _dead_config(tmp_path)
    deck = client.ModelClient(config, outage=outage).complete(Request(
        mode=Mode.JSON_SCHEMA, system="s", user="u", schema={"type": "object"},
        section_id="колода", candidates=()))
    assert not deck and len(calls) == 1
    judge = donor.Judge(donor.load_config(), config, "t.pptx", outage=outage)
    judge.mark(_plan(), library=None)
    assert len(calls) == 1, "после отказа на колоде картинки не спрашивают сервер заново"
    assert "модель уже отказала" in judge.note()


def test_model_off_asks_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(donor, "donor_pictures", lambda *a, **k: {(0, "7"): b"\x89PNG"})
    with FakeModel() as fake:
        judge = donor.Judge(donor.load_config(), _config(fake, client.Access.OFF, tmp_path), "t.pptx")
        plan = judge.mark(_plan(), library=None)
    assert fake.requests == [] and plan.slides[0].dropped_pictures == () and judge.note() is None
    assert "dropped_pictures" not in plan.slides[0].to_json(), "план без модели не меняется ни ключом"
