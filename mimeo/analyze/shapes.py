"""Сбор наблюдений по фигурам слайда с полным разрешением свойств.

Геометрия — DOM-GEOM §2, §4, §5. Текст — DOM-TEXT §2, §5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from ..oxml.ns import local_name, qn
from ..oxml.units import pct_to_ratio, spc_to_pt, sz_to_pt
from .color import ColorContext, ResolvedColor, resolve_color, resolve_fill_color
from .deck import Deck, Placeholder, Rect, SlideInfo
from .theme import Theme

_TITLE_PH = {"title", "ctrTitle"}
_BODY_PH = {"body", "subTitle", "obj"}
#: Колонтитульные плейсхолдеры портят статистику полей. DOM-GEOM §7.
CHROME_PH = {"sldNum", "ftr", "dt", "hdr"}


# --- преобразование координат ------------------------------------------


@dataclass(frozen=True)
class Affine:
    """x_слайд = ax·x + bx. Для групп. DOM-GEOM §4."""

    ax: float = 1.0
    bx: float = 0.0
    ay: float = 1.0
    by: float = 0.0

    def rect(self, r: Rect) -> Rect:
        return Rect(
            x=round(self.ax * r.x + self.bx),
            y=round(self.ay * r.y + self.by),
            cx=max(0, round(self.ax * r.cx)),
            cy=max(0, round(self.ay * r.cy)),
        )

    def then(self, inner: "Affine") -> "Affine":
        """Применить inner, затем self."""
        return Affine(
            ax=self.ax * inner.ax,
            bx=self.ax * inner.bx + self.bx,
            ay=self.ay * inner.ay,
            by=self.ay * inner.by + self.by,
        )


def _group_affine(grp: ET.Element) -> Affine:
    xfrm = grp.find(f"{qn('p:grpSpPr')}/{qn('a:xfrm')}")
    if xfrm is None:
        return Affine()
    off, ext = xfrm.find(qn("a:off")), xfrm.find(qn("a:ext"))
    ch_off, ch_ext = xfrm.find(qn("a:chOff")), xfrm.find(qn("a:chExt"))
    if off is None or ext is None or ch_off is None or ch_ext is None:
        return Affine()
    try:
        ch_cx, ch_cy = int(ch_ext.get("cx", 0)), int(ch_ext.get("cy", 0))
        if ch_cx == 0 or ch_cy == 0:
            return Affine()
        ax = int(ext.get("cx", 0)) / ch_cx
        ay = int(ext.get("cy", 0)) / ch_cy
        return Affine(
            ax=ax,
            bx=int(off.get("x", 0)) - int(ch_off.get("x", 0)) * ax,
            ay=ay,
            by=int(off.get("y", 0)) - int(ch_off.get("y", 0)) * ay,
        )
    except (TypeError, ValueError):
        return Affine()


# --- наблюдения --------------------------------------------------------


@dataclass(frozen=True)
class RunObs:
    slide_index: int
    shape_id: str
    text: str
    para_index: int
    level: int
    size_pt: float | None
    bold: bool
    italic: bool
    caps: str | None
    spacing_pt: float
    latin: str | None
    cyrl: str | None
    color_hex: str | None
    align: str | None
    line_spacing_pct: float | None
    in_placeholder: str | None


@dataclass(frozen=True)
class ShapeObs:
    slide_index: int
    shape_id: str
    name: str
    kind: str
    ph_type: str | None
    ph_idx: int | None
    rect: Rect | None
    rotated: bool
    hidden: bool
    geom: str | None
    corner_radius_pct: float | None
    fill_kind: str | None
    fill: ResolvedColor | None
    line: ResolvedColor | None
    line_w_emu: int | None
    has_shadow: bool
    runs: tuple[RunObs, ...]
    insets: tuple[int, int, int, int] = (91440, 45720, 91440, 45720)
    anchor: str | None = None
    wrap: str | None = None
    autofit: str | None = None
    para_count: int = 0
    has_chart: bool = False
    has_table: bool = False

    @property
    def has_text(self) -> bool:
        return any(r.text.strip() for r in self.runs)

    @property
    def text(self) -> str:
        """Весь текст фигуры одной строкой. DOM-TEXT §8: признаки вроде
        «это число» считаются на этом уровне, а не на уровне прогона."""
        return "".join(r.text for r in self.runs).strip()


@dataclass
class SlideObs:
    index: int
    part: str
    background: ResolvedColor | None
    shapes: list[ShapeObs] = field(default_factory=list)


# --- цепочки наследования текста ---------------------------------------


def _lvl_ppr(container: ET.Element | None, level: int) -> ET.Element | None:
    if container is None:
        return None
    return container.find(qn(f"a:lvl{level + 1}pPr"))


def _lst_style_lvl(txbody: ET.Element | None, level: int) -> ET.Element | None:
    if txbody is None:
        return None
    return _lvl_ppr(txbody.find(qn("a:lstStyle")), level)


def _txstyle_lvl(
    txstyles: ET.Element | None, ph_type: str | None, level: int
) -> ET.Element | None:
    """Мастер-стили выбираются по типу плейсхолдера. DOM-TEXT §2, уровень 6."""
    if txstyles is None:
        return None
    if ph_type in _TITLE_PH:
        name = "p:titleStyle"
    elif ph_type in _BODY_PH:
        name = "p:bodyStyle"
    else:
        name = "p:otherStyle"
    return _lvl_ppr(txstyles.find(qn(name)), level)


def _txbody(sp: ET.Element | None) -> ET.Element | None:
    return sp.find(qn("p:txBody")) if sp is not None else None


def _attr(chain: list[ET.Element], name: str) -> str | None:
    """Свойства разрешаются по одному, а не целым блоком. DOM-TEXT §2."""
    for el in chain:
        v = el.get(name)
        if v is not None:
            return v
    return None


def _child(chain: list[ET.Element], tag: str) -> ET.Element | None:
    q = qn(tag)
    for el in chain:
        found = el.find(q)
        if found is not None:
            return found
    return None


def _flag(value: str | None) -> bool:
    return value in ("1", "true", "on")


# --- разбор заливок ----------------------------------------------------


def _parse_fill(
    holder: ET.Element | None, ctx: ColorContext, unhandled: list[tuple[str, str]]
) -> tuple[str | None, ResolvedColor | None]:
    if holder is None:
        return None, None
    if holder.find(qn("a:noFill")) is not None:
        return "none", None
    solid = holder.find(qn("a:solidFill"))
    if solid is not None:
        return "solid", resolve_fill_color(solid, ctx, unhandled)
    grad = holder.find(qn("a:gradFill"))
    if grad is not None:
        # Градиент сводим к первому стопу: для подсчёта палитры достаточно.
        gs = grad.find(f"{qn('a:gsLst')}/{qn('a:gs')}")
        return "gradient", resolve_fill_color(gs, ctx, unhandled)
    if holder.find(qn("a:blipFill")) is not None:
        return "picture", None
    if holder.find(qn("a:pattFill")) is not None:
        return "pattern", None
    return None, None


def _parse_line(
    sp_pr: ET.Element | None, ctx: ColorContext, unhandled: list[tuple[str, str]]
) -> tuple[ResolvedColor | None, int | None]:
    if sp_pr is None:
        return None, None
    ln = sp_pr.find(qn("a:ln"))
    if ln is None:
        return None, None
    width = ln.get("w")
    _, color = _parse_fill(ln, ctx, unhandled)
    try:
        return color, int(width) if width is not None else None
    except (TypeError, ValueError):
        return color, None


def _has_shadow(sp_pr: ET.Element | None) -> bool:
    if sp_pr is None:
        return False
    effects = sp_pr.find(qn("a:effectLst"))
    if effects is None:
        return False
    return any(local_name(e.tag).endswith("Shdw") for e in effects)


def _geom(sp_pr: ET.Element | None) -> tuple[str | None, float | None]:
    """DOM-GEOM §3."""
    if sp_pr is None:
        return None, None
    prst = sp_pr.find(qn("a:prstGeom"))
    if prst is None:
        return ("custom", None) if sp_pr.find(qn("a:custGeom")) is not None else (None, None)
    name = prst.get("prst")
    radius = None
    if name == "roundRect":
        adj = prst.find(f"{qn('a:avLst')}/{qn('a:gd')}")
        raw = adj.get("fmla", "") if adj is not None else ""
        value = raw.split()[-1] if raw.startswith("val") else None
        try:
            radius = (int(value) if value is not None else 16667) / 100000.0
        except ValueError:
            radius = 0.16667
    return name, radius


# --- основной обход ----------------------------------------------------


class SlideAnalyzer:
    def __init__(self, deck: Deck, slide: SlideInfo) -> None:
        self.deck = deck
        self.slide = slide
        self.theme: Theme | None = deck.theme_for_slide(slide)
        self.master = deck.master_for_slide(slide)
        self.layout = deck.layouts.get(slide.layout_part or "")
        self.ctx = ColorContext(
            scheme=self.theme.scheme if self.theme else {},
            clr_map=deck.clr_map_for_slide(slide),
        )
        self.unhandled: list[tuple[str, str]] = []

    # -- плейсхолдеры --

    def _inherited_ph(self, idx: int | None, ph_type: str | None) -> tuple[Placeholder | None, Placeholder | None]:
        layout_ph = self.layout.lookup(idx, ph_type) if self.layout else None
        master_ph = self.master.lookup(idx, ph_type) if self.master else None
        return layout_ph, master_ph

    # -- текст --

    @staticmethod
    def _body_metrics(sp: ET.Element | None) -> tuple[tuple[int, int, int, int], str | None, str | None, str | None]:
        """Врезки, привязка, перенос и режим автоподбора. DOM-TEXT §5, §6."""
        defaults = (91440, 45720, 91440, 45720)
        body = _txbody(sp)
        body_pr = body.find(qn("a:bodyPr")) if body is not None else None
        if body_pr is None:
            return defaults, None, None, None
        def _ins(attr: str, fallback: int) -> int:
            raw = body_pr.get(attr)
            try:
                return int(raw) if raw is not None else fallback
            except ValueError:
                return fallback
        autofit = None
        for tag in ("a:normAutofit", "a:spAutoFit", "a:noAutofit"):
            if body_pr.find(qn(tag)) is not None:
                autofit = tag.split(":")[1]
                break
        return (
            (_ins("lIns", defaults[0]), _ins("tIns", defaults[1]),
             _ins("rIns", defaults[2]), _ins("bIns", defaults[3])),
            body_pr.get("anchor"),
            body_pr.get("wrap"),
            autofit,
        )

    def _runs(
        self, sp: ET.Element, shape_id: str, ph_type: str | None, ph_idx: int | None
    ) -> tuple[RunObs, ...]:
        body = _txbody(sp)
        if body is None:
            return ()

        layout_ph, master_ph = self._inherited_ph(ph_idx, ph_type)
        layout_body = _txbody(layout_ph.sp) if layout_ph else None
        master_body = _txbody(master_ph.sp) if master_ph else None

        # normAutofit/@fontScale уже посчитан PowerPoint. DOM-TEXT §5.
        font_scale = 1.0
        body_pr = body.find(qn("a:bodyPr"))
        if body_pr is not None:
            auto = body_pr.find(qn("a:normAutofit"))
            if auto is not None:
                font_scale = pct_to_ratio(auto.get("fontScale")) or 1.0

        out: list[RunObs] = []
        for para_index, para in enumerate(body.findall(qn("a:p"))):
            p_pr = para.find(qn("a:pPr"))
            level = 0
            if p_pr is not None and p_pr.get("lvl"):
                try:
                    level = int(p_pr.get("lvl") or 0)
                except ValueError:
                    level = 0

            ppr_chain = [
                el
                for el in (
                    p_pr,
                    _lst_style_lvl(body, level),
                    _lst_style_lvl(layout_body, level),
                    _lst_style_lvl(master_body, level),
                    _txstyle_lvl(self.master.txstyles if self.master else None, ph_type, level),
                    _lvl_ppr(self.deck.default_text_style, level),
                )
                if el is not None
            ]

            align = _attr(ppr_chain, "algn")
            ln_spc = _child(ppr_chain, "a:lnSpc")
            line_pct = None
            if ln_spc is not None:
                pct = ln_spc.find(qn("a:spcPct"))
                if pct is not None:
                    ratio = pct_to_ratio(pct.get("val"))
                    line_pct = None if ratio is None else ratio * 100.0

            defaults = [el for el in (p.find(qn("a:defRPr")) for p in ppr_chain) if el is not None]

            for run in para.findall(qn("a:r")):
                text_el = run.find(qn("a:t"))
                text = text_el.text or "" if text_el is not None else ""
                if not text:
                    continue

                r_pr = run.find(qn("a:rPr"))
                rpr_chain = ([r_pr] if r_pr is not None else []) + defaults

                size = sz_to_pt(_attr(rpr_chain, "sz"))
                latin_el = _child(rpr_chain, "a:latin")
                token = latin_el.get("typeface") if latin_el is not None else None

                fill = _child(rpr_chain, "a:solidFill")
                color = resolve_fill_color(fill, self.ctx, self.unhandled)

                out.append(
                    RunObs(
                        slide_index=self.slide.index,
                        shape_id=shape_id,
                        text=text,
                        para_index=para_index,
                        level=level,
                        size_pt=None if size is None else round(size * font_scale, 2),
                        bold=_flag(_attr(rpr_chain, "b")),
                        italic=_flag(_attr(rpr_chain, "i")),
                        caps=_attr(rpr_chain, "cap"),
                        spacing_pt=spc_to_pt(_attr(rpr_chain, "spc")),
                        latin=self.theme.typeface(token) if self.theme else token,
                        cyrl=self.theme.script_face(token) if self.theme else None,
                        color_hex=color.hex if color else None,
                        align=align,
                        line_spacing_pct=line_pct,
                        in_placeholder=ph_type,
                    )
                )
        return tuple(out)

    # -- фигуры --

    def _shape(self, el: ET.Element, xf: Affine) -> ShapeObs | None:
        tag = local_name(el.tag)
        kind = {"sp": "sp", "pic": "pic", "graphicFrame": "graphicFrame", "cxnSp": "cxnSp"}.get(tag)
        if kind is None:
            return None

        nv_pr = el.find(f"{qn('p:nvSpPr')}/{qn('p:cNvPr')}")
        if nv_pr is None:
            for holder in ("p:nvPicPr", "p:nvGraphicFramePr", "p:nvCxnSpPr"):
                nv_pr = el.find(f"{qn(holder)}/{qn('p:cNvPr')}")
                if nv_pr is not None:
                    break
        shape_id = (nv_pr.get("id") if nv_pr is not None else None) or "?"
        name = (nv_pr.get("name") if nv_pr is not None else None) or ""
        hidden = _flag(nv_pr.get("hidden")) if nv_pr is not None else False

        ph = el.find(f"{qn('p:nvSpPr')}/{qn('p:nvPr')}/{qn('p:ph')}")
        ph_type = ph_idx = None
        if ph is not None:
            ph_type = ph.get("type") or "body"
            raw = ph.get("idx")
            ph_idx = int(raw) if raw is not None and raw.isdigit() else None

        sp_pr = el.find(qn("p:spPr"))
        # У graphicFrame трансформация лежит в p:xfrm, а не в a:xfrm.
        xfrm = el.find(qn("p:xfrm")) if kind == "graphicFrame" else (
            sp_pr.find(qn("a:xfrm")) if sp_pr is not None else None
        )

        rect: Rect | None = None
        rotated = False
        if xfrm is not None:
            off, ext = xfrm.find(qn("a:off")), xfrm.find(qn("a:ext"))
            if off is not None and ext is not None:
                try:
                    rect = Rect(int(off.get("x", 0)), int(off.get("y", 0)),
                                int(ext.get("cx", 0)), int(ext.get("cy", 0)))
                except (TypeError, ValueError):
                    rect = None
            rotated = bool(xfrm.get("rot")) and xfrm.get("rot") not in ("0", None)

        if rect is None and ph is not None:
            # Геометрия наследуется от макета, затем от мастера. DOM-GEOM §5.
            layout_ph, master_ph = self._inherited_ph(ph_idx, ph_type)
            if layout_ph is not None and layout_ph.rect is not None:
                rect = layout_ph.rect
            elif master_ph is not None and master_ph.rect is not None:
                rect = master_ph.rect

        if rect is not None:
            rect = xf.rect(rect)

        fill_kind, fill = _parse_fill(sp_pr, self.ctx, self.unhandled)
        line, line_w = _parse_line(sp_pr, self.ctx, self.unhandled)
        geom, radius = _geom(sp_pr)

        runs = self._runs(el, shape_id, ph_type, ph_idx) if kind == "sp" else ()
        insets, anchor, wrap, autofit = self._body_metrics(el if kind == "sp" else None)
        body_el = _txbody(el) if kind == "sp" else None
        para_count = len(body_el.findall(qn("a:p"))) if body_el is not None else 0
        has_table = kind == "graphicFrame" and el.find(f".//{qn('a:tbl')}") is not None
        has_chart = kind == "graphicFrame" and not has_table and el.find(
            f".//{qn('a:graphicData')}") is not None
        if has_table:
            self.unhandled.append(("table_text", "текст таблиц не разбирается"))

        return ShapeObs(
            slide_index=self.slide.index,
            shape_id=shape_id,
            name=name,
            kind=kind,
            ph_type=ph_type,
            ph_idx=ph_idx,
            rect=rect,
            rotated=rotated,
            hidden=hidden,
            geom=geom,
            corner_radius_pct=radius,
            fill_kind=fill_kind,
            fill=fill,
            line=line,
            line_w_emu=line_w,
            has_shadow=_has_shadow(sp_pr),
            runs=runs,
            insets=insets,
            anchor=anchor,
            wrap=wrap,
            autofit=autofit,
            para_count=para_count,
            has_chart=has_chart,
            has_table=has_table,
        )

    def _walk(self, container: ET.Element, xf: Affine, out: list[ShapeObs], depth: int = 0) -> None:
        for el in container:
            tag = local_name(el.tag)
            if tag == "grpSp":
                if depth >= 8:
                    self.unhandled.append(("deep_group", "вложенность групп больше 8"))
                    continue
                self._walk(el, xf.then(_group_affine(el)), out, depth + 1)
            else:
                obs = self._shape(el, xf)
                if obs is not None:
                    out.append(obs)

    def run(self) -> SlideObs:
        root = self.deck.pkg.xml(self.slide.part)
        tree = root.find(f"{qn('p:cSld')}/{qn('p:spTree')}")

        background = None
        bg = root.find(f"{qn('p:cSld')}/{qn('p:bg')}")
        if bg is not None:
            bg_pr = bg.find(qn("p:bgPr"))
            if bg_pr is not None:
                _, background = _parse_fill(bg_pr, self.ctx, self.unhandled)
            elif bg.find(qn("p:bgRef")) is not None:
                # Фон по ссылке в bgFillStyleLst темы, idx от 1001. DOM-COLOR §5.
                ref = bg.find(qn("p:bgRef"))
                background = resolve_color(next(iter(ref), None), self.ctx, self.unhandled)

        shapes: list[ShapeObs] = []
        if tree is not None:
            self._walk(tree, Affine(), shapes)

        return SlideObs(
            index=self.slide.index, part=self.slide.part, background=background, shapes=shapes
        )


def analyze_slides(deck: Deck) -> tuple[list[SlideObs], list[tuple[str, str]]]:
    observations: list[SlideObs] = []
    unhandled: list[tuple[str, str]] = []
    for slide in deck.slides:
        analyzer = SlideAnalyzer(deck, slide)
        observations.append(analyzer.run())
        unhandled.extend(analyzer.unhandled)
    return observations, unhandled
