"""Граф документа: presentation -> master -> layout -> slide. DOM-PKG §4."""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from ..opc.package import Package
from ..oxml.ns import RT, qn
from .color import MAP_ROLES
from .theme import Theme, load_theme


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    cx: int
    cy: int

    @property
    def right(self) -> int:
        return self.x + self.cx

    @property
    def bottom(self) -> int:
        return self.y + self.cy

    @property
    def area(self) -> int:
        return self.cx * self.cy


@dataclass(frozen=True)
class Placeholder:
    ph_type: str
    idx: int | None
    sp: ET.Element
    rect: Rect | None


@dataclass
class PartInfo:
    """Общая часть мастера и макета: плейсхолдеры и карта цветов."""

    part: str
    clr_map: dict[str, str] = field(default_factory=dict)
    by_idx: dict[int, Placeholder] = field(default_factory=dict)
    by_type: dict[str, Placeholder] = field(default_factory=dict)

    def lookup(self, idx: int | None, ph_type: str | None) -> Placeholder | None:
        """Сопоставление по idx приоритетнее, чем по type. DOM-GEOM §5."""
        if idx is not None and idx in self.by_idx:
            return self.by_idx[idx]
        if ph_type and ph_type in self.by_type:
            return self.by_type[ph_type]
        return None


@dataclass
class MasterInfo(PartInfo):
    theme_part: str | None = None
    txstyles: ET.Element | None = None
    layouts: tuple[str, ...] = ()


@dataclass
class LayoutInfo(PartInfo):
    master_part: str | None = None
    layout_type: str | None = None


@dataclass(frozen=True)
class SlideInfo:
    part: str
    index: int
    layout_part: str | None
    clr_map_ovr: dict[str, str] | None


@dataclass
class Deck:
    pkg: Package
    presentation_part: str
    slide_cx: int
    slide_cy: int
    slide_size_type: str | None
    default_text_style: ET.Element | None
    masters: dict[str, MasterInfo]
    layouts: dict[str, LayoutInfo]
    themes: dict[str, Theme]
    slides: tuple[SlideInfo, ...]

    def master_for_slide(self, slide: SlideInfo) -> MasterInfo | None:
        layout = self.layouts.get(slide.layout_part or "")
        if layout is None or layout.master_part is None:
            return None
        return self.masters.get(layout.master_part)

    def theme_for_slide(self, slide: SlideInfo) -> Theme | None:
        master = self.master_for_slide(slide)
        if master is None or master.theme_part is None:
            return None
        return self.themes.get(master.theme_part)

    def clr_map_for_slide(self, slide: SlideInfo) -> dict[str, str]:
        """Слайд > макет > мастер. DOM-COLOR §2."""
        if slide.clr_map_ovr:
            return slide.clr_map_ovr
        layout = self.layouts.get(slide.layout_part or "")
        if layout is not None and layout.clr_map:
            return layout.clr_map
        master = self.master_for_slide(slide)
        return dict(master.clr_map) if master else {}


# --- разбор ------------------------------------------------------------


def _rect_of(sp: ET.Element) -> Rect | None:
    xfrm = sp.find(f"{qn('p:spPr')}/{qn('a:xfrm')}")
    if xfrm is None:
        return None
    off, ext = xfrm.find(qn("a:off")), xfrm.find(qn("a:ext"))
    if off is None or ext is None:
        return None
    try:
        return Rect(int(off.get("x", 0)), int(off.get("y", 0)),
                    int(ext.get("cx", 0)), int(ext.get("cy", 0)))
    except (TypeError, ValueError):
        return None


def _sp_tree(root: ET.Element) -> ET.Element | None:
    return root.find(f"{qn('p:cSld')}/{qn('p:spTree')}")


def _collect_placeholders(root: ET.Element, info: PartInfo) -> None:
    tree = _sp_tree(root)
    if tree is None:
        return
    for sp in tree.iter(qn("p:sp")):
        ph = sp.find(f"{qn('p:nvSpPr')}/{qn('p:nvPr')}/{qn('p:ph')}")
        if ph is None:
            continue
        ph_type = ph.get("type") or "body"     # умолчание по ECMA-376
        raw_idx = ph.get("idx")
        idx = int(raw_idx) if raw_idx is not None and raw_idx.isdigit() else None
        entry = Placeholder(ph_type=ph_type, idx=idx, sp=sp, rect=_rect_of(sp))
        if idx is not None:
            info.by_idx.setdefault(idx, entry)
        info.by_type.setdefault(ph_type, entry)


def _clr_map_from_master(root: ET.Element) -> dict[str, str]:
    el = root.find(qn("p:clrMap"))
    if el is None:
        return {}
    return {role: el.get(role) or role for role in MAP_ROLES}


def _clr_map_override(root: ET.Element) -> dict[str, str]:
    """`p:clrMapOvr` либо наследует мастера, либо переопределяет целиком."""
    ovr = root.find(qn("p:clrMapOvr"))
    if ovr is None:
        return {}
    override = ovr.find(qn("a:overrideClrMapping"))
    if override is None:
        return {}
    return {role: override.get(role) or role for role in MAP_ROLES}


def load_deck(pkg: Package) -> Deck:
    pres_part = pkg.main_part
    pres = pkg.xml(pres_part)

    sld_sz = pres.find(qn("p:sldSz"))
    slide_cx = int(sld_sz.get("cx", 0)) if sld_sz is not None else 0
    slide_cy = int(sld_sz.get("cy", 0)) if sld_sz is not None else 0
    size_type = sld_sz.get("type") if sld_sz is not None else None

    masters: dict[str, MasterInfo] = {}
    layouts: dict[str, LayoutInfo] = {}
    themes: dict[str, Theme] = {}

    for rel in pkg.related(pres_part, RT.SLIDE_MASTER):
        part = rel.target_part
        if part is None:
            continue
        root = pkg.xml(part)
        theme_rel = pkg.related_one(part, RT.THEME)
        theme_part = theme_rel.target_part if theme_rel else None
        if theme_part and theme_part not in themes:
            themes[theme_part] = load_theme(pkg, theme_part)

        layout_parts: list[str] = []
        for lrel in pkg.related(part, RT.SLIDE_LAYOUT):
            lpart = lrel.target_part
            if lpart is None:
                continue
            layout_parts.append(lpart)
            lroot = pkg.xml(lpart)
            linfo = LayoutInfo(
                part=lpart,
                clr_map=_clr_map_override(lroot),
                master_part=part,
                layout_type=lroot.get("type"),
            )
            _collect_placeholders(lroot, linfo)
            layouts[lpart] = linfo

        minfo = MasterInfo(
            part=part,
            clr_map=_clr_map_from_master(root),
            theme_part=theme_part,
            txstyles=root.find(qn("p:txStyles")),
            layouts=tuple(layout_parts),
        )
        _collect_placeholders(root, minfo)
        masters[part] = minfo

    # Порядок слайдов задаёт p:sldIdLst, а не имена файлов. DOM-PKG §4.
    slides: list[SlideInfo] = []
    id_lst = pres.find(qn("p:sldIdLst"))
    if id_lst is not None:
        for i, sld_id in enumerate(id_lst.findall(qn("p:sldId"))):
            rid = sld_id.get(qn("r:id"))
            part = pkg.part_for_rid(pres_part, rid) if rid else None
            if part is None or not pkg.has_part(part):
                continue
            root = pkg.xml(part)
            lrel = pkg.related_one(part, RT.SLIDE_LAYOUT)
            ovr = _clr_map_override(root)
            slides.append(
                SlideInfo(
                    part=part,
                    index=i,
                    layout_part=lrel.target_part if lrel else None,
                    clr_map_ovr=ovr or None,
                )
            )

    return Deck(
        pkg=pkg,
        presentation_part=pres_part,
        slide_cx=slide_cx,
        slide_cy=slide_cy,
        slide_size_type=size_type,
        default_text_style=pres.find(qn("p:defaultTextStyle")),
        masters=masters,
        layouts=layouts,
        themes=themes,
        slides=tuple(slides),
    )
