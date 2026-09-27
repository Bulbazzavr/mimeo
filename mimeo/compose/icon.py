"""Пиктограмма на месте значка шаблона — `Z-32`, `ADR-0028`.

Значок донора — картинка рядом с пунктом: на выданных шаблонах это линейные
иконки одного цвета (шестерёнка VK Tech, лампочка и книга VK Education), и
стоят они одни и те же, о чём бы ни был пункт. Какую пиктограмму поставить,
решает модель по смыслу пункта (`plan/icons.py`); здесь — механика: картинка
уходит, на её место и в её рамку встаёт **нативная фигура PowerPoint** —
`a:custGeom` с контуром иконки. ТЗ, раздел 2, п. 3, требует пиктограмм внутри
слайда, а слайд растром не засчитывается; фигура редактируется и
перекрашивается в PowerPoint, как любая другая.

Иконки — открытый набор Tabler Icons (MIT, `THIRD-PARTY.md`), файлы SVG в
`assets/icons/tabler/` лежат как выпущены. Набор линейный: контур толщиной 2
на поле 24 × 24, скруглённые концы и углы, заливки нет, — поэтому фигура
получает только линию, и правило заливки SVG переводить не нужно.
"""

from __future__ import annotations

import math
import os
import re
from xml.etree import ElementTree as ET

from ..oxml.ns import qn

#: Поле иконки в единицах пути фигуры: 24 единицы SVG × 1000 — целые числа
#: без потери точности (координаты набора — не мельче тысячных).
_UNIT = 1000

_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def assets_root() -> str:
    """`assets/icons/` в корне репозитория — рядом с `config/` (`ADR-0022`)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(os.path.dirname(here)), "assets", "icons")


def _arc_args(raw: list[str], i: int) -> tuple[list[float], int] | None:
    """Семь чисел дуги с позиции `i`. Флаги бывают слитными (`a1 1 0 011 1`):
    после трёх чисел дуги два флага — по одной цифре каждый."""
    nums: list[float] = []
    while len(nums) < 7 and i < len(raw):
        tok = raw[i]
        if tok.isalpha():
            return None
        if len(nums) in (3, 4) and len(tok) > 1 and tok[0] in "01" and tok[1] in "01.":
            # Слитые флаги: «011» — флаг 0, флаг 1 и начало следующего числа.
            nums.append(float(tok[0]))
            raw[i] = tok[1:]
            continue
        nums.append(float(tok))
        i += 1
    return (nums, i) if len(nums) == 7 else None


def _arc(x1, y1, rx, ry, phi, large, sweep, x2, y2) -> list[tuple[float, ...]]:
    """Дуга SVG в кубические кривые (SVG 1.1, прил. F.6.5 и F.6.6)."""
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if not rx or not ry:
        return [(x1, y1, x2, y2, x2, y2)]
    cos, sin = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    xp, yp = cos * dx + sin * dy, -sin * dx + cos * dy
    lam = (xp / rx) ** 2 + (yp / ry) ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * yp * yp - ry * ry * xp * xp
    den = rx * rx * yp * yp + ry * ry * xp * xp
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp, cyp = coef * rx * yp / ry, -coef * ry * xp / rx
    cx = cos * cxp - sin * cyp + (x1 + x2) / 2
    cy = sin * cxp + cos * cyp + (y1 + y2) / 2

    def angle(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    t1 = angle(1, 0, (xp - cxp) / rx, (yp - cyp) / ry)
    dt = angle((xp - cxp) / rx, (yp - cyp) / ry, (-xp - cxp) / rx, (-yp - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    parts = max(1, math.ceil(abs(dt) / (math.pi / 2) - 1e-9))
    step = dt / parts
    k = 4 / 3 * math.tan(step / 4)
    out = []

    def point(t):
        return (cx + rx * math.cos(t) * cos - ry * math.sin(t) * sin,
                cy + rx * math.cos(t) * sin + ry * math.sin(t) * cos)

    def deriv(t):
        return (-rx * math.sin(t) * cos - ry * math.cos(t) * sin,
                -rx * math.sin(t) * sin + ry * math.cos(t) * cos)

    t = t1
    for _ in range(parts):
        p0, p3 = point(t), point(t + step)
        d0, d3 = deriv(t), deriv(t + step)
        out.append((p0[0] + k * d0[0], p0[1] + k * d0[1],
                    p3[0] - k * d3[0], p3[1] - k * d3[1], p3[0], p3[1]))
        t += step
    # Конец — точно в заданную точку: округление не должно рвать контур.
    last = out[-1]
    out[-1] = (*last[:4], x2, y2)
    return out


def parse_path(d: str) -> list[tuple]:
    """Путь SVG в абсолютные команды `M`, `L`, `C`, `Q`, `Z`.

    Дуги — кубическими кривыми, сокращения (`H`, `V`, `S`, `T`) — полными
    командами. Неразборный хвост пути отбрасывается: иконка — набор, который
    мы положили сами, и тест проверяет, что разбирается каждый файл."""
    raw = _TOKEN.findall(d)
    out: list[tuple] = []
    x = y = sx = sy = 0.0
    last_c = last_q = None
    cmd = None
    i = 0
    while i < len(raw):
        tok = raw[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in "Zz":
                out.append(("Z",))
                x, y = sx, sy
                last_c = last_q = None
                continue
        elif cmd is None:
            return out
        up = cmd.upper()
        rel = cmd.islower()
        if up == "A":
            got = _arc_args(raw, i)
            if got is None:
                return out
            (rx, ry, phi, large, sweep, ex, ey), i = got
            if rel:
                ex, ey = ex + x, ey + y
            for seg in _arc(x, y, rx, ry, phi, int(large), int(sweep), ex, ey):
                out.append(("C", *seg))
            x, y = ex, ey
            last_c = last_q = None
            continue
        n = _ARGS[up]
        if i + n > len(raw) or any(t.isalpha() for t in raw[i:i + n]):
            return out
        v = [float(t) for t in raw[i:i + n]]
        i += n
        if up == "M":
            px, py = (v[0] + x, v[1] + y) if rel else (v[0], v[1])
            out.append(("M", px, py))
            x, y = sx, sy = px, py
            cmd = "l" if rel else "L"       # пары после M — линии
            last_c = last_q = None
        elif up in "LHV":
            if up == "H":
                px, py = (v[0] + x if rel else v[0]), y
            elif up == "V":
                px, py = x, (v[0] + y if rel else v[0])
            else:
                px, py = (v[0] + x, v[1] + y) if rel else (v[0], v[1])
            out.append(("L", px, py))
            x, y = px, py
            last_c = last_q = None
        elif up in "CS":
            if up == "C":
                c1 = (v[0] + x, v[1] + y) if rel else (v[0], v[1])
                rest = v[2:]
            else:
                c1 = (2 * x - last_c[0], 2 * y - last_c[1]) if last_c else (x, y)
                rest = v
            c2 = (rest[0] + x, rest[1] + y) if rel else (rest[0], rest[1])
            end = (rest[2] + x, rest[3] + y) if rel else (rest[2], rest[3])
            out.append(("C", *c1, *c2, *end))
            last_c, last_q = c2, None
            x, y = end
        elif up in "QT":
            if up == "Q":
                c = (v[0] + x, v[1] + y) if rel else (v[0], v[1])
                end = (v[2] + x, v[3] + y) if rel else (v[2], v[3])
            else:
                c = (2 * x - last_q[0], 2 * y - last_q[1]) if last_q else (x, y)
                end = (v[0] + x, v[1] + y) if rel else (v[0], v[1])
            out.append(("Q", *c, *end))
            last_q, last_c = c, None
            x, y = end
    return out


def load_icon(name: str, root: str | None = None) -> tuple[float, list[tuple]] | None:
    """Поле иконки и её контур: (сторона поля, команды). Нет файла — `None`.

    Берутся все `path` файла, кроме невидимых (`stroke="none"`: у Tabler это
    рамка поля `M0 0h24v24H0z`)."""
    path = os.path.join(root or os.path.join(assets_root(), "tabler"), f"{name}.svg")
    try:
        svg = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    box = (svg.get("viewBox") or "0 0 24 24").split()
    side = float(box[2]) if len(box) == 4 else 24.0
    cmds: list[tuple] = []
    for el in svg.iter():
        if el.tag.rpartition("}")[2] != "path" or el.get("stroke") == "none":
            continue
        cmds.extend(parse_path(el.get("d") or ""))
    return (side, cmds) if cmds else None


def _pt(parent: ET.Element, x: float, y: float) -> None:
    ET.SubElement(parent, qn("a:pt"), {"x": str(round(x * _UNIT)), "y": str(round(y * _UNIT))})


def _color(color: str | None) -> ET.Element:
    """`#RRGGBB` — цвет значка донора; `None` — первый акцент темы шаблона."""
    fill = ET.Element(qn("a:solidFill"))
    if color and re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        ET.SubElement(fill, qn("a:srgbClr"), {"val": color[1:].upper()})
    else:
        ET.SubElement(fill, qn("a:schemeClr"), {"val": "accent1"})
    return fill


def icon_shape(shape_id: str, name: str, rect: tuple[int, int, int, int],
               color: str | None, root: str | None = None) -> ET.Element | None:
    """Фигура `p:sp` с контуром иконки `name` в квадрате по центру `rect`
    (x, y, cx, cy — в координатах родителя картинки). Нет иконки — `None`."""
    icon = load_icon(name, root)
    if icon is None:
        return None
    field, cmds = icon
    x, y, cx, cy = rect
    side = min(cx, cy)
    if side <= 0:
        return None
    x, y = x + (cx - side) // 2, y + (cy - side) // 2

    sp = ET.Element(qn("p:sp"))
    nv = ET.SubElement(sp, qn("p:nvSpPr"))
    ET.SubElement(nv, qn("p:cNvPr"), {"id": shape_id, "name": f"Пиктограмма {name}", "descr": name})
    ET.SubElement(nv, qn("p:cNvSpPr"))
    ET.SubElement(nv, qn("p:nvPr"))
    pr = ET.SubElement(sp, qn("p:spPr"))
    xfrm = ET.SubElement(pr, qn("a:xfrm"))
    ET.SubElement(xfrm, qn("a:off"), {"x": str(x), "y": str(y)})
    ET.SubElement(xfrm, qn("a:ext"), {"cx": str(side), "cy": str(side)})
    geom = ET.SubElement(pr, qn("a:custGeom"))
    for tag in ("a:avLst", "a:gdLst", "a:ahLst", "a:cxnLst"):
        ET.SubElement(geom, qn(tag))
    ET.SubElement(geom, qn("a:rect"), {"l": "l", "t": "t", "r": "r", "b": "b"})
    lst = ET.SubElement(geom, qn("a:pathLst"))
    size = str(round(field * _UNIT))
    path = ET.SubElement(lst, qn("a:path"), {"w": size, "h": size, "fill": "none"})
    for op, *v in cmds:
        if op == "M":
            _pt(ET.SubElement(path, qn("a:moveTo")), *v)
        elif op == "L":
            _pt(ET.SubElement(path, qn("a:lnTo")), *v)
        elif op == "C":
            el = ET.SubElement(path, qn("a:cubicBezTo"))
            for k in range(0, 6, 2):
                _pt(el, v[k], v[k + 1])
        elif op == "Q":
            el = ET.SubElement(path, qn("a:quadBezTo"))
            for k in range(0, 4, 2):
                _pt(el, v[k], v[k + 1])
        elif op == "Z":
            ET.SubElement(path, qn("a:close"))
    ET.SubElement(pr, qn("a:noFill"))
    # Толщина контура — как у набора: 2 единицы на поле 24, в масштабе места.
    ln = ET.SubElement(pr, qn("a:ln"), {"w": str(max(1, round(side * 2 / field))), "cap": "rnd"})
    ln.append(_color(color))
    ET.SubElement(ln, qn("a:round"))
    return sp


def replace_with_icon(tree: ET.Element, picture: ET.Element, name: str,
                      color: str | None, root: str | None = None) -> str | None:
    """Картинка-значок `picture` уступает место пиктограмме `name` — в том же
    родителе и на той же позиции в порядке фигур. Возвращает текст
    предупреждения или `None`."""
    xfrm = picture.find(f"{qn('p:spPr')}/{qn('a:xfrm')}")
    off = xfrm.find(qn("a:off")) if xfrm is not None else None
    ext = xfrm.find(qn("a:ext")) if xfrm is not None else None
    nv = picture.find(f"{qn('p:nvPicPr')}/{qn('p:cNvPr')}")
    if off is None or ext is None or nv is None:
        return f"значок {nv.get('id') if nv is not None else '?'}: нет рамки — пиктограмма не встала"
    rect = (int(off.get("x", 0)), int(off.get("y", 0)), int(ext.get("cx", 0)), int(ext.get("cy", 0)))
    shape = icon_shape(nv.get("id"), name, rect, color, root)
    if shape is None:
        return f"пиктограммы «{name}» нет в наборе — значок шаблона оставлен"
    parents = {child: parent for parent in tree.iter() for child in parent}
    parent = parents.get(picture)
    if parent is None:
        return f"значок {nv.get('id')} не найден на слайде"
    at = list(parent).index(picture)
    parent.remove(picture)
    parent.insert(at, shape)
    return None
