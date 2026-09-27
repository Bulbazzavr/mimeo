"""Диаграмма `c:chart` в SVG — по кэшам значений, без пересчёта из таблицы.

PowerPoint хранит в части диаграммы копию данных (`c:strCache`, `c:numCache`)
ровно для того, чтобы её можно было нарисовать без Excel; ими и пользуемся.
Поддержаны столбцы и полосы (с накоплением тоже), линии, области, круг и
кольцо, точечная. Остальные виды — рамка с названием и рядами, и отметка в
отчёте: подделывать неизвестную диаграмму нельзя.

Всё рисуется в `viewBox` в пунктах: SVG масштабируется целиком, и кегль в
пунктах остаётся кеглем слайда при любой ширине окна.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from ..oxml.ns import local_name, qn
from ._base import EMU_PER_PT, Color, Ctx, child_color, integer
from ._geom import num
from ._paint import fill_of, find_fill, first_color
from ._text import font_stack

_SUPPORTED = {"barChart", "bar3DChart", "lineChart", "line3DChart", "areaChart",
              "area3DChart", "pieChart", "pie3DChart", "doughnutChart", "ofPieChart",
              "scatterChart"}


@dataclass
class _Series:
    name: str
    cats: list[str]
    vals: list[float | None]
    xs: list[float | None] = field(default_factory=list)
    fill: Color | None = None
    line: Color | None = None
    line_w: float = 2.25
    no_fill: bool = False
    points: dict[int, Color] = field(default_factory=dict)
    show_val: bool = False
    marker: bool = True
    marker_r: float = 2.5
    fmt: str = "General"
    label_fmt: str | None = None   # формат подписей данных, если свой
    label_pos: str | None = None   # c:dLblPos
    sep: str = ", "                # разделитель частей подписи


def _text_of(rich: ET.Element | None) -> str:
    if rich is None:
        return ""
    paras = []
    for p in rich.iter(qn("a:p")):
        paras.append("".join(t.text or "" for t in p.iter(qn("a:t"))))
    return " ".join(x for x in paras if x)


def _pts(holder: ET.Element | None) -> tuple[list[str], str]:
    """Точки кэша/литерала по `idx` (пропуски — пустые строки) и формат."""
    if holder is None:
        return [], "General"
    cache = None
    for tag in ("c:strRef", "c:numRef", "c:multiLvlStrRef"):
        ref = holder.find(qn(tag))
        if ref is not None:
            for ctag in ("c:strCache", "c:numCache", "c:multiLvlStrCache"):
                cache = ref.find(qn(ctag))
                if cache is not None:
                    break
            break
    if cache is None:
        cache = _first(holder, "c:strLit", "c:numLit")
    if cache is None:
        return [], "General"
    if cache.tag == qn("c:multiLvlStrCache"):
        lvl = cache.find(qn("c:lvl"))
        count_el = cache.find(qn("c:ptCount"))
        cache = lvl if lvl is not None else cache
    else:
        count_el = cache.find(qn("c:ptCount"))
    fmt_el = cache.find(qn("c:formatCode"))
    fmt = (fmt_el.text or "General") if fmt_el is not None else "General"
    items: dict[int, str] = {}
    for pt in cache.findall(qn("c:pt")):
        try:
            idx = int(pt.get("idx") or 0)
        except ValueError:
            continue
        v = pt.find(qn("c:v"))
        items[idx] = (v.text or "") if v is not None else ""
        fc = pt.get("formatCode")
        if fc and fmt == "General":
            fmt = fc
    count = max(items) + 1 if items else 0
    try:
        count = max(count, int(count_el.get("val"))) if count_el is not None else count
    except (TypeError, ValueError):
        pass
    return [items.get(i, "") for i in range(count)], fmt


def _first(holder: ET.Element | None, *tags: str) -> ET.Element | None:
    """Первый найденный из тегов. Не через `or`: элемент без детей ложен."""
    if holder is None:
        return None
    for tag in tags:
        el = holder.find(tag if tag.startswith(".") else qn(tag))
        if el is not None:
            return el
    return None


def _floats(values: list[str]) -> list[float | None]:
    out: list[float | None] = []
    for v in values:
        try:
            out.append(float(v))
        except ValueError:
            out.append(None)
    return out


def _flag(el: ET.Element | None, tag: str) -> bool | None:
    if el is None:
        return None
    f = el.find(qn(tag))
    if f is None:
        return None
    return f.get("val") in (None, "1", "true")


#: Языки, где дробная часть отделяется запятой, а разряды — пробелом. Какие
#: разделители ставить, PowerPoint решает по языку системы; у нас — по
#: `c:lang` диаграммы: колоды русские.
_COMMA_LANGS = ("ru", "uk", "be", "kk", "de", "fr", "es", "it", "pt", "pl", "cs", "nl", "sv",
                "fi", "nb", "da", "tr", "hu", "ro", "bg", "sr", "hr", "sk", "sl", "lt", "lv", "et")


def _parse_format(code: str) -> tuple[str, str, bool, int, bool] | None:
    """Код формата Excel -> (текст до числа, после, процент, знаков после
    запятой, разряды). None — «General». Кавычки и обратная косая — буквальный
    текст: `0"%"` приписывает знак процента, а не умножает на сто."""
    code = code.split(";")[0]
    if code.strip().lower() in ("", "general"):
        return None
    before: list[str] = []
    after: list[str] = []
    pattern: list[str] = []
    percent = False
    i = 0
    while i < len(code):
        ch = code[i]
        target = after if pattern else before
        if ch == '"':
            j = code.find('"', i + 1)
            j = len(code) if j < 0 else j
            target.append(code[i + 1:j])
            i = j + 1
            continue
        if ch == "\\" and i + 1 < len(code):
            target.append(code[i + 1])
            i += 2
            continue
        if ch == "[":
            j = code.find("]", i + 1)
            i = len(code) if j < 0 else j + 1
            continue
        if ch in "_*":          # отступ шириной знака и заполнитель — пропускаем
            i += 2
            continue
        if ch in "0#?" or (ch in ".," and pattern):
            pattern.append(ch)
        elif ch == "%":
            percent = True
            target.append("%")
        else:
            target.append(ch)
        i += 1
    digits = "".join(pattern).rstrip(",")
    whole, _, frac = digits.partition(".")
    return ("".join(before), "".join(after), percent,
            sum(1 for ch in frac if ch in "0#?"), "," in whole)


def fmt_value(v: float, code: str, comma: bool = False) -> str:
    """Число по коду формата Excel: разряды, знаки после запятой, процент и
    буквальный текст. `comma` — запятая дробной части (русская запись)."""
    dec, group = (",", " ") if comma else (".", ",")
    parsed = _parse_format(code or "General")
    if parsed is None:
        s = f"{v:.10g}"
        if "e" in s:
            s = f"{v:.4g}"
        return s.replace(".", dec)
    before, after, percent, decimals, grouped = parsed
    x = v * 100 if percent else v
    body = f"{abs(x):.{decimals}f}"
    whole, _, frac = body.partition(".")
    if grouped:
        parts = []
        while len(whole) > 3:
            parts.insert(0, whole[-3:])
            whole = whole[:-3]
        parts.insert(0, whole)
        whole = group.join(parts)
    sign = "-" if x < 0 and float(body) != 0 else ""
    return f"{sign}{before}{whole}{dec + frac if frac else ''}{after}"


def _nice(lo: float, hi: float, unit: float | None = None) -> tuple[float, float, float]:
    """Шаг и границы шкалы: около пяти «круглых» делений."""
    if hi <= lo:
        hi = lo + 1
    if unit and unit > 0:
        step = unit
    else:
        raw = (hi - lo) / 5
        mag = 10 ** math.floor(math.log10(raw))
        step = mag
        for m in (1, 2, 2.5, 5, 10):
            step = m * mag
            if step >= raw - 1e-12:
                break
    return math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step, step


class _Canvas:
    def __init__(self, font: str, color: str, comma: bool = False) -> None:
        self.items: list[str] = []
        self.font = font
        self.color = color
        self.comma = comma

    def fmt(self, v: float, code: str | None) -> str:
        return fmt_value(v, code or "General", self.comma)

    def rect(self, x: float, y: float, w: float, h: float, fill: Color | None,
             stroke: str = "") -> None:
        if fill is None and not stroke:
            return
        attrs = f' fill="{fill[0]}"' if fill else ' fill="none"'
        if fill and fill[1] < 0.999:
            attrs += f' fill-opacity="{num(fill[1])}"'
        self.items.append(f'<rect x="{num(x)}" y="{num(y)}" width="{num(max(w, 0))}" '
                          f'height="{num(max(h, 0))}"{attrs}{stroke}/>')

    def text(self, x: float, y: float, s: str, size: float, anchor: str = "middle",
             rotate: float = 0, bold: bool = False, color: str | None = None) -> None:
        tr = f' transform="rotate({num(rotate)} {num(x)} {num(y)})"' if rotate else ""
        weight = ' font-weight="700"' if bold else ""
        self.items.append(f'<text x="{num(x)}" y="{num(y)}" dy="0.35em" font-size="{num(size)}" '
                          f'text-anchor="{anchor}" fill="{color or self.color}"{weight}{tr}>'
                          f"{html.escape(s, quote=False)}</text>")

    def raw(self, s: str) -> None:
        self.items.append(s)


def _accent(ctx: Ctx, i: int) -> Color:
    """Цвет ряда по умолчанию: акценты темы по кругу, второй круг темнее."""
    role = f"accent{i % 6 + 1}"
    el = ET.Element(qn("a:schemeClr"), {"val": role})
    if i >= 6:
        ET.SubElement(el, qn("a:lumMod"), {"val": "60000"})
    holder = ET.Element(qn("a:solidFill"))
    holder.append(el)
    return child_color(holder, ctx) or ("#4472C4", 1.0)


def _series(ser: ET.Element, i: int, ctx: Ctx, show_val_chart: bool,
            marker_chart: bool, chart_lbls: ET.Element | None = None) -> _Series:
    name = ""
    tx = ser.find(qn("c:tx"))
    if tx is not None:
        vals, _ = _pts(tx)
        if vals:
            name = vals[0]
        else:
            v = tx.find(qn("c:v"))
            name = (v.text or "") if v is not None else ""
    cats, _ = _pts(ser.find(qn("c:cat")))
    if not cats:
        xs_raw, _ = _pts(ser.find(qn("c:xVal")))
        cats = xs_raw
    raw_vals, fmt = _pts(_first(ser, "c:val", "c:yVal"))
    xs_raw, _ = _pts(ser.find(qn("c:xVal")))
    s = _Series(name=name or f"Ряд {i + 1}", cats=cats, vals=_floats(raw_vals),
                xs=_floats(xs_raw), fmt=fmt)
    idx_el = ser.find(qn("c:idx"))
    try:
        order = int(idx_el.get("val")) if idx_el is not None else i
    except (TypeError, ValueError):
        order = i
    base = _accent(ctx, order)
    sp = ser.find(qn("c:spPr"))
    f = fill_of(find_fill(sp), ctx) if sp is not None else None
    s.no_fill = f is not None and f.kind == "none"
    s.fill = first_color(f) or base
    ln = sp.find(qn("a:ln")) if sp is not None else None
    lf = fill_of(find_fill(ln), ctx) if ln is not None else None
    if lf is not None:
        # Линия задана явно: её цвет, либо её нет вовсе.
        s.line = first_color(lf) if lf.kind != "none" else None
    elif f is not None and f.kind != "none":
        s.line = s.fill
    else:
        s.line = base
    if ln is not None and ln.get("w"):
        s.line_w = integer(ln.get("w"), 28575) / EMU_PER_PT
    for dpt in ser.findall(qn("c:dPt")):
        try:
            pidx = int(dpt.find(qn("c:idx")).get("val"))
        except (AttributeError, TypeError, ValueError):
            continue
        dsp = dpt.find(qn("c:spPr"))
        pf = first_color(fill_of(find_fill(dsp), ctx)) if dsp is not None else None
        if pf:
            s.points[pidx] = pf
    own = ser.find(qn("c:dLbls"))
    lbl = _flag(own, "c:showVal")
    s.show_val = lbl if lbl is not None else show_val_chart
    for holder in (own, chart_lbls):
        if holder is None:
            continue
        nf = holder.find(qn("c:numFmt"))
        if s.label_fmt is None and nf is not None and nf.get("sourceLinked") != "1":
            s.label_fmt = nf.get("formatCode")
        pos = holder.find(qn("c:dLblPos"))
        if s.label_pos is None and pos is not None:
            s.label_pos = pos.get("val")
        sep = holder.find(qn("c:separator"))
        if sep is not None and sep.text is not None and s.sep == ", ":
            s.sep = sep.text
    sym = ser.find(f"{qn('c:marker')}/{qn('c:symbol')}")
    s.marker = marker_chart and not (sym is not None and sym.get("val") == "none")
    size = ser.find(f"{qn('c:marker')}/{qn('c:size')}")
    if size is not None:
        s.marker_r = integer(size.get("val"), 5) / 2
    return s


def _wrap_label(s: str, width: float, size: float) -> list[str]:
    """Подпись категории в несколько строк по ширине полосы (оценка ширины
    знака — 0.55 кегля)."""
    per = max(int(width / (size * 0.55)), 1)
    if len(s) <= per:
        return [s]
    lines, cur = [], ""
    for word in s.split():
        if cur and len(cur) + 1 + len(word) > per:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    if len(lines) > 3:
        lines = lines[:3]
        lines[-1] = lines[-1][: max(per - 1, 1)] + "…"
    return lines


def render(part: str, ctx: Ctx, w_emu: float, h_emu: float) -> str:
    doc = ctx.doc
    root = doc.pkg.xml(part)
    chart = root.find(qn("c:chart"))
    if chart is None:
        doc.note("chart without c:chart")
        return ""
    W, H = w_emu / EMU_PER_PT, h_emu / EMU_PER_PT
    plot = chart.find(qn("c:plotArea"))

    tx_color = child_color(ET.fromstring(
        '<a:solidFill xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:schemeClr val="tx1"/></a:solidFill>'), ctx) or ("#000000", 1.0)
    base = 10.0
    face = ctx.theme.minor_latin if ctx.theme else None
    defr = root.find(f"{qn('c:txPr')}/{qn('a:p')}/{qn('a:pPr')}/{qn('a:defRPr')}")
    if defr is not None:
        base = integer(defr.get("sz"), 1000) / 100
        c = first_color(fill_of(find_fill(defr), ctx))
        if c:
            tx_color = c
        lat = defr.find(qn("a:latin"))
        if lat is not None and lat.get("typeface"):
            tf = lat.get("typeface")
            face = ctx.theme.typeface(tf) if (tf.startswith("+") and ctx.theme) else tf
    lang = root.find(qn("c:lang"))
    comma = (lang.get("val") or "").lower().split("-")[0] in _COMMA_LANGS if lang is not None else False
    cv = _Canvas(font_stack(face), tx_color[0], comma)

    sp = root.find(qn("c:spPr"))
    bg = fill_of(find_fill(sp), ctx) if sp is not None else None
    if bg is not None and bg.kind == "solid":
        cv.rect(0, 0, W, H, bg.color)

    kinds = [el for el in (plot if plot is not None else []) if local_name(el.tag).endswith("Chart")]
    if not kinds:
        doc.note("empty chart")
        return _svg(cv, W, H)
    kind_el = kinds[0]
    kind = local_name(kind_el.tag)
    for extra in kinds[1:]:
        doc.note(f"chart combo {local_name(extra.tag)}")
    if kind not in _SUPPORTED:
        doc.note(f"chart {kind}")
    if kind in ("bar3DChart", "line3DChart", "area3DChart", "pie3DChart", "ofPieChart"):
        doc.note(f"chart {kind}")

    show_val_chart = bool(_flag(kind_el.find(qn("c:dLbls")), "c:showVal"))
    marker_chart = bool(_flag(kind_el, "c:marker")) if kind.startswith("line") else True
    if kind == "scatterChart":
        style = kind_el.find(qn("c:scatterStyle"))
        marker_chart = style is None or "Marker" in (style.get("val") or "lineMarker") \
            or (style.get("val") or "") == "marker"
    series = [_series(s, i, ctx, show_val_chart, marker_chart, kind_el.find(qn("c:dLbls")))
              for i, s in enumerate(kind_el.findall(qn("c:ser")))]

    pad = base * 0.6
    top, bottom, left, right = pad, H - pad, pad, W - pad

    # Название: явное, либо имя единственного ряда, если автоназвание не снято.
    title = chart.find(qn("c:title"))
    auto_deleted = _flag(chart, "c:autoTitleDeleted")
    title_text = ""
    title_size = base * 1.4
    if title is not None:
        title_text = _text_of(title.find(f"{qn('c:tx')}/{qn('c:rich')}"))
        rpr = _first(title, f".//{qn('a:defRPr')}", f".//{qn('a:rPr')}")
        if rpr is not None and rpr.get("sz"):
            title_size = integer(rpr.get("sz"), round(title_size * 100)) / 100
        if not title_text and len(series) == 1:
            title_text = series[0].name
    elif not auto_deleted and len(series) == 1:
        title_text = series[0].name
    if title_text:
        cv.text(W / 2, top + title_size * 0.7, title_text, title_size)
        top += title_size * 1.6

    pie_like = kind in ("pieChart", "pie3DChart", "doughnutChart", "ofPieChart")
    vary = _flag(kind_el, "c:varyColors")
    legend = chart.find(qn("c:legend"))
    if legend is not None:
        pos_el = legend.find(qn("c:legendPos"))
        pos = pos_el.get("val") if pos_el is not None else "r"
        if pie_like or (vary and len(series) == 1 and kind in ("barChart", "bar3DChart")):
            s0 = series[0] if series else None
            entries = [(c, (s0.points.get(i) if s0 else None) or _accent(ctx, i))
                       for i, c in enumerate(s0.cats if s0 else [])]
        else:
            entries = [(s.name, s.fill if kind not in ("lineChart", "line3DChart",
                                                       "scatterChart") else (s.line or s.fill))
                       for s in series]
        top, bottom, left, right = _legend(cv, entries, pos, base, top, bottom, left, right)

    if pie_like:
        _pie(cv, kind, kind_el, series, ctx, base, left, top, right, bottom)
    elif kind in _SUPPORTED:
        _axes_chart(cv, ctx, kind, kind_el, plot, series, base, left, top, right, bottom)
    else:
        cv.rect(left, top, right - left, bottom - top, None,
                f' stroke="{tx_color[0]}" stroke-width="0.75"')
        for i, s in enumerate(series[:8]):
            cv.text((left + right) / 2, top + base * (1.5 + 1.4 * i), s.name, base)
    return _svg(cv, W, H)


def _svg(cv: _Canvas, W: float, H: float) -> str:
    return (f'<svg class="ch" viewBox="0 0 {num(W)} {num(H)}" '
            f'style="font-family:{html.escape(cv.font, quote=True)}">{"".join(cv.items)}</svg>')


def _legend(cv: _Canvas, entries: list[tuple[str, Color | None]], pos: str, size: float,
            top: float, bottom: float, left: float, right: float
            ) -> tuple[float, float, float, float]:
    sw = size * 0.8
    if pos in ("b", "t"):
        widths = [sw + size * 0.4 + len(t) * size * 0.55 + size for t, _ in entries]
        total = sum(widths)
        x = (left + right) / 2 - total / 2
        y = bottom - size * 0.8 if pos == "b" else top + size * 0.8
        for (t, c), wd in zip(entries, widths):
            cv.rect(x, y - sw / 2, sw, sw, c)
            cv.text(x + sw + size * 0.3, y, t, size, anchor="start")
            x += wd
        if pos == "b":
            return top, bottom - size * 2, left, right
        return top + size * 2, bottom, left, right
    longest = max((len(t) for t, _ in entries), default=0)
    width = min(sw + size * 0.6 + longest * size * 0.55, (right - left) * 0.4)
    x = right - width if pos in ("r", "tr") else left
    y = (top + bottom) / 2 - len(entries) * size * 1.4 / 2 + size * 0.7
    for t, c in entries:
        cv.rect(x, y - sw / 2, sw, sw, c)
        cv.text(x + sw + size * 0.3, y, t, size, anchor="start")
        y += size * 1.4
    if pos in ("r", "tr"):
        return top, bottom, left, right - width - size
    return top, bottom, left + width + size, right


def _stroke(ln: ET.Element | None, ctx: Ctx, default: str) -> str:
    """Атрибуты линии SVG из `a:ln` оси или сетки: цвет с прозрачностью и
    толщина. Нет элемента — умолчание; `a:noFill` — линии нет (пусто)."""
    if ln is None:
        return default
    f = fill_of(find_fill(ln), ctx)
    if f is not None and f.kind == "none":
        return ""
    c = first_color(f)
    if c is None:
        return default
    w = integer(ln.get("w"), 9525) / EMU_PER_PT
    out = f' stroke="{c[0]}" stroke-width="{num(max(w, 0.25))}"'
    if c[1] < 0.999:
        out += f' stroke-opacity="{num(c[1])}"'
    return out


def _pie(cv: _Canvas, kind: str, kind_el: ET.Element, series: list[_Series], ctx: Ctx,
         base: float, left: float, top: float, right: float, bottom: float) -> None:
    if not series:
        return
    s = series[0]
    vals = [max(v or 0.0, 0.0) for v in s.vals]
    total = sum(vals)
    if total <= 0:
        return
    cx, cy = (left + right) / 2, (top + bottom) / 2
    r = max(min(right - left, bottom - top) / 2 - base * 0.3, 1.0)
    hole = 0.0
    if kind == "doughnutChart":
        hs = kind_el.find(qn("c:holeSize"))
        try:
            hole = (int(hs.get("val")) if hs is not None else 50) / 100
        except (TypeError, ValueError):
            hole = 0.5
    first = kind_el.find(qn("c:firstSliceAng"))
    try:
        ang = float(first.get("val")) if first is not None else 0.0
    except (TypeError, ValueError):
        ang = 0.0
    vary = _flag(kind_el, "c:varyColors")
    vary = True if vary is None else vary
    dl = kind_el.find(qn("c:dLbls"))
    ser_dl = None
    for ser in kind_el.findall(qn("c:ser"))[:1]:
        ser_dl = ser.find(qn("c:dLbls"))
    show_val = s.show_val
    show_pct = bool(_flag(ser_dl, "c:showPercent") or _flag(dl, "c:showPercent"))
    show_cat = bool(_flag(ser_dl, "c:showCatName") or _flag(dl, "c:showCatName"))
    labels = []
    for i, v in enumerate(vals):
        if v <= 0:
            continue
        sweep = v / total * 360
        a0, a1 = math.radians(ang - 90), math.radians(ang + sweep - 90)
        color = s.points.get(i) or (_accent(ctx, i) if vary else s.fill) or ("#4472C4", 1.0)
        large = 1 if sweep > 180 else 0
        x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        if sweep >= 359.999:
            d = (f"M{num(cx - r)} {num(cy)}A{num(r)} {num(r)} 0 1 1 {num(cx + r)} {num(cy)}"
                 f"A{num(r)} {num(r)} 0 1 1 {num(cx - r)} {num(cy)}Z")
            if hole:
                ri = r * hole
                d += (f"M{num(cx - ri)} {num(cy)}A{num(ri)} {num(ri)} 0 1 0 {num(cx + ri)} "
                      f"{num(cy)}A{num(ri)} {num(ri)} 0 1 0 {num(cx - ri)} {num(cy)}Z")
        elif hole:
            ri = r * hole
            xi0, yi0 = cx + ri * math.cos(a0), cy + ri * math.sin(a0)
            xi1, yi1 = cx + ri * math.cos(a1), cy + ri * math.sin(a1)
            d = (f"M{num(x0)} {num(y0)}A{num(r)} {num(r)} 0 {large} 1 {num(x1)} {num(y1)}"
                 f"L{num(xi1)} {num(yi1)}A{num(ri)} {num(ri)} 0 {large} 0 {num(xi0)} {num(yi0)}Z")
        else:
            d = (f"M{num(cx)} {num(cy)}L{num(x0)} {num(y0)}A{num(r)} {num(r)} 0 {large} 1 "
                 f"{num(x1)} {num(y1)}Z")
        cv.raw(f'<path d="{d}" fill="{color[0]}" fill-rule="evenodd" stroke="#FFFFFF" '
               f'stroke-width="{num(max(r / 150, 0.5))}"/>')
        mid = math.radians(ang + sweep / 2 - 90)
        rr = r * ((1 + hole) / 2 if hole else 0.65)
        parts = []
        if show_cat and i < len(s.cats):
            parts.append(s.cats[i])
        if show_val:
            parts.append(cv.fmt(v, s.label_fmt or s.fmt))
        if show_pct:
            parts.append(f"{round(v / total * 100)}%")
        if parts:
            labels.append((cx + rr * math.cos(mid), cy + rr * math.sin(mid), s.sep.join(parts),
                           color))
        ang += sweep
    for x, y, t, c in labels:
        cv.text(x, y, t, base, color="#FFFFFF" if _dark(c) else None)


def _dark(c: Color) -> bool:
    """Подпись на тёмном секторе — белая, на светлом — цвета текста."""
    r, g, b = (int(c[0][i:i + 2], 16) for i in (1, 3, 5))
    return 0.299 * r + 0.587 * g + 0.114 * b < 150


def _axes_chart(cv: _Canvas, ctx: Ctx, kind: str, kind_el: ET.Element, plot: ET.Element,
                series: list[_Series], base: float, left: float, top: float, right: float,
                bottom: float) -> None:
    horizontal = False
    grouping = "clustered" if kind.startswith("bar") else "standard"
    g_el = kind_el.find(qn("c:grouping"))
    if g_el is not None and g_el.get("val"):
        grouping = g_el.get("val")
    if kind.startswith("bar"):
        d = kind_el.find(qn("c:barDir"))
        horizontal = d is not None and d.get("val") == "bar"
    stacked = grouping in ("stacked", "percentStacked")
    percent = grouping == "percentStacked"
    ncat = max((max(len(s.cats), len(s.vals)) for s in series), default=0)
    if ncat == 0:
        return
    cats = next((s.cats for s in series if s.cats), [str(i + 1) for i in range(ncat)])
    cats = cats + [""] * (ncat - len(cats))

    val_ax = plot.find(qn("c:valAx")) if plot is not None else None
    cat_ax = plot.find(qn("c:catAx")) if plot is not None else None
    if kind == "scatterChart" and plot is not None:
        axes = plot.findall(qn("c:valAx"))
        val_ax = axes[1] if len(axes) > 1 else val_ax
        cat_ax = axes[0] if axes else None

    # Диапазон значений.
    if stacked:
        pos = [sum(max(s.vals[i] or 0, 0) for s in series if i < len(s.vals)) for i in range(ncat)]
        neg = [sum(min(s.vals[i] or 0, 0) for s in series if i < len(s.vals)) for i in range(ncat)]
        lo, hi = min(neg + [0.0]), max(pos + [0.0])
        if percent:
            lo, hi = (-1.0 if lo < 0 else 0.0), 1.0
    else:
        vals = [v for s in series for v in s.vals if v is not None]
        lo, hi = min(vals + [0.0]), max(vals + [0.0])
    scaling = val_ax.find(qn("c:scaling")) if val_ax is not None else None
    unit = None
    if val_ax is not None:
        mu = val_ax.find(qn("c:majorUnit"))
        try:
            unit = float(mu.get("val")) if mu is not None else None
        except (TypeError, ValueError):
            unit = None
    lo, hi, step = _nice(lo, hi, unit)
    for tag in ("c:min", "c:max"):
        el = scaling.find(qn(tag)) if scaling is not None else None
        if el is not None:
            try:
                if tag == "c:min":
                    lo = float(el.get("val"))
                else:
                    hi = float(el.get("val"))
            except (TypeError, ValueError):
                pass
    if hi <= lo:
        hi = lo + step
    fmt_el = val_ax.find(qn("c:numFmt")) if val_ax is not None else None
    fmt = fmt_el.get("formatCode") if fmt_el is not None and fmt_el.get("formatCode") else (
        "0%" if percent else (series[0].fmt if series else "General"))
    val_deleted = bool(_flag(val_ax, "c:delete"))
    cat_deleted = bool(_flag(cat_ax, "c:delete"))

    ticks = []
    t = lo
    while t <= hi + step * 1e-6 and len(ticks) < 50:
        ticks.append(t)
        t += step
    labels = [cv.fmt(v, fmt) for v in ticks]

    # Заголовки осей.
    vtitle = _text_of(val_ax.find(f"{qn('c:title')}/{qn('c:tx')}/{qn('c:rich')}")) \
        if val_ax is not None else ""
    ctitle = _text_of(cat_ax.find(f"{qn('c:title')}/{qn('c:tx')}/{qn('c:rich')}")) \
        if cat_ax is not None else ""

    # Поля под подписи.
    lab_w = max((len(x) for x in labels), default=1) * base * 0.55 + base * 0.6
    if not horizontal:
        if vtitle:
            cv.text(left + base * 0.7, (top + bottom) / 2, vtitle, base, rotate=-90)
            left += base * 1.6
        if ctitle:
            cv.text((left + right) / 2, bottom - base * 0.7, ctitle, base)
            bottom -= base * 1.6
        if not val_deleted:
            left += lab_w
        band = (right - left) / ncat
        cat_lines = max((len(_wrap_label(c, band, base)) for c in cats), default=1)
        if not cat_deleted:
            bottom -= base * (1.3 * cat_lines + 0.4)
    else:
        if vtitle:
            cv.text((left + right) / 2, bottom - base * 0.7, vtitle, base)
            bottom -= base * 1.6
        if not val_deleted:
            bottom -= base * 1.6
        cat_w = max((len(c) for c in cats), default=1) * base * 0.55 + base * 0.6
        if not cat_deleted:
            left += min(cat_w, (right - left) * 0.35)
    if right - left < 4 or bottom - top < 4:
        return

    def vpos(v: float) -> float:
        frac = (v - lo) / (hi - lo)
        return (left + frac * (right - left)) if horizontal else (bottom - frac * (bottom - top))

    grid_el = val_ax.find(qn("c:majorGridlines")) if val_ax is not None else None
    grid = _stroke(grid_el.find(f"{qn('c:spPr')}/{qn('a:ln')}") if grid_el is not None else None,
                   ctx, ' stroke="#D9D9D9" stroke-width="0.75"') if grid_el is not None else ""
    for v, lab in zip(ticks, labels):
        p = vpos(v)
        if grid:
            if horizontal:
                cv.raw(f'<line x1="{num(p)}" y1="{num(top)}" x2="{num(p)}" y2="{num(bottom)}"{grid}/>')
            else:
                cv.raw(f'<line x1="{num(left)}" y1="{num(p)}" x2="{num(right)}" y2="{num(p)}"{grid}/>')
        if not val_deleted:
            if horizontal:
                cv.text(p, bottom + base * 0.8, lab, base)
            else:
                cv.text(left - base * 0.4, p, lab, base, anchor="end")

    zero = vpos(min(max(0.0, lo), hi))
    n = ncat
    band = ((bottom - top) if horizontal else (right - left)) / n

    def cpos(i: int) -> float:
        # У горизонтальных полос первая категория внизу, как у PowerPoint.
        return (bottom - (i + 0.5) * band) if horizontal else (left + (i + 0.5) * band)

    if not cat_deleted:
        for i, c in enumerate(cats):
            if horizontal:
                cv.text(left - base * 0.4, cpos(i), c, base, anchor="end")
            else:
                lines = _wrap_label(c, band, base)
                for k, line in enumerate(lines):
                    cv.text(cpos(i), bottom + base * (0.9 + 1.3 * k), line, base)

    if kind.startswith("bar"):
        gap_el = kind_el.find(qn("c:gapWidth"))
        ov_el = kind_el.find(qn("c:overlap"))
        try:
            gap = float(gap_el.get("val")) / 100 if gap_el is not None else 1.5
        except (TypeError, ValueError):
            gap = 1.5
        try:
            overlap = float(ov_el.get("val")) / 100 if ov_el is not None else (1.0 if stacked else 0.0)
        except (TypeError, ValueError):
            overlap = 0.0
        k = 1 if stacked else max(len(series), 1)
        bw = band / (k - (k - 1) * overlap + gap)
        acc_pos = [0.0] * n
        acc_neg = [0.0] * n
        totals = [sum(abs(s.vals[i] or 0) for s in series if i < len(s.vals)) or 1.0
                  for i in range(n)]
        for si, s in enumerate(series):
            for i in range(n):
                v = s.vals[i] if i < len(s.vals) else None
                if v is None:
                    continue
                if percent:
                    v = v / totals[i]
                if stacked:
                    start = acc_pos[i] if v >= 0 else acc_neg[i]
                    end = start + v
                    if v >= 0:
                        acc_pos[i] = end
                    else:
                        acc_neg[i] = end
                    offset = -bw / 2
                else:
                    start, end = 0.0, v
                    offset = -band / 2 + gap * bw / 2 + si * bw * (1 - overlap)
                color = s.points.get(i) or s.fill
                if s.no_fill:
                    color = None
                p0, p1 = vpos(max(min(start, hi), lo)), vpos(max(min(end, hi), lo))
                c0 = cpos(i)
                if horizontal:
                    # Первая серия — ближе к началу оси, то есть ниже.
                    y = c0 - offset - bw
                    cv.rect(min(p0, p1), y, abs(p1 - p0), bw, color)
                    lx, ly = max(p0, p1) + base * 0.3, y + bw / 2
                    anchor = "start"
                else:
                    x = c0 + offset
                    cv.rect(x, min(p0, p1), bw, abs(p1 - p0), color)
                    lx, ly = x + bw / 2, min(p0, p1) - base * 0.6
                    anchor = "middle"
                if s.show_val:
                    shown = s.vals[i] if not percent else v
                    if s.label_pos in ("ctr", "inEnd", "inBase") and not stacked:
                        if horizontal:
                            lx, anchor = (p0 + p1) / 2, "middle"
                        else:
                            ly = (p0 + p1) / 2
                    if stacked:
                        if horizontal:
                            lx, anchor = (p0 + p1) / 2, "middle"
                        else:
                            ly = (p0 + p1) / 2
                    cv.text(lx, ly, cv.fmt(shown if shown is not None else 0.0,
                                           (s.label_fmt or s.fmt) if not percent else "0%"),
                            base, anchor=anchor)
    else:
        area = kind.startswith("area")
        acc = [0.0] * n
        for s in series:
            pts = []
            for i in range(n):
                v = s.vals[i] if i < len(s.vals) else None
                if v is None:
                    continue
                if stacked:
                    acc[i] += v
                    v = acc[i]
                if kind == "scatterChart" and s.xs and i < len(s.xs) and s.xs[i] is not None:
                    xs = [x for x in s.xs if x is not None]
                    x_lo, x_hi = min(xs), max(xs)
                    span = (x_hi - x_lo) or 1.0
                    x = left + (s.xs[i] - x_lo) / span * (right - left)
                else:
                    x = cpos(i)
                pts.append((x, vpos(max(min(v, hi), lo)), v))
            if not pts:
                continue
            color = s.line or s.fill or ("#4472C4", 1.0)
            if area:
                d = f"M{num(pts[0][0])} {num(zero)}" + "".join(
                    f"L{num(x)} {num(y)}" for x, y, _ in pts) + f"L{num(pts[-1][0])} {num(zero)}Z"
                fc = s.fill or color
                cv.raw(f'<path d="{d}" fill="{fc[0]}" fill-opacity="{num(fc[1])}"/>')
            elif not (kind == "scatterChart" and s.line is None and not s.marker):
                d = "M" + "L".join(f"{num(x)} {num(y)}" for x, y, _ in pts)
                if kind != "scatterChart" or s.line is not None:
                    cv.raw(f'<path d="{d}" fill="none" stroke="{color[0]}" '
                           f'stroke-width="{num(s.line_w)}" stroke-linejoin="round"/>')
            if s.marker and not area:
                for x, y, _ in pts:
                    cv.raw(f'<circle cx="{num(x)}" cy="{num(y)}" r="{num(s.marker_r)}" '
                           f'fill="{(s.fill or color)[0]}"/>')
            if s.show_val:
                for x, y, v in pts:
                    dy = base * 0.9 if s.label_pos in (None, "t", "outEnd") else (
                        -base * 0.9 if s.label_pos == "b" else 0.0)
                    cv.text(x, y - dy, cv.fmt(v, s.label_fmt or s.fmt), base)

    # Ось категорий — линия по нулю, если её не убрали (`a:noFill`).
    axis = _stroke(cat_ax.find(f"{qn('c:spPr')}/{qn('a:ln')}") if cat_ax is not None else None,
                   ctx, ' stroke="#BFBFBF" stroke-width="0.75"')
    if axis and not cat_deleted:
        if horizontal:
            cv.raw(f'<line x1="{num(zero)}" y1="{num(top)}" x2="{num(zero)}" y2="{num(bottom)}"{axis}/>')
        else:
            cv.raw(f'<line x1="{num(left)}" y1="{num(zero)}" x2="{num(right)}" y2="{num(zero)}"{axis}/>')
