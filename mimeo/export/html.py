"""Готовая колода `.pptx` -> один самодостаточный `.html`.

ТЗ требует выгрузки в .html, .pptx и .pdf; организаторы на сессии вопросов
сказали про HTML, что вёрстка надёжнее скриншотов, а редактируемость не нужна
(`CTX-QA`). Поэтому слайд здесь — разметка: текст остаётся текстом, фигуры —
блоками и SVG, картинки встроены. Слайд никогда не бывает одной картинкой.

Чистый Python на стандартной библиотеке (`ADR-0001`): эксперты запустят движок
на Linux, где нет ни PowerPoint, ни LibreOffice.

Порядок рисования слайда повторяет PowerPoint: фон (слайд, иначе макет, иначе
мастер), декор мастера, декор макета, фигуры слайда. Плейсхолдеры макета и
мастера не рисуются — они только отдают положение и стили плейсхолдерам слайда
(`DOM-GEOM §5`, `DOM-TEXT §2`).

Выход детерминирован: тот же файл даёт байт в байт тот же HTML — порядок
обхода задан документом, счётчики сквозные, времени в выводе нет.
"""

from __future__ import annotations

import copy
import html
import math
import os
from dataclasses import dataclass, replace
from xml.etree import ElementTree as ET

from ..analyze.color import ColorContext
from ..analyze.deck import Deck, LayoutInfo, MasterInfo, Placeholder, SlideInfo, load_deck
from ..opc.package import Package
from ..oxml.ns import local_name, qn
from . import _chart, _table
from ._base import EMU_PER_PT, Ctx, Doc, color, integer, load_fmt, pct
from ._geom import Geometry, num, shape_geometry
from ._paint import (
    Fill,
    Line,
    css_background,
    fill_of,
    find_fill,
    line_of,
    marker_defs,
    style_fill,
    style_line,
    svg_fill,
    svg_stroke,
)
from ._text import TextStyles, body_layer, pseudo_level


@dataclass(frozen=True)
class HtmlReport:
    """Итог выгрузки. `unsupported` — что пришлось приблизить или пропустить,
    по строке «что ×сколько», по алфавиту: молча терять содержимое нельзя."""

    output: str | None
    slides: int
    text_chars: int                 # знаков текста слайдов, выведенных текстом
    images: int                     # различных картинок, встроенных в файл
    unsupported: tuple[str, ...]


def to_html(pptx_path: str) -> tuple[str, HtmlReport]:
    """HTML строкой и отчёт. Файл не пишется."""
    with Package(os.fspath(pptx_path)) as pkg:
        renderer = _Renderer(pkg, os.fspath(pptx_path))
        text = renderer.render()
        return text, renderer.report()


def render_html(pptx_path: str, out_path: str) -> HtmlReport:
    """Пишет HTML в `out_path` (UTF-8, переводы строк `\\n`) и возвращает отчёт."""
    text, report = to_html(pptx_path)
    folder = os.path.dirname(os.fspath(out_path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return replace(report, output=os.fspath(out_path))


# --- положение фигур ------------------------------------------------------------


@dataclass(frozen=True)
class _Box:
    """Рамка фигуры: левый верхний угол, размер (EMU), поворот и отражения."""

    x: float
    y: float
    w: float
    h: float
    rot: float = 0.0
    fh: bool = False
    fv: bool = False


@dataclass(frozen=True)
class _Group:
    box: _Box
    ch: tuple[float, float, float, float]      # chOff x, y, chExt cx, cy
    fill: ET.Element | None                    # заливка группы для a:grpFill


def _xfrm(el: ET.Element | None) -> _Box | None:
    if el is None:
        return None
    off, ext = el.find(qn("a:off")), el.find(qn("a:ext"))
    if off is None or ext is None:
        return None
    return _Box(
        x=float(integer(off.get("x"), 0)), y=float(integer(off.get("y"), 0)),
        w=float(max(integer(ext.get("cx"), 0), 0)), h=float(max(integer(ext.get("cy"), 0), 0)),
        rot=integer(el.get("rot"), 0) / 60000,
        fh=el.get("flipH") in ("1", "true"), fv=el.get("flipV") in ("1", "true"))


def _to_parent(b: _Box, g: _Group) -> _Box:
    """Рамка ребёнка группы -> рамка в системе родителя группы (`DOM-GEOM §4`).

    Масштаб группы применяется к центру и размерам рамки ребёнка, а не к его
    повёрнутому контуру: так же поступает PowerPoint — перекоса у фигур не
    бывает. Затем отражения и поворот группы вокруг её центра; отражение по
    одной оси меняет знак угла ребёнка."""
    chx, chy, chw, chh = g.ch
    sx = g.box.w / chw if chw else 1.0
    sy = g.box.h / chh if chh else 1.0
    cx = g.box.x + (b.x + b.w / 2 - chx) * sx
    cy = g.box.y + (b.y + b.h / 2 - chy) * sy
    w, h = b.w * sx, b.h * sy
    gcx, gcy = g.box.x + g.box.w / 2, g.box.y + g.box.h / 2
    dx, dy = cx - gcx, cy - gcy
    if g.box.fh:
        dx = -dx
    if g.box.fv:
        dy = -dy
    rot = -b.rot if g.box.fh != g.box.fv else b.rot
    a = math.radians(g.box.rot)
    cx = gcx + dx * math.cos(a) - dy * math.sin(a)
    cy = gcy + dx * math.sin(a) + dy * math.cos(a)
    return _Box(cx - w / 2, cy - h / 2, w, h, (rot + g.box.rot) % 360,
                b.fh != g.box.fh, b.fv != g.box.fv)


def _place(b: _Box, groups: tuple[_Group, ...]) -> _Box:
    for g in reversed(groups):
        b = _to_parent(b, g)
    return b


# --- плейсхолдеры ------------------------------------------------------------------

_TITLE = ("title", "ctrTitle")
_CHROME = ("dt", "ftr", "sldNum", "hdr")


def _ph(el: ET.Element) -> ET.Element | None:
    for nv in el:
        if local_name(nv.tag).startswith("nv"):
            return nv.find(f"{qn('p:nvPr')}/{qn('p:ph')}")
    return None


def _layout_ph(layout: LayoutInfo | None, ph_type: str, idx: int | None) -> Placeholder | None:
    """Сопоставление по `idx`, затем по типу; заголовок и титульный заголовок
    взаимозаменяемы, как и тело с объектом."""
    if layout is None:
        return None
    if idx is not None and idx in layout.by_idx:
        return layout.by_idx[idx]
    if ph_type in layout.by_type:
        return layout.by_type[ph_type]
    for group in (_TITLE, ("body", "obj", "subTitle")):
        if ph_type in group:
            for alt in group:
                if alt in layout.by_type:
                    return layout.by_type[alt]
    return None


def _master_ph(master: MasterInfo | None, ph_type: str) -> Placeholder | None:
    """Макет наследует мастер по типу: все «телесные» плейсхолдеры — от `body`."""
    if master is None:
        return None
    kind = "title" if ph_type in _TITLE else ph_type if ph_type in _CHROME else "body"
    return master.by_type.get(kind)


def _txstyle(master: MasterInfo | None, ph_type: str | None) -> ET.Element | None:
    """Стиль мастера по типу плейсхолдера (`DOM-TEXT §2`, уровень 6)."""
    if master is None or master.txstyles is None:
        return None
    if ph_type in _TITLE:
        name = "p:titleStyle"
    elif ph_type is not None and ph_type not in _CHROME:
        name = "p:bodyStyle"
    else:
        name = "p:otherStyle"
    return master.txstyles.find(qn(name))


# --- выбор ветки mc:AlternateContent ------------------------------------------------

#: Префиксы, чьи ветки `mc:Choice` мы читаем как обычный DrawingML: расширения
#: в них нам не мешают. VML (`v`) и прочее — нет, тогда берём `mc:Fallback`.
_MC_OK = {"p14", "p15", "a14", "a16", "asvg", "thm15", "x14", "p159", "a15"}
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _alternate(el: ET.Element) -> list[ET.Element]:
    fallback = None
    for branch in el:
        name = local_name(branch.tag)
        if name == "Choice":
            req = set((branch.get("Requires") or "").split())
            if req and req <= _MC_OK:
                return list(branch)
        elif name == "Fallback":
            fallback = branch
    return list(fallback) if fallback is not None else []


# --- отрисовка -----------------------------------------------------------------------

_CSS = """*,*::before,*::after{box-sizing:border-box}
html{background:#e9ebef}
body{margin:0;padding:24px 16px 48px;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
.deck{max-width:1200px;margin:0 auto;display:flex;flex-direction:column;gap:32px}
.page{margin:0}
.no{font:13px/1.4 system-ui,sans-serif;color:#5f6673;margin:0 0 6px 2px}
.slide{position:relative;overflow:hidden;container-type:inline-size;background:#fff;
box-shadow:0 1px 3px rgba(0,0,0,.18),0 6px 20px rgba(0,0,0,.08)}
.slide .s,.slide .f,.slide .i,.slide .t,.slide .bg,.slide svg{position:absolute}
.slide .f,.slide .bg{inset:0;overflow:hidden}
.slide .i{background-size:100% 100%;background-repeat:no-repeat}
.slide svg{overflow:visible}
.slide svg.ch{inset:0;width:100%;height:100%;overflow:hidden}
.slide .t{display:flex;flex-direction:column}
.slide .t p,.slide .tb p{margin:0;white-space:pre-wrap;overflow-wrap:break-word}
.slide .t.nw p{white-space:pre}
.slide .bu{display:inline-block;text-indent:0;white-space:pre}
.slide .tb{position:absolute;left:0;top:0;width:100%;border-collapse:collapse;table-layout:fixed}
.slide .tb td{overflow-wrap:break-word}
.slide a{color:inherit}
@media print{html,body{background:none;padding:0}.deck{max-width:none;gap:0}
.page{break-after:page}.no{display:none}.slide{box-shadow:none}}"""

_JS = ("(function(){var s=document.querySelectorAll('.page');"
       "document.addEventListener('keydown',function(e){if(e.altKey||e.ctrlKey||e.metaKey)return;"
       "var d={PageDown:1,ArrowRight:1,ArrowDown:0,PageUp:-1,ArrowLeft:-1}[e.key];"
       "if(!d)return;var c=0,b=1e9;for(var i=0;i<s.length;i++){"
       "var t=Math.abs(s[i].getBoundingClientRect().top);if(t<b){b=t;c=i}}"
       "var n=Math.min(Math.max(c+d,0),s.length-1);s[n].scrollIntoView({block:'start'});"
       "e.preventDefault()})})();")

_SVG_BLIP = "{http://schemas.microsoft.com/office/drawing/2016/SVG/main}svgBlip"
_CORE = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
_DC_TITLE = "{http://purl.org/dc/elements/1.1/}title"


class _Renderer:
    def __init__(self, pkg: Package, path: str) -> None:
        self.pkg = pkg
        self.path = path
        self.deck: Deck = load_deck(pkg)
        self.doc = Doc(pkg, self.deck.slide_cx, self.deck.slide_cy)
        self.doc.slide_ids = {s.part: i + 1 for i, s in enumerate(self.deck.slides)}
        self.slides_done = 0
        pres = pkg.xml(self.deck.presentation_part)
        try:
            self.first_no = int(pres.get("firstSlideNum") or 1)
        except ValueError:
            self.first_no = 1

    # -- документ --

    def _title(self) -> str:
        rel = self.pkg.related_one("/", _CORE)
        if rel is not None and rel.target_part and self.pkg.has_part(rel.target_part):
            try:
                el = self.pkg.xml(rel.target_part).find(_DC_TITLE)
            except ET.ParseError:
                el = None
            if el is not None and (el.text or "").strip():
                return el.text.strip()
        return os.path.splitext(os.path.basename(self.path))[0]

    def render(self) -> str:
        pages = []
        for i, info in enumerate(self.deck.slides):
            root = self.pkg.xml(info.part)
            if root.get("show") in ("0", "false"):
                self.doc.note("hidden slide")
                continue
            self.slides_done += 1
            body = self._slide(info, root, i + 1)
            pages.append(f'<div class="page"><div class="no">{i + 1}</div>{body}</div>')
        css = _CSS + "\n" + "\n".join(self.doc.media.rules)
        title = html.escape(self._title(), quote=False)
        return ("<!doctype html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"utf-8\">\n"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
                f"<title>{title}</title>\n<style>\n{css}\n</style>\n</head>\n<body>\n"
                f"<main class=\"deck\">\n" + "\n".join(pages) + "\n</main>\n"
                f"<script>{_JS}</script>\n</body>\n</html>\n")

    def report(self) -> HtmlReport:
        notes = tuple(f"{k} ×{v}" for k, v in sorted(self.doc.notes.items()))
        return HtmlReport(output=None, slides=self.slides_done, text_chars=self.doc.text_chars,
                          images=self.doc.media.count, unsupported=notes)

    # -- слайд --

    def _slide(self, info: SlideInfo, root: ET.Element, position: int) -> str:
        """Слайд `position` (с единицы, по `p:sldIdLst`; скрытые тоже считаются
        — так номер совпадает с номером в PowerPoint и со ссылками на слайды)."""
        deck = self.deck
        layout = deck.layouts.get(info.layout_part or "")
        master = deck.master_for_slide(info)
        theme = deck.theme_for_slide(info)
        colors = ColorContext(scheme=theme.scheme if theme else {},
                              clr_map=deck.clr_map_for_slide(info))
        ctx = Ctx(doc=self.doc, part=info.part, colors=colors, theme=theme,
                  fmt=load_fmt(self.pkg, master.theme_part if master else None),
                  master=master, layout=layout, default_text=deck.default_text_style,
                  slide_no=position - 1 + self.first_no, count_text=True)
        layout_root = self.pkg.xml(layout.part) if layout else None
        master_root = self.pkg.xml(master.part) if master else None

        section_bg, bg_html = self._background(
            [(root, info.part), (layout_root, layout.part if layout else ""),
             (master_root, master.part if master else "")], ctx)
        out = [bg_html] if bg_html else []
        show_slide = root.get("showMasterSp") not in ("0", "false")
        show_layout = layout_root is None or layout_root.get("showMasterSp") not in ("0", "false")
        if master_root is not None and show_slide and show_layout:
            out += self._tree(_sp_tree(master_root), ctx.with_part(master.part, False), (), "decor")
        if layout_root is not None and show_slide:
            out += self._tree(_sp_tree(layout_root), ctx.with_part(layout.part, False), (), "decor")
        out += self._tree(_sp_tree(root), ctx, (), "slide")
        ratio = f"{self.doc.cx}/{self.doc.cy}"
        return (f'<section class="slide" id="slide-{position}" '
                f'style="aspect-ratio:{ratio};{section_bg}">' + "".join(out) + "</section>")

    def _background(self, chain: list[tuple[ET.Element | None, str]],
                    ctx: Ctx) -> tuple[str, str]:
        for root, part in chain:
            if root is None:
                continue
            bg = root.find(f"{qn('p:cSld')}/{qn('p:bg')}")
            if bg is None:
                continue
            pctx = ctx.with_part(part, False)
            pr = bg.find(qn("p:bgPr"))
            fill: Fill | None = None
            if pr is not None:
                fill = fill_of(find_fill(pr), pctx)
            ref = bg.find(qn("p:bgRef"))
            if ref is not None:
                fill = style_fill(ref, pctx)
            if fill is None:
                return "", ""
            if fill.kind == "blip":
                layer = self._image(fill.blip, fill.part or part, pctx, None, "bg")
                return "", layer
            return css_background(fill), ""
        return "", ""

    # -- дерево фигур --

    def _tree(self, tree: ET.Element | None, ctx: Ctx, groups: tuple[_Group, ...],
              level: str) -> list[str]:
        if tree is None:
            return []
        out: list[str] = []
        for el in tree:
            out += self._safe(el, ctx, groups, level)
        return out

    def _safe(self, el: ET.Element, ctx: Ctx, groups: tuple[_Group, ...],
              level: str) -> list[str]:
        """Одна кривая фигура не должна стоить всей выгрузки: её пропускаем
        и называем в отчёте — молча не теряем (`evidence.unhandled`)."""
        try:
            return self._element(el, ctx, groups, level)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, ZeroDivisionError,
                OverflowError, ET.ParseError) as exc:
            ctx.doc.note(f"render error {local_name(el.tag)}: {type(exc).__name__}")
            return []

    def _element(self, el: ET.Element, ctx: Ctx, groups: tuple[_Group, ...],
                 level: str) -> list[str]:
        name = local_name(el.tag)
        if name == "AlternateContent":
            branch = _alternate(el)
            if not branch:
                ctx.doc.note("mc:AlternateContent without usable branch")
            out: list[str] = []
            for child in branch:
                out += self._element(child, ctx, groups, level)
            return out
        if name not in ("sp", "pic", "grpSp", "cxnSp", "graphicFrame", "contentPart"):
            return []
        nv = next((c for c in el if local_name(c.tag).startswith("nv")), None)
        cnv = nv.find(qn("p:cNvPr")) if nv is not None else None
        if cnv is not None and cnv.get("hidden") in ("1", "true"):
            return []
        if name == "contentPart":
            ctx.doc.note("ink (contentPart)")
            return []
        ph = _ph(el)
        if ph is not None and level == "decor":
            return []  # плейсхолдеры макета и мастера не рисуются
        if name == "grpSp":
            return self._group(el, ctx, groups, level)
        if name == "graphicFrame":
            return [self._frame(el, ctx, groups)]
        return [self._shape(el, name, ph, ctx, groups)]

    def _group(self, el: ET.Element, ctx: Ctx, groups: tuple[_Group, ...],
               level: str) -> list[str]:
        pr = el.find(qn("p:grpSpPr"))
        xfrm = pr.find(qn("a:xfrm")) if pr is not None else None
        box = _xfrm(xfrm)
        ch_off = xfrm.find(qn("a:chOff")) if xfrm is not None else None
        ch_ext = xfrm.find(qn("a:chExt")) if xfrm is not None else None
        fill = find_fill(pr)
        if fill is not None and fill.tag == qn("a:grpFill") and groups:
            fill = groups[-1].fill
        if box is None or ch_off is None or ch_ext is None:
            group = _Group(box=_Box(0, 0, 0, 0), ch=(0, 0, 0, 0), fill=fill)
        else:
            group = _Group(box=box, ch=(float(integer(ch_off.get("x"))), float(integer(ch_off.get("y"))),
                                        float(integer(ch_ext.get("cx"))),
                                        float(integer(ch_ext.get("cy")))),
                           fill=fill)
        out: list[str] = []
        for child in el:
            if local_name(child.tag) in ("nvGrpSpPr", "grpSpPr"):
                continue
            out += self._safe(child, ctx, groups + (group,), level)
        return out

    # -- фигура и картинка --

    def _shape(self, el: ET.Element, name: str, ph: ET.Element | None, ctx: Ctx,
               groups: tuple[_Group, ...]) -> str:
        doc = ctx.doc
        sp_pr = el.find(qn("p:spPr"))
        ph_type: str | None = None
        layout_ph = master_ph = None
        if ph is not None:
            ph_type = ph.get("type") or "body"
            try:
                idx = int(ph.get("idx")) if ph.get("idx") is not None else None
            except ValueError:
                idx = None
            layout_ph = _layout_ph(ctx.layout, ph_type, idx)
            master_ph = _master_ph(ctx.master, layout_ph.ph_type if layout_ph else ph_type)
        inherited = [p.sp for p in (layout_ph, master_ph) if p is not None]
        pr_chain = [x for x in [sp_pr] + [i.find(qn("p:spPr")) for i in inherited] if x is not None]

        box = None
        for pr in pr_chain:
            box = _xfrm(pr.find(qn("a:xfrm")))
            if box is not None:
                break
        if box is None:
            doc.note("shape without position")
            return ""
        placed = _place(box, groups)

        geom_el = None
        for pr in pr_chain:
            geom_el = pr.find(qn("a:prstGeom"))
            if geom_el is None:
                geom_el = pr.find(qn("a:custGeom"))
            if geom_el is not None:
                break
        geom = shape_geometry(geom_el, placed.w, placed.h)
        if geom.approx:
            doc.note(geom.approx)

        styles_el = [s for s in [el.find(qn("p:style"))] + [i.find(qn("p:style")) for i in inherited]
                     if s is not None]
        style = styles_el[0] if styles_el else None

        # Заливка: у картинки — её blipFill, у фигуры — по цепочке spPr, затем стиль.
        fill: Fill | None = None
        if name == "pic":
            blip_fill = el.find(qn("p:blipFill"))
            if blip_fill is not None:
                fill = Fill(kind="blip", blip=blip_fill, part=ctx.part)
        else:
            for pr in pr_chain:
                f_el = find_fill(pr)
                if f_el is None:
                    continue
                if f_el.tag == qn("a:grpFill"):
                    grp = groups[-1].fill if groups else None
                    fill = fill_of(grp, ctx) if grp is not None else None
                else:
                    fill = fill_of(f_el, ctx)
                break
            if fill is None and style is not None:
                fill = style_fill(style.find(qn("a:fillRef")), ctx)

        ln_chain = [x for x in (pr.find(qn("a:ln")) for pr in pr_chain) if x is not None]
        style_ln, style_ctx = style_line(style.find(qn("a:lnRef")) if style is not None else None, ctx)
        line = line_of(ln_chain, style_ln, style_ctx, ctx)

        layers = [self._geometry(geom, fill, line, placed, ctx)]

        body = el.find(qn("p:txBody"))
        if body is not None:
            styles = self._text_styles(body, ph_type, layout_ph, master_ph, style, ctx)
            body_chain = [b for b in [body.find(qn("a:bodyPr"))] +
                          [(i.find(qn("p:txBody")).find(qn("a:bodyPr"))
                            if i.find(qn("p:txBody")) is not None else None) for i in inherited]
                          if b is not None]
            text_rect = geom.text_rect
            tx = _xfrm(el.find(qn("p:txXfrm")))
            if tx is not None and box.w and box.h:
                # SmartArt: у фигуры своя рамка текста (`dsp:txXfrm`).
                kx, ky = placed.w / box.w, placed.h / box.h
                text_rect = ((tx.x - box.x) * kx, (tx.y - box.y) * ky,
                             (tx.x - box.x + tx.w) * kx, (tx.y - box.y + tx.h) * ky)
            if text_rect is not None and (placed.fh or placed.fv):
                l, t, r, b = text_rect
                if placed.fh:
                    l, r = placed.w - r, placed.w - l
                if placed.fv:
                    t, b = placed.h - b, placed.h - t
                text_rect = (l, t, r, b)
            layers.append(body_layer(body, ctx, styles, body_chain, placed.w, placed.h,
                                     text_rect, upside_down=placed.fv))
        inner = "".join(x for x in layers if x)
        if not inner:
            return ""
        return self._wrap(placed, inner)

    def _wrap(self, b: _Box, inner: str) -> str:
        doc = self.doc
        style = (f"left:{doc.px(b.x)};top:{doc.py(b.y)};width:{doc.px(b.w)};"
                 f"height:{doc.py(b.h)}")
        if b.rot % 360:
            style += f";transform:rotate({num(b.rot % 360, 2)}deg)"
        return f'<div class="s" style="{style}">{inner}</div>'

    def _text_styles(self, body: ET.Element, ph_type: str | None,
                     layout_ph: Placeholder | None, master_ph: Placeholder | None,
                     style: ET.Element | None, ctx: Ctx) -> TextStyles:
        """Цепочка стилей текста фигуры (`DOM-TEXT §2`)."""
        containers: list[tuple[ET.Element, bool]] = []
        own = body.find(qn("a:lstStyle"))
        if own is not None:
            containers.append((own, False))
        if style is not None:
            font_ref = style.find(qn("a:fontRef"))
            if font_ref is not None:
                clr = next(iter(font_ref), None)
                latin = {"major": "+mj-lt", "minor": "+mn-lt"}.get(font_ref.get("idx") or "")
                containers.append((pseudo_level(color_el=clr, latin=latin), True))
        for p in (layout_ph, master_ph):
            if p is None:
                continue
            tb = p.sp.find(qn("p:txBody"))
            lst = tb.find(qn("a:lstStyle")) if tb is not None else None
            if lst is not None:
                containers.append((lst, False))
        master_style = _txstyle(ctx.master, ph_type)
        if master_style is not None:
            containers.append((master_style, False))
        if ctx.default_text is not None:
            containers.append((ctx.default_text, False))
        return TextStyles(tuple(containers))

    def _geometry(self, geom: Geometry, fill: Fill | None, line: Line | None, b: _Box,
                  ctx: Ctx) -> str:
        """Слой формы: блок CSS для прямоугольника, скругления и эллипса без
        обводки, SVG — для всего остального. Картинка-заливка — отдельным слоем.

        Обводку рисует только SVG: Chrome округляет толщину рамки CSS вниз до
        целого пикселя, и линия в 1.5 пт на экране шириной 1280 выходила
        однопиксельной вместо двух."""
        doc = ctx.doc
        flip = ""
        if b.fh or b.fv:
            flip = f"transform:scale({-1 if b.fh else 1},{-1 if b.fv else 1});"
        if geom.line_like:
            fill = None
        out = []
        blip = fill is not None and fill.kind == "blip"
        if blip:
            out.append(self._image(fill.blip, fill.part or ctx.part, ctx, geom, "f", b, flip))
        if geom.kind in ("rect", "roundRect", "ellipse") and line is None:
            bg = "" if blip else css_background(fill)
            if not bg:
                return "".join(out)
            radius = ""
            if geom.kind == "ellipse":
                radius = "border-radius:50%;"
            elif geom.kind == "roundRect" and geom.radius > 0:
                radius = f"border-radius:{doc.cq(geom.radius)};"
            out.append(f'<div class="f" style="{bg}{radius}{flip}"></div>')
            return "".join(out)
        if not geom.paths:
            return "".join(out)
        pad = (max(line.width, 3175) * (4 if (line.head or line.tail) else 1)) if line else 0.0
        pad += EMU_PER_PT
        k = 1 / EMU_PER_PT
        vw, vh = (b.w + 2 * pad) * k, (b.h + 2 * pad) * k
        defs = []
        items = []
        mstart = mend = ""
        if line and (line.head or line.tail) and geom.line_like:
            mdefs, mstart, mend = marker_defs(line, ctx)
            defs.append(mdefs)
        for p in geom.paths:
            if blip:
                fill_attr, fdefs = ' fill="none"', ""
            else:
                fill_attr, fdefs = svg_fill(fill, ctx, b.w * k, b.h * k, p.fill)
            if fdefs:
                defs.append(fdefs)
            stroke = svg_stroke(line, k) if p.stroke else ' stroke="none"'
            if fill_attr == ' fill="none"' and stroke == ' stroke="none"':
                continue
            markers = (mstart + mend) if p.stroke and geom.line_like else ""
            items.append(f'<path d="{p.d(k, k)}"{fill_attr}{stroke}{markers} '
                         f'fill-rule="evenodd"/>')
        if not items:
            return "".join(out)
        style = (f"left:-{doc.cq(pad)};top:-{doc.cq(pad)};width:{doc.cq(b.w + 2 * pad)};"
                 f"height:{doc.cq(b.h + 2 * pad)};{flip}")
        d = f"<defs>{''.join(defs)}</defs>" if defs else ""
        out.append(f'<svg viewBox="{num(-pad * k)} {num(-pad * k)} {num(vw)} {num(vh)}" '
                   f'preserveAspectRatio="none" style="{style}">{d}{"".join(items)}</svg>')
        return "".join(out)

    def _image(self, blip_fill: ET.Element | None, part: str, ctx: Ctx, geom: Geometry | None,
               cls: str, b: _Box | None = None, flip: str = "") -> str:
        """Картинка: слой с обрезкой (`a:srcRect`), растяжением (`a:fillRect`),
        прозрачностью (`a:alphaModFix`) и формой рамки."""
        doc = ctx.doc
        if blip_fill is None:
            return ""
        blip = blip_fill.find(qn("a:blip"))
        if blip is None:
            return ""
        if blip.get(qn("r:link")) and not blip.get(qn("r:embed")):
            doc.note("linked picture")
            return ""
        rid = blip.get(qn("r:embed"))
        svg = blip.find(f".//{_SVG_BLIP}")
        target = None
        if svg is not None and svg.get(qn("r:embed")):
            target = doc.pkg.part_for_rid(part, svg.get(qn("r:embed")))
        if target is None and rid:
            target = doc.pkg.part_for_rid(part, rid)
        if target is None:
            return ""
        media = doc.media.use(target, doc)
        if media is None:
            return ""
        style = []
        filters = []
        pre = []        # определения SVG (фильтр, контур), на которые ссылается слой
        for eff in blip:
            en = local_name(eff.tag)
            if en == "alphaModFix":
                amt = pct(eff.get("amt"), 1.0)
                if amt < 0.999:
                    style.append(f"opacity:{num(max(amt, 0.0))}")
            elif en == "grayscl":
                filters.append("grayscale(1)")
            elif en == "lum":
                bright = pct(eff.get("bright"), 0.0)
                contrast = pct(eff.get("contrast"), 0.0)
                if bright:
                    filters.append(f"brightness({num(1 + bright)})")
                if contrast:
                    filters.append(f"contrast({num(1 + contrast)})")
            elif en == "duotone":
                duo = _duotone(eff, ctx)
                if duo:
                    pre.append(duo[0])
                    filters.append(f"url(#{duo[1]})")
                else:
                    doc.note("picture effect duotone")
            elif en in ("biLevel", "clrChange", "clrRepl", "hsl", "tint"):
                doc.note(f"picture effect {en}")
        if filters:
            style.append("filter:" + " ".join(filters))
        if blip_fill.find(qn("a:tile")) is not None:
            doc.note("tiled picture fill")

        def rect(el: ET.Element | None) -> tuple[float, float, float, float]:
            if el is None:
                return 0.0, 0.0, 0.0, 0.0
            return (pct(el.get("l"), 0.0), pct(el.get("t"), 0.0),
                    pct(el.get("r"), 0.0), pct(el.get("b"), 0.0))

        sl, st, sr, sb = rect(blip_fill.find(qn("a:srcRect")))
        stretch = blip_fill.find(qn("a:stretch"))
        fl, ft, fr, fb = rect(stretch.find(qn("a:fillRect")) if stretch is not None else None)
        fw, fh_ = 1 - fl - fr, 1 - ft - fb
        sw, sh = 1 - sl - sr, 1 - st - sb
        outer = []
        if geom is not None and geom.kind == "ellipse":
            outer.append("border-radius:50%")
        elif geom is not None and geom.kind == "roundRect" and geom.radius > 0:
            outer.append(f"border-radius:{doc.cq(geom.radius)}")
        if geom is not None and geom.kind == "path" and geom.paths and b is not None and b.w and b.h:
            cid = doc.uid("c")
            d = "".join(p.d(1 / b.w, 1 / b.h, 5) for p in geom.paths if p.fill != "none")
            pre.append(f'<svg width="0" height="0" aria-hidden="true"><clipPath id="{cid}" '
                       f'clipPathUnits="objectBoundingBox"><path d="{d}"/></clipPath></svg>')
            outer.append(f"clip-path:url(#{cid})")
        if flip:
            outer.append(flip.rstrip(";"))
        if abs(sw) < 1e-9 or abs(sh) < 1e-9:
            return ""
        clip = "".join(pre)
        plain = (sl, st, sr, sb, fl, ft, fr, fb) == (0,) * 8
        if plain and not outer:
            return f'{clip}<div class="{cls} i {media}" style="{";".join(style)}"></div>'
        if plain:
            inner = f'<div class="i {media}" style="inset:0;{";".join(style)}"></div>'
        else:
            left = fl - sl * fw / sw
            top = ft - st * fh_ / sh
            inner = (f'<div class="i {media}" style="left:{num(left * 100)}%;top:{num(top * 100)}%;'
                     f'width:{num(fw / sw * 100)}%;height:{num(fh_ / sh * 100)}%;'
                     f'{";".join(style)}"></div>')
        return f'{clip}<div class="{cls}" style="{";".join(outer)}">{inner}</div>'

    # -- рамки: таблица, диаграмма, OLE, SmartArt --

    def _frame(self, el: ET.Element, ctx: Ctx, groups: tuple[_Group, ...]) -> str:
        doc = ctx.doc
        box = _xfrm(el.find(qn("p:xfrm")))
        if box is None:
            doc.note("graphicFrame without position")
            return ""
        placed = _place(box, groups)
        data = el.find(f"{qn('a:graphic')}/{qn('a:graphicData')}")
        uri = (data.get("uri") or "") if data is not None else ""
        kind = uri.rsplit("/", 1)[-1]
        if data is None:
            return ""
        if kind == "table":
            tbl = data.find(qn("a:tbl"))
            if tbl is None:
                return ""
            base = [(s, False) for s in (_txstyle(ctx.master, None), ctx.default_text) if s is not None]
            return _table.render(tbl, ctx, placed.x, placed.y, placed.w, TextStyles(tuple(base)))
        if kind == "chart":
            ref = data.find(qn("c:chart"))
            rid = ref.get(qn("r:id")) if ref is not None else None
            part = doc.pkg.part_for_rid(ctx.part, rid) if rid else None
            if part is None or not doc.pkg.has_part(part):
                doc.note("chart part missing")
                return ""
            svg = _chart.render(part, ctx, placed.w, placed.h)
            return self._wrap(placed, svg) if svg else ""
        if kind == "diagram":
            drawn = self._smartart(data, box, ctx, groups)
            if drawn is not None:
                return drawn
        # OLE и прочее: рисуем запасную картинку, если она есть.
        pics = []
        for node in data.iter():
            if local_name(node.tag) == "AlternateContent":
                for child in _alternate(node):
                    pics += [p for p in child.iter(qn("p:pic"))]
                break
        if not pics:
            pics = list(data.iter(qn("p:pic")))
        label = {"ole": "OLE object", "diagram": "SmartArt"}.get(kind, f"graphicFrame {kind}")
        if pics:
            pic = pics[0]
            blip_fill = pic.find(qn("p:blipFill"))
            layer = self._image(blip_fill, ctx.part, ctx, None, "f")
            if layer:
                doc.note(f"{label} (picture fallback)")
                return self._wrap(placed, layer)
        doc.note(label)
        return ""

    def _smartart(self, data: ET.Element, box: _Box, ctx: Ctx,
                  groups: tuple[_Group, ...]) -> str | None:
        """SmartArt по готовому рисунку. PowerPoint кладёт рядом с данными
        диаграммы уже разложенные фигуры (`ppt/diagrams/drawingN.xml`,
        пространство имён `dsp`) — это те же `sp` с `spPr` и `txBody`. Их
        рисуем группой в рамке кадра; раскладку SmartArt не пересчитываем.

        Текст SmartArt лежит не в части слайда, поэтому в `text_chars` не
        идёт: счётчик сверяется с текстом слайдов. Нет рисунка — None."""
        doc = ctx.doc
        ids = data.find(qn("dgm:relIds"))
        dm = doc.pkg.part_for_rid(ctx.part, ids.get(qn("r:dm")) or "") if ids is not None else None
        if not dm or not doc.pkg.has_part(dm):
            return None
        ext = doc.pkg.xml(dm).find(f".//{_DSP}dataModelExt")
        drawing = doc.pkg.part_for_rid(ctx.part, ext.get("relId") or "") if ext is not None else None
        if not drawing or not doc.pkg.has_part(drawing):
            return None
        tree = doc.pkg.xml(drawing).find(f"{_DSP}spTree")
        if tree is None:
            return None
        tree = copy.deepcopy(tree)
        for node in tree.iter():
            if node.tag.startswith(_DSP):
                node.tag = _P_NS + node.tag[len(_DSP):]
        frame = _Group(box=replace(box, rot=0.0, fh=False, fv=False), ch=(0.0, 0.0, box.w, box.h),
                       fill=None)
        return "".join(self._tree(tree, ctx.with_part(drawing, False), groups + (frame,), "decor"))


#: Готовый рисунок SmartArt (`ppt/diagrams/drawingN.xml`) и PresentationML.
_DSP = "{http://schemas.microsoft.com/office/drawing/2008/diagram}"
_P_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"


def _duotone(eff: ET.Element, ctx: Ctx) -> tuple[str, str] | None:
    """`a:duotone` — перекраска картинки: яркость переводится в градиент от
    первого цвета (тени) ко второму (света). В CSS такого нет, поэтому —
    фильтр SVG: сначала яркость, затем таблица по каждому каналу."""
    cols = [color(c, ctx) for c in eff if local_name(c.tag).endswith("Clr")]
    if len(cols) != 2 or cols[0] is None or cols[1] is None:
        return None
    fid = ctx.doc.uid("d")
    chan = []
    for i, name in enumerate("RGB"):
        lo = int(cols[0][0][1 + 2 * i:3 + 2 * i], 16) / 255
        hi = int(cols[1][0][1 + 2 * i:3 + 2 * i], 16) / 255
        chan.append(f'<feFunc{name} type="table" tableValues="{num(lo, 4)} {num(hi, 4)}"/>')
    lum = "0.3 0.59 0.11 0 0 " * 3 + "0 0 0 1 0"
    svg = (f'<svg width="0" height="0" aria-hidden="true"><filter id="{fid}" '
           f'color-interpolation-filters="sRGB"><feColorMatrix type="matrix" values="{lum}"/>'
           f'<feComponentTransfer>{"".join(chan)}</feComponentTransfer></filter></svg>')
    return svg, fid


def _sp_tree(root: ET.Element | None) -> ET.Element | None:
    if root is None:
        return None
    return root.find(f"{qn('p:cSld')}/{qn('p:spTree')}")
