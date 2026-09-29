"""Пустые карточки уходят со слайда, оставшиеся раздвигаются (просьба пользователя 29.09)."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from mimeo.compose.cards import drop_empty_cards
from mimeo.oxml.ns import NS, qn

_A, _P = NS["a"], NS["p"]


def _sp(sid: int, x: int, y: int, cx: int, cy: int, *, fill: bool = False, text: str | None = None) -> str:
    paint = "<a:solidFill><a:srgbClr val=\"FFFFFF\"/></a:solidFill>" if fill else "<a:noFill/>"
    body = (f"<p:txBody><a:bodyPr/><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody>"
            if text is not None else "")
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="s{sid}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="roundRect"><a:avLst/></a:prstGeom>{paint}</p:spPr>{body}</p:sp>')


def _slide(cards: list[str | None], filled_text: bool = True) -> ET.Element:
    """Ряд карточек: подложка 200×300, маркер и место под текст внутри, шаг 250."""
    shapes = []
    for n, text in enumerate(cards):
        x = n * 250
        shapes.append(_sp(100 + n * 10, x, 0, 200, 300, fill=True))
        shapes.append(_sp(101 + n * 10, x + 10, 10, 8, 8, fill=True))
        shapes.append(_sp(102 + n * 10, x + 10, 30, 180, 200, text=text or ""))
    return ET.fromstring(f'<p:sld xmlns:a="{_A}" xmlns:p="{_P}"><p:cSld><p:spTree>'
                         + "".join(shapes) + "</p:spTree></p:cSld></p:sld>")


def _boxes(tree: ET.Element) -> list[tuple[int, int]]:
    out = []
    for sp in tree.iter(qn("p:sp")):
        if sp.find(qn("p:nvSpPr")).find(qn("p:cNvPr")).get("id").endswith("0"):
            off = sp.find(f"{qn('p:spPr')}/{qn('a:xfrm')}/{qn('a:off')}")
            ext = sp.find(f"{qn('p:spPr')}/{qn('a:xfrm')}/{qn('a:ext')}")
            out.append((int(off.get("x")), int(ext.get("cx"))))
    return out


def test_empty_card_goes_and_the_row_spreads() -> None:
    tree = _slide(["первый", "второй", "третий", None])
    slots = {"102", "112", "122", "132"}
    assert drop_empty_cards(tree, slots, {"102", "112", "122"}) == 1
    # Ряд был 0…950 с зазором 50: три карточки по (950 − 100) / 3.
    assert _boxes(tree) == [(0, 283), (333, 283), (666, 283)]
    texts = [t.text for t in tree.iter(qn("a:t"))]
    assert texts == ["первый", "второй", "третий"]


def test_full_row_and_lone_plate_stay() -> None:
    tree = _slide(["первый", "второй"])
    assert drop_empty_cards(tree, {"102", "112"}, {"102", "112"}) == 0
    # Одиночная пустая плашка — не ряд карточек: её не трогаем.
    lone = _slide([None])
    assert drop_empty_cards(lone, {"102"}, set()) == 0
    assert len(list(lone.iter(qn("p:sp")))) == 3
