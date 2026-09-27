"""Пиктограммы на месте значков шаблона (`Z-32`, `ADR-0028`).

Модель выбирает иконку по смыслу пункта (`plan/icons.py`), сборка ставит её
нативной фигурой цветом значка донора (`compose/icon.py`). Живой модели не
нужно: поддельный сервер отвечает выбором по заказу.
"""

from __future__ import annotations

import json
import os
import struct
import zlib
from xml.etree import ElementTree as ET

from mimeo.compose.icon import assets_root, icon_shape, load_icon, parse_path, replace_with_icon
from mimeo.model import DeckPlan, PlannedSlide, PlanSource
from mimeo.oxml.ns import NS, qn
from mimeo.plan import client, icons
from tests.fake_model import FakeModel


def _rgba_png(pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    height, width = len(pixels), len(pixels[0])
    raw = b"".join(b"\x00" + bytes(v for px in row for v in px) for row in pixels)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_every_listed_icon_has_a_file_that_parses_inside_its_field():
    """Словарь конфига и набор не расходятся: модель не выберет иконку, которой
    нет, а каждая иконка набора разбирается в контур внутри поля 24 × 24."""
    with open(icons.config_path(), encoding="utf-8") as fh:
        listed = json.load(fh)["icons"]
    cfg = icons.load_config()
    assert cfg.problem is None
    assert set(cfg.icons) == set(listed), "у каждой иконки словаря есть файл"
    for name in listed:
        side, cmds = load_icon(name)
        coords = [v for c in cmds for v in c[1:]]
        assert coords and -0.5 <= min(coords) and max(coords) <= side + 0.5, name
    assert os.path.isfile(os.path.join(assets_root(), "tabler", "LICENSE")), "MIT требует текст лицензии"


def test_arc_ends_where_svg_says_and_relative_commands_accumulate():
    """Дуга становится кривыми и кончается в своей точке; относительные
    команды считаются от текущей точки, `h`/`v` — полными линиями."""
    cmds = parse_path("M4 12a8 8 0 1 0 16 0h2v3l-1 1z")
    assert cmds[0] == ("M", 4.0, 12.0)
    arcs = [c for c in cmds if c[0] == "C"]
    assert len(arcs) == 2 and arcs[-1][-2:] == (20.0, 12.0), "полуокружность — две четверти"
    assert ("L", 22.0, 12.0) in cmds and ("L", 22.0, 15.0) in cmds and ("L", 21.0, 16.0) in cmds
    assert cmds[-1] == ("Z",)
    # Слитые флаги дуги: «a1 1 0 011 1» — флаги 0 и 1, конец (1, 1).
    fused = parse_path("M0 0a1 1 0 011 1")
    assert fused[-1][-2:] == (1.0, 1.0)


def test_shape_is_native_outline_in_donor_colour_or_theme_accent():
    shape = icon_shape("7", "rocket", (0, 0, 400, 200), "#0077ff")
    assert shape.tag == qn("p:sp")
    ext = shape.find(f"{qn('p:spPr')}/{qn('a:xfrm')}/{qn('a:ext')}")
    off = shape.find(f"{qn('p:spPr')}/{qn('a:xfrm')}/{qn('a:off')}")
    assert (ext.get("cx"), ext.get("cy"), off.get("x")) == ("200", "200", "100"), "квадрат по центру рамки"
    assert shape.find(f".//{qn('a:custGeom')}/{qn('a:pathLst')}/{qn('a:path')}").get("fill") == "none"
    assert shape.find(f".//{qn('a:ln')}//{qn('a:srgbClr')}").get("val") == "0077FF"
    themed = icon_shape("7", "rocket", (0, 0, 200, 200), None)
    assert themed.find(f".//{qn('a:ln')}//{qn('a:schemeClr')}").get("val") == "accent1"
    assert icon_shape("7", "нет-такой", (0, 0, 200, 200), None) is None


def test_picture_is_replaced_in_place():
    """Пиктограмма встаёт на место картинки в порядке фигур и берёт её id."""
    tree = ET.fromstring(
        f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}">'
        '<p:cSld><p:spTree><p:sp/>'
        '<p:pic><p:nvPicPr><p:cNvPr id="9" name="Иконка"/></p:nvPicPr>'
        '<p:spPr><a:xfrm><a:off x="10" y="20"/><a:ext cx="300" cy="300"/></a:xfrm></p:spPr></p:pic>'
        '<p:sp/></p:spTree></p:cSld></p:sld>')
    pic = tree.find(f".//{qn('p:pic')}")
    assert replace_with_icon(tree, pic, "bulb", "#FFFFFF") is None
    shapes = list(tree.find(f".//{qn('p:spTree')}"))
    assert [s.tag for s in shapes] == [qn("p:sp")] * 3
    assert shapes[1].find(f".//{qn('p:cNvPr')}").get("id") == "9"


def test_colour_is_the_most_common_opaque_one():
    """Сглаженный край и прозрачный фон цвет значка не уводят."""
    blue, edge, clear = (0, 119, 255, 255), (120, 180, 255, 140), (0, 0, 0, 0)
    png = _rgba_png([[blue, blue, edge], [blue, clear, clear], [edge, clear, clear]])
    assert icons.dominant_color(png) == "#0077FF"
    assert icons.dominant_color(b"not a png") is None


def _plan() -> DeckPlan:
    return DeckPlan(source=PlanSource("t.md", "0" * 64, "0" * 64, ()),
                    slides=(PlannedSlide(index=0, pattern_id="p01", fills=(), reason="тест"),))


def _config(fake, tmp_path, access=client.Access.ON) -> client.ClientConfig:
    return client.ClientConfig(access=access, extra_body={},
                               endpoint=client.Endpoint(base_url=fake.base_url, timeout_sec=10, budget_sec=20),
                               cache_root=str(tmp_path / "llm"), tz_cache_root=str(tmp_path / "tz"))


def test_model_picks_one_icon_per_item_in_one_request(tmp_path, monkeypatch):
    found = [icons.Spot(0, "7", "Выгрузка в pdf", "Форматы", "#FFFFFF"),
             icons.Spot(0, "8", "Команда из трёх человек", "Форматы", None)]
    monkeypatch.setattr(icons, "spots", lambda *a, **k: found)
    answer = "текст:" + json.dumps({"icons": ["file-export", "users"]})
    with FakeModel(answer) as fake:
        picker = icons.Picker(icons.load_config(), _config(fake, tmp_path), "t.pptx")
        plan = picker.mark(_plan(), library=None)
        again = picker.mark(_plan(), library=None)
    placed = plan.slides[0].icons
    assert [(i.shape_id, i.name, i.color) for i in placed] == [("7", "file-export", "#FFFFFF"),
                                                              ("8", "users", None)]
    assert again.slides[0].icons == placed
    assert len(fake.requests) == 1, "пункты — одним запросом, второй вариант — из памяти выбора"
    assert plan.slides[0].to_json()["icons"][0] == {"shape_id": "7", "name": "file-export",
                                                    "color": "#FFFFFF"}
    assert "Пиктограммы" in picker.note()


def test_wrong_answer_and_model_off_leave_template_icons(tmp_path, monkeypatch):
    found = [icons.Spot(0, "7", "Выгрузка в pdf", "", None)]
    monkeypatch.setattr(icons, "spots", lambda *a, **k: found)
    with FakeModel("текст:" + json.dumps({"icons": ["нет-такой"]})) as fake:
        picker = icons.Picker(icons.load_config(), _config(fake, tmp_path), "t.pptx")
        assert picker.mark(_plan(), library=None).slides[0].icons == ()
        assert "не по форме" in picker.note()
        off = icons.Picker(icons.load_config(), _config(fake, tmp_path, client.Access.OFF), "t.pptx")
        assert off.mark(_plan(), library=None).slides[0].icons == ()
        assert off.note() is None


def test_missing_config_is_said_not_silently_replaced(tmp_path):
    cfg = icons.load_config(str(tmp_path / "icons.json"))
    assert cfg.problem and "icons.json" in cfg.problem
