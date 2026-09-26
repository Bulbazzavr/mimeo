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


def test_model_off_asks_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(donor, "donor_pictures", lambda *a, **k: {(0, "7"): b"\x89PNG"})
    with FakeModel() as fake:
        judge = donor.Judge(donor.load_config(), _config(fake, client.Access.OFF, tmp_path), "t.pptx")
        plan = judge.mark(_plan(), library=None)
    assert fake.requests == [] and plan.slides[0].dropped_pictures == () and judge.note() is None
    assert "dropped_pictures" not in plan.slides[0].to_json(), "план без модели не меняется ни ключом"
