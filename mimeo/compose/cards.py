"""Пустые карточки уходят со слайда, оставшиеся раздвигаются — просьба
пользователя 29 сентября.

Клон слайда несёт столько карточек, сколько их нарисовал дизайнер, а пунктов
у нас может быть меньше. Раньше из пустой карточки убирался только текст
донора, а подложка с маркером оставалась — «карточка без подписи» (`Z-23`,
`Z-49`). Организаторы 17 сентября ставят «новые композиции в рамках
дизайн-системы» выше точного повтора макета (`CTX-QA`), поэтому число
карточек теперь подгоняется под текст: пустая уходит целиком, а её ряд
раздвигается на освободившееся место. Стиль остаётся шаблонным — двигаются и
растягиваются его же фигуры.

Карточка — фигура **слайда** с видимой заливкой или обводкой, внутри которой
лежит хотя бы одно текстовое место раскладки, и у которой есть сестра того же
размера (повтор — признак ряда карточек, а не одиночной плашки). Всё, что
лежит внутри подложки, — её содержимое. Пустая — ни в одной её фигуре нет
текста и ни одно её место не заполнено сборкой.

Одиночная пустая плашка — без сестёр, не больше пятой части слайда — тоже
уходит: рамка без подписи ничего не говорит (WorkSpace, слайд 13, 29 сентября).

**Маркер пункта** — рамка, значок, пиктограмма слева от текстового места, в
повторе одного размера. Пункт пуст — маркер уходит, если в том же списке есть
такой же маркер у заполненного пункта: «нет текста — значок не нужен»
(пользователь 29 сентября, WorkSpace, слайд 14).

Чего проход не трогает: подложки из макета (их на слайде нет — слайд 4
шаблона VK Tech рисует карточки макетом), повёрнутые фигуры и карточки без
своей подложки.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree as ET

from ..oxml.ns import qn

_TOP = (qn("p:sp"), qn("p:pic"), qn("p:grpSp"), qn("p:graphicFrame"), qn("p:cxnSp"))
#: Размеры сестёр совпадают с точностью до этой доли.
_SAME = 0.04
#: Доля площади фигуры внутри подложки, чтобы считаться её содержимым.
_INSIDE = 0.8
#: Одиночная пустая плашка уходит, если она не больше этой доли слайда:
#: крупная — уже фон раздела, а не рамка под подпись.
_LONE_SHARE = 0.2
#: Допуск на стык маркера и текста, EMU (0.02 дюйма).
_TOUCH = 18288


@dataclass
class _Shape:
    el: ET.Element
    xfrm: ET.Element
    x: int
    y: int
    cx: int
    cy: int

    @property
    def area(self) -> int:
        return max(self.cx, 1) * max(self.cy, 1)


def _xfrm(el: ET.Element) -> ET.Element | None:
    if el.tag == qn("p:grpSp"):
        return el.find(f"{qn('p:grpSpPr')}/{qn('a:xfrm')}")
    if el.tag == qn("p:graphicFrame"):
        return el.find(qn("p:xfrm"))
    return el.find(f"{qn('p:spPr')}/{qn('a:xfrm')}")


def _shape(el: ET.Element) -> _Shape | None:
    xfrm = _xfrm(el)
    if xfrm is None or xfrm.get("rot") not in (None, "0"):
        return None
    off, ext = xfrm.find(qn("a:off")), xfrm.find(qn("a:ext"))
    if off is None or ext is None:
        return None
    try:
        return _Shape(el, xfrm, int(off.get("x")), int(off.get("y")),
                      int(ext.get("cx")), int(ext.get("cy")))
    except (TypeError, ValueError):
        return None


def _visible(el: ET.Element) -> bool:
    """Подложка: заливка или обводка видна."""
    if el.tag == qn("p:pic"):
        return True
    if el.tag != qn("p:sp"):
        return False
    sp_pr = el.find(qn("p:spPr"))
    if sp_pr is None:
        return False
    for tag in ("a:solidFill", "a:gradFill", "a:blipFill", "a:pattFill"):
        if sp_pr.find(qn(tag)) is not None:
            return True
    line = sp_pr.find(qn("a:ln"))
    if line is not None and (line.find(qn("a:solidFill")) is not None
                             or line.find(qn("a:gradFill")) is not None):
        return True
    if sp_pr.find(qn("a:noFill")) is None:
        ref = el.find(f"{qn('p:style')}/{qn('a:fillRef')}")
        return ref is not None and ref.get("idx") not in (None, "0")
    return False


def _text(el: ET.Element) -> str:
    return "".join(t.text or "" for t in el.iter(qn("a:t"))).strip()


def _ids(el: ET.Element) -> set[str]:
    return {nv.get("id") for nv in el.iter(qn("p:cNvPr")) if nv.get("id")}


def _inside(inner: _Shape, box: _Shape) -> bool:
    w = min(inner.x + inner.cx, box.x + box.cx) - max(inner.x, box.x)
    h = min(inner.y + inner.cy, box.y + box.cy) - max(inner.y, box.y)
    return w > 0 and h > 0 and w * h >= _INSIDE * inner.area


def _same(a: _Shape, b: _Shape) -> bool:
    return (abs(a.cx - b.cx) <= _SAME * max(a.cx, b.cx)
            and abs(a.cy - b.cy) <= _SAME * max(a.cy, b.cy))


def _move(s: _Shape, x: int, y: int, cx: int | None = None) -> None:
    off, ext = s.xfrm.find(qn("a:off")), s.xfrm.find(qn("a:ext"))
    off.set("x", str(int(x)))
    off.set("y", str(int(y)))
    if cx is not None:
        ext.set("cx", str(int(cx)))


@dataclass
class _Card:
    box: _Shape
    members: list[_Shape]
    empty: bool


def drop_empty_cards(tree: ET.Element, text_slots: set[str], filled: set[str],
                     slide_area: int = 0) -> int:
    """Убирает пустые карточки слайда и раздвигает оставшиеся в их рядах, затем
    одиночные пустые плашки и маркеры пустых пунктов.

    `text_slots` — id фигур текстовых мест раскладки, `filled` — id фигур,
    которые сборка заполнила, `slide_area` — площадь слайда, EMU² (0 —
    одиночные плашки не трогаются). Возвращает, сколько убрано."""
    sp_tree = tree.find(f"{qn('p:cSld')}/{qn('p:spTree')}")
    if sp_tree is None:
        return 0
    return (_drop_cards(sp_tree, text_slots, filled, slide_area)
            + _drop_markers(sp_tree, text_slots, filled))


def _remove(sp_tree: ET.Element, shapes: list[_Shape]) -> None:
    alive = {id(el) for el in sp_tree}
    for s in shapes:
        if id(s.el) in alive:
            sp_tree.remove(s.el)
            alive.discard(id(s.el))


def _drop_cards(sp_tree: ET.Element, text_slots: set[str], filled: set[str],
                slide_area: int) -> int:
    shapes = [s for el in sp_tree if el.tag in _TOP and (s := _shape(el)) is not None]
    boxes = [s for s in shapes if _visible(s.el)]
    cards: list[_Card] = []
    for box in boxes:
        members = [s for s in shapes if s is not box and _inside(s, box) and s.area < box.area]
        ids = set().union(_ids(box.el), *(_ids(m.el) for m in members))
        if not ids & text_slots:
            continue
        # Пустая — сборка не заполнила ни одного её места, и в её местах и
        # таблицах нет текста. Надпись шаблона вне мест (цифра «01») уходит
        # вместе с карточкой.
        spoken = any(_text(m.el) for m in [box, *members]
                     if _ids(m.el) & text_slots or m.el.tag == qn("p:graphicFrame"))
        cards.append(_Card(box, members, not (ids & filled) and not spoken))
    # Карточка внутри другой карточки — её содержимое, а не отдельная.
    inner = {id(c) for c in cards for o in cards if o is not c and _inside(c.box, o.box)
             and c.box.area < o.box.area}
    cards = [c for c in cards if id(c) not in inner]
    # Карточка — в повторе: у неё есть сестра того же размера. Одиночная —
    # только пустая и небольшая, и уходит без раздвижки.
    lone = [c for c in cards if c.empty and slide_area
            and not any(o is not c and _same(o.box, c.box) for o in cards)
            and c.box.area <= _LONE_SHARE * slide_area]
    _remove(sp_tree, [s for c in lone for s in (c.box, *c.members)])
    dropped = len(lone)
    cards = [c for c in cards if any(o is not c and _same(o.box, c.box) for o in cards)]
    if not any(c.empty for c in cards):
        return dropped

    families: list[list[_Card]] = []
    for card in cards:
        for family in families:
            if _same(family[0].box, card.box):
                family.append(card)
                break
        else:
            families.append([card])
    for family in families:
        if not any(c.empty for c in family):
            continue
        rows: list[list[_Card]] = []
        for card in sorted(family, key=lambda c: (c.box.y, c.box.x)):
            for row in rows:
                if abs(row[0].box.y - card.box.y) <= _SAME * card.box.cy:
                    row.append(card)
                    break
            else:
                rows.append([card])
        row_y = [row[0].box.y for row in rows]
        kept_rows = []
        for row in rows:
            row.sort(key=lambda c: c.box.x)
            keep = [c for c in row if not c.empty]
            for card in row:
                if card.empty:
                    _remove(sp_tree, [card.box, *card.members])
                    dropped += 1
            if keep:
                _spread(row, keep)
                kept_rows.append(keep)
        # Опустевший ряд — нижние ряды поднимаются на его место.
        if not _even(row_y, rows[0][0].box.cy):
            continue
        for n, keep in enumerate(kept_rows):
            dy = row_y[n] - keep[0].box.y
            if dy:
                for card in keep:
                    for s in [card.box, *card.members]:
                        _move(s, s.x, s.y + dy)
                        s.y += dy
    return dropped


def _even(starts: list[int], size: int) -> bool:
    """Шаг ряда ровный — это один ряд карточек, а не две колонки поодаль."""
    steps = [b - a for a, b in zip(starts, starts[1:])]
    return all(abs(d - steps[0]) <= _SAME * size for d in steps)


def _spread(row: list[_Card], keep: list[_Card]) -> None:
    """Оставшиеся карточки ряда занимают его прежнюю ширину с прежним зазором."""
    if len(keep) == len(row) or not _even([c.box.x for c in row], row[0].box.cx):
        return
    left = row[0].box.x
    right = row[-1].box.x + row[-1].box.cx
    width = row[0].box.cx
    gap = (right - left - len(row) * width) / (len(row) - 1) if len(row) > 1 else 0
    new_w = (right - left - (len(keep) - 1) * gap) / len(keep)
    for n, card in enumerate(keep):
        box = card.box
        new_x = left + n * (new_w + gap)
        if box.el.tag == qn("p:grpSp"):
            # Группу растянуть нельзя — поплывут её дети; только сдвиг.
            _shift(card, new_x + (new_w - box.cx) / 2 - box.x)
            continue
        scale = new_w / box.cx
        for s in card.members:
            _place_member(s, box, new_x, scale)
        _move(box, new_x, box.y, new_w)
        box.x, box.cx = int(new_x), int(new_w)


def _shift(card: _Card, dx: float) -> None:
    for s in [card.box, *card.members]:
        _move(s, s.x + dx, s.y)
        s.x = int(s.x + dx)


def _place_member(s: _Shape, box: _Shape, new_x: float, scale: float) -> None:
    """Текст растягивается вместе с карточкой; маркер, значок, картинка —
    своего размера, у своего края: левый у левого, правый у правого, средний
    по центру."""
    rel = s.x - box.x
    if s.el.find(qn("p:txBody")) is not None and s.el.tag == qn("p:sp") and not _visible(s.el):
        x, cx = new_x + rel * scale, s.cx * scale
        _move(s, x, s.y, cx)
        s.x, s.cx = int(x), int(cx)
        return
    center = rel + s.cx / 2
    if center < box.cx / 3:
        x = new_x + rel
    elif center > 2 * box.cx / 3:
        x = new_x + box.cx * scale - (box.cx - rel)
    else:
        x = new_x + center * scale - s.cx / 2
    _move(s, x, s.y)
    s.x = int(x)


def _drop_markers(sp_tree: ET.Element, text_slots: set[str], filled: set[str]) -> int:
    """Маркер пустого пункта уходит, если такой же маркер есть у заполненного."""
    shapes = [s for el in sp_tree if el.tag in _TOP and (s := _shape(el)) is not None]
    texts = [s for s in shapes if s.el.tag == qn("p:sp") and _ids(s.el) & text_slots]
    marks: dict[int, tuple[_Shape, list[_Shape]]] = {}
    for t in texts:
        for m in shapes:
            if m is t or _ids(m.el) & text_slots:
                continue
            if not (_visible(m.el) or m.el.tag in (qn("p:grpSp"), qn("p:pic"))):
                continue
            gap = t.x - (m.x + m.cx)
            if (m.cx > t.cx / 2 or gap < -_TOUCH or gap > 1.5 * max(m.cx, m.cy)
                    or m.y >= t.y + t.cy or m.y + m.cy <= t.y):
                continue
            marks.setdefault(id(m), (m, []))[1].append(t)

    def said(t: _Shape) -> bool:
        return bool(_ids(t.el) & filled) or bool(_text(t.el))

    gone: list[_Shape] = []
    count = 0
    for m, ts in marks.values():
        if any(said(t) for t in ts):
            continue
        # Список: такой же маркер стоит у заполненного пункта.
        if not any(o is not m and _same(o, m) and any(said(t) for t in ots)
                   for o, ots in marks.values()):
            continue
        count += 1
        gone.append(m)
        gone.extend(s for s in shapes if s is not m and s.area < m.area and _inside(s, m))
    _remove(sp_tree, gone)
    return count
