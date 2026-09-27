"""Заливки и линии: DrawingML -> CSS и SVG. DOM-COLOR §5.

Заливка фигуры ищется по цепочке: своя `p:spPr`, затем `p:spPr` плейсхолдеров
макета и мастера, затем ссылка `p:style/a:fillRef` на матрицу стилей темы.
Линия — так же, но свойства `a:ln` разрешаются по одному: толщина может прийти
из файла слайда, а цвет из темы.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from ..oxml.ns import local_name, qn
from ._base import Color, Ctx, child_color, css, integer, mix, pct
from ._geom import num

FILL_TAGS = ("noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill")
_FILL_QN = {qn(f"a:{t}") for t in FILL_TAGS}


@dataclass(frozen=True)
class Fill:
    kind: str                                   # solid | grad | blip | none
    color: Color | None = None
    stops: tuple[tuple[float, Color], ...] = ()
    angle: float = 0.0                          # градусы по часовой от оси x
    radial: str | None = None                   # circle | rect | shape
    focus: tuple[float, float] = (0.5, 0.5)
    blip: ET.Element | None = None              # a:blipFill целиком
    part: str | None = None                     # от чьих связей разрешать r:embed


NO_FILL = Fill(kind="none")


def find_fill(holder: ET.Element | None) -> ET.Element | None:
    """Первый элемент заливки среди детей (`p:spPr`, `a:ln`, `a:tcPr`…)."""
    if holder is None:
        return None
    for child in holder:
        if child.tag in _FILL_QN:
            return child
    return None


def fill_of(el: ET.Element | None, ctx: Ctx) -> Fill | None:
    """Элемент заливки в `Fill`. `a:grpFill` разрешает вызывающий: ему
    известна группа."""
    if el is None:
        return None
    tag = local_name(el.tag)
    if tag == "noFill":
        return NO_FILL
    if tag == "solidFill":
        c = child_color(el, ctx)
        return Fill(kind="solid", color=c) if c else NO_FILL
    if tag == "gradFill":
        stops = []
        lst = el.find(qn("a:gsLst"))
        for gs in (lst.findall(qn("a:gs")) if lst is not None else []):
            c = child_color(gs, ctx)
            if c is None:
                continue
            stops.append((pct(gs.get("pos"), 0.0), c))
        if not stops:
            return NO_FILL
        stops.sort(key=lambda s: s[0])
        lin = el.find(qn("a:lin"))
        path = el.find(qn("a:path"))
        if path is not None:
            rect = path.find(qn("a:fillToRect"))
            fx = fy = 0.5
            if rect is not None:
                lft, rgt = pct(rect.get("l"), 0.0), pct(rect.get("r"), 0.0)
                top, bot = pct(rect.get("t"), 0.0), pct(rect.get("b"), 0.0)
                fx, fy = (lft + 1 - rgt) / 2, (top + 1 - bot) / 2
            kind = path.get("path") or "circle"
            if kind != "circle":
                ctx.doc.note(f"gradient path={kind}")
            return Fill(kind="grad", stops=tuple(stops), radial=kind, focus=(fx, fy))
        angle = integer(lin.get("ang"), 0) / 60000 if lin is not None else 90.0
        return Fill(kind="grad", stops=tuple(stops), angle=angle)
    if tag == "blipFill":
        return Fill(kind="blip", blip=el, part=ctx.part)
    if tag == "pattFill":
        fg = child_color(el.find(qn("a:fgClr")), ctx) or ("#000000", 1.0)
        bg = child_color(el.find(qn("a:bgClr")), ctx) or ("#FFFFFF", 1.0)
        ctx.doc.note("pattern fill")
        return Fill(kind="solid", color=mix(bg, fg[0], _pattern_density(el.get("prst") or "")))
    return None


def _pattern_density(prst: str) -> float:
    """Доля переднего цвета в узоре: узор сводится к ровному тону."""
    if prst.startswith("pct"):
        return integer(prst[3:], 50) / 100
    if prst.startswith(("lt", "narrow")):
        return 0.25
    if prst.startswith(("dk", "wide")):
        return 0.5
    return 0.4


# --- ссылки на матрицу стилей темы ----------------------------------------------


def style_fill(ref: ET.Element | None, ctx: Ctx) -> Fill | None:
    """`a:fillRef idx=N` + цвет: N-я заливка темы, где `phClr` — этот цвет.
    1..999 — `fillStyleLst`, 1001.. — `bgFillStyleLst`, 0 — без заливки."""
    if ref is None:
        return None
    idx = integer(ref.get("idx"), 0)
    if idx == 0:
        return NO_FILL
    lst = ctx.fmt.bg_fills if idx >= 1001 else ctx.fmt.fills
    i = idx - 1001 if idx >= 1001 else idx - 1
    if not 0 <= i < len(lst):
        return None
    ph = child_color(ref, ctx)
    return fill_of(lst[i], ctx.with_ph(ph[0] if ph else None))


def style_line(ref: ET.Element | None, ctx: Ctx) -> tuple[ET.Element | None, Ctx]:
    """`a:lnRef idx=N`: N-я линия темы и контекст, где `phClr` — цвет ссылки."""
    if ref is None:
        return None, ctx
    idx = integer(ref.get("idx"), 0)
    if not 1 <= idx <= len(ctx.fmt.lines):
        return None, ctx
    ph = child_color(ref, ctx)
    return ctx.fmt.lines[idx - 1], ctx.with_ph(ph[0] if ph else None)


# --- линии ---------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    color: Color
    width: float                # EMU
    dash: str = "solid"
    cap: str = "flat"
    join: str = "round"
    head: tuple[str, str, str] | None = None   # вид, ширина, длина
    tail: tuple[str, str, str] | None = None


def line_of(chain: list[ET.Element], style_ln: ET.Element | None, style_ctx: Ctx,
            ctx: Ctx) -> Line | None:
    """Линия фигуры по цепочке `a:ln` (слайд, макет, мастер) и линии темы.

    Заливка линии берётся у первого звена, где она есть; если её нет нигде,
    кроме темы, — из темы со своим `phClr`. Нет заливки вовсе — нет линии."""
    fill: Fill | None = None
    for ln in chain:
        fill = fill_of(find_fill(ln), ctx)
        if fill is not None:
            break
    if fill is None and style_ln is not None:
        fill = fill_of(find_fill(style_ln), style_ctx)
    if fill is None or fill.kind == "none":
        return None
    if fill.kind == "grad":
        c = fill.stops[0][1]
    elif fill.kind == "solid" and fill.color:
        c = fill.color
    else:
        return None
    links = chain + ([style_ln] if style_ln is not None else [])

    def attr(name: str) -> str | None:
        for ln in links:
            v = ln.get(name)
            if v is not None:
                return v
        return None

    def child(tag: str) -> ET.Element | None:
        for ln in links:
            found = ln.find(qn(tag))
            if found is not None:
                return found
        return None

    width = float(integer(attr("w"), 9525))
    dash_el = child("a:prstDash")
    dash = (dash_el.get("val") or "solid") if dash_el is not None else "solid"
    if dash_el is None and child("a:custDash") is not None:
        dash = "dash"
    join = "round"
    for ln in links:
        if ln.find(qn("a:miter")) is not None:
            join = "miter"
            break
        if ln.find(qn("a:bevel")) is not None:
            join = "bevel"
            break
        if ln.find(qn("a:round")) is not None:
            break
    cap = {"rnd": "round", "sq": "square"}.get(attr("cap") or "", "butt")

    def end(tag: str) -> tuple[str, str, str] | None:
        el = child(tag)
        if el is None or (el.get("type") or "none") == "none":
            return None
        return el.get("type") or "triangle", el.get("w") or "med", el.get("len") or "med"

    return Line(color=c, width=max(width, 0.0), dash=dash, cap=cap, join=join,
                head=end("a:headEnd"), tail=end("a:tailEnd"))


#: Штрихи в толщинах линии, как у PowerPoint (`a:prstDash`).
_DASHES = {
    "dot": (1, 3), "dash": (4, 3), "lgDash": (8, 3), "dashDot": (4, 3, 1, 3),
    "lgDashDot": (8, 3, 1, 3), "lgDashDotDot": (8, 3, 1, 3, 1, 3),
    "sysDash": (3, 1), "sysDot": (1, 1), "sysDashDot": (3, 1, 1, 1),
    "sysDashDotDot": (3, 1, 1, 1, 1, 1),
}


def svg_stroke(line: Line | None, scale: float) -> str:
    """Атрибуты обводки SVG; `scale` переводит EMU в единицы `viewBox`."""
    if line is None:
        return ' stroke="none"'
    hex_, alpha = line.color
    w = max(line.width, 3175.0) * scale     # тоньше четверти пункта PowerPoint не рисует
    out = f' stroke="{hex_}" stroke-width="{num(w, 3)}"'
    if alpha < 0.999:
        out += f' stroke-opacity="{num(alpha, 3)}"'
    pattern = _DASHES.get(line.dash)
    if pattern:
        out += ' stroke-dasharray="' + " ".join(num(v * w, 3) for v in pattern) + '"'
    if line.cap != "butt":
        out += f' stroke-linecap="{line.cap}"'
    if line.join != "miter":
        out += f' stroke-linejoin="{line.join}"'
    return out


# --- заливка в CSS и в SVG --------------------------------------------------------


def _css_stops(fill: Fill) -> str:
    return ",".join(f"{css(c)} {num(p * 100, 2)}%" for p, c in fill.stops)


def css_background(fill: Fill | None) -> str:
    """Заливка прямоугольного слоя как `background` CSS (кроме картинки)."""
    if fill is None or fill.kind in ("none", "blip"):
        return ""
    if fill.kind == "solid":
        return f"background:{css(fill.color)};"
    if len(fill.stops) == 1:
        return f"background:{css(fill.stops[0][1])};"
    if fill.radial:
        shape = "circle" if fill.radial == "circle" else "ellipse"
        fx, fy = fill.focus
        return (f"background:radial-gradient({shape} farthest-corner at {num(fx * 100, 2)}% "
                f"{num(fy * 100, 2)}%,{_css_stops(fill)});")
    # DrawingML: 0° — слева направо, по часовой. CSS: 0° — снизу вверх.
    return f"background:linear-gradient({num(fill.angle + 90, 2)}deg,{_css_stops(fill)});"


def svg_fill(fill: Fill | None, ctx: Ctx, w: float, h: float,
             mode: str = "norm") -> tuple[str, str]:
    """(атрибуты заливки, определения для `defs`) в системе `viewBox` w×h.

    Градиент задан в координатах пользователя, а не долях рамки: иначе на
    неквадратной фигуре угол градиента исказился бы."""
    if fill is None or fill.kind in ("none", "blip") or mode == "none":
        return ' fill="none"', ""
    shade = {"lighten": ("#FFFFFF", 0.4), "lightenLess": ("#FFFFFF", 0.2),
             "darken": ("#000000", 0.4), "darkenLess": ("#000000", 0.2)}.get(mode)
    if fill.kind == "solid" and fill.color:
        c = mix(fill.color, *shade) if shade else fill.color
        out = f' fill="{c[0]}"'
        if c[1] < 0.999:
            out += f' fill-opacity="{num(c[1], 3)}"'
        return out, ""
    if fill.kind != "grad":
        return ' fill="none"', ""
    gid = ctx.doc.uid("g")
    stops = "".join(
        f'<stop offset="{num(p, 4)}" stop-color="{(mix(c, *shade) if shade else c)[0]}"'
        + (f' stop-opacity="{num(c[1], 3)}"' if c[1] < 0.999 else "") + "/>"
        for p, c in fill.stops)
    if fill.radial:
        fx, fy = fill.focus[0] * w, fill.focus[1] * h
        r = max(math.hypot(max(fx, w - fx), max(fy, h - fy)), 1e-6)
        defs = (f'<radialGradient id="{gid}" gradientUnits="userSpaceOnUse" cx="{num(fx)}" '
                f'cy="{num(fy)}" r="{num(r)}">{stops}</radialGradient>')
    else:
        a = math.radians(fill.angle)
        dx, dy = math.cos(a), math.sin(a)
        half = (abs(w * dx) + abs(h * dy)) / 2
        cx, cy = w / 2, h / 2
        defs = (f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" '
                f'x1="{num(cx - dx * half)}" y1="{num(cy - dy * half)}" '
                f'x2="{num(cx + dx * half)}" y2="{num(cy + dy * half)}">{stops}</linearGradient>')
    return f' fill="url(#{gid})"', defs


def first_color(fill: Fill | None) -> Color | None:
    """Один цвет заливки — там, где градиент не нарисовать (текст, маркер)."""
    if fill is None:
        return None
    if fill.kind == "solid":
        return fill.color
    if fill.kind == "grad" and fill.stops:
        return fill.stops[0][1]
    return None


def marker_defs(line: Line, ctx: Ctx) -> tuple[str, str, str]:
    """Наконечники линии как маркеры SVG: (defs, marker-start, marker-end).

    Размеры `w`/`len` (sm/med/lg) — в толщинах линии, как у PowerPoint."""
    size = {"sm": 2.0, "med": 3.0, "lg": 5.0}
    defs = []
    refs = ["", ""]
    for i, end in enumerate((line.head, line.tail)):
        if end is None:
            continue
        kind, wd, ln = end
        mid = ctx.doc.uid("k")
        mw, ml = size.get(wd, 3.0), size.get(ln, 3.0)
        c = line.color[0]
        if kind == "oval":
            body, ref = f'<ellipse cx="5" cy="5" rx="5" ry="5" fill="{c}"/>', 5
        elif kind == "diamond":
            body, ref = f'<path d="M0 5L5 0L10 5L5 10Z" fill="{c}"/>', 5
        elif kind == "stealth":
            body, ref = f'<path d="M0 0L10 5L0 10L3.5 5Z" fill="{c}"/>', 9
        elif kind == "arrow":
            body = f'<path d="M1 1L9 5L1 9" fill="none" stroke="{c}" stroke-width="1.5"/>'
            ref = 9
        else:
            body, ref = f'<path d="M0 0L10 5L0 10Z" fill="{c}"/>', 9
        defs.append(f'<marker id="{mid}" viewBox="0 0 10 10" refX="{ref}" refY="5" '
                    f'markerWidth="{num(ml)}" markerHeight="{num(mw)}" preserveAspectRatio="none" '
                    f'orient="auto-start-reverse" markerUnits="strokeWidth">{body}</marker>')
        refs[i] = f"url(#{mid})"
    attrs_start = f' marker-start="{refs[0]}"' if refs[0] else ""
    attrs_end = f' marker-end="{refs[1]}"' if refs[1] else ""
    return "".join(defs), attrs_start, attrs_end
