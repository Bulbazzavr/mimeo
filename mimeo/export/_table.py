"""Таблица `a:tbl` в HTML-таблицу поверх слайда.

Оформление ячейки собирается слоями, как у PowerPoint: стиль таблицы по
частям (`wholeTbl`, полосы, первая/последняя строка и столбец), затем
собственные `a:tcPr` ячейки. Стиль ищется в `/ppt/tableStyles.xml`; встроенные
стили Office там не обязаны лежать — для них есть приближение ниже.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from ..oxml.ns import local_name, qn
from ._base import Ctx, css, integer
from ._paint import css_background, fill_of, find_fill, line_of, style_fill
from ._text import TextStyles, paragraphs, pseudo_level

#: «Medium Style 2 – Accent N»: стиль таблицы PowerPoint по умолчанию. В
#: tableStyles.xml его часто нет — PowerPoint знает его сам.
_MEDIUM2 = """<a:tblStyle xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 styleId="{sid}" styleName="Medium Style 2">
<a:wholeTbl><a:tcTxStyle><a:fontRef idx="minor"><a:prstClr val="black"/></a:fontRef>
<a:schemeClr val="dk1"/></a:tcTxStyle><a:tcStyle><a:tcBdr>
<a:left><a:ln w="12700"><a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:left>
<a:right><a:ln w="12700"><a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:right>
<a:top><a:ln w="12700"><a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:top>
<a:bottom><a:ln w="12700"><a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:bottom>
<a:insideH><a:ln w="12700"><a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:insideH>
<a:insideV><a:ln w="12700"><a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:insideV>
</a:tcBdr><a:fill><a:solidFill><a:schemeClr val="{acc}"><a:tint val="20000"/></a:schemeClr>
</a:solidFill></a:fill></a:tcStyle></a:wholeTbl>
<a:band1H><a:tcStyle><a:tcBdr/><a:fill><a:solidFill><a:schemeClr val="{acc}"><a:tint val="40000"/>
</a:schemeClr></a:solidFill></a:fill></a:tcStyle></a:band1H>
<a:band1V><a:tcStyle><a:tcBdr/><a:fill><a:solidFill><a:schemeClr val="{acc}"><a:tint val="40000"/>
</a:schemeClr></a:solidFill></a:fill></a:tcStyle></a:band1V>
<a:lastCol><a:tcTxStyle b="on"><a:fontRef idx="minor"><a:prstClr val="black"/></a:fontRef>
<a:schemeClr val="lt1"/></a:tcTxStyle><a:tcStyle><a:tcBdr/><a:fill><a:solidFill>
<a:schemeClr val="{acc}"/></a:solidFill></a:fill></a:tcStyle></a:lastCol>
<a:firstCol><a:tcTxStyle b="on"><a:fontRef idx="minor"><a:prstClr val="black"/></a:fontRef>
<a:schemeClr val="lt1"/></a:tcTxStyle><a:tcStyle><a:tcBdr/><a:fill><a:solidFill>
<a:schemeClr val="{acc}"/></a:solidFill></a:fill></a:tcStyle></a:firstCol>
<a:lastRow><a:tcTxStyle b="on"><a:fontRef idx="minor"><a:prstClr val="black"/></a:fontRef>
<a:schemeClr val="lt1"/></a:tcTxStyle><a:tcStyle><a:tcBdr><a:top><a:ln w="38100">
<a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:top></a:tcBdr><a:fill>
<a:solidFill><a:schemeClr val="{acc}"/></a:solidFill></a:fill></a:tcStyle></a:lastRow>
<a:firstRow><a:tcTxStyle b="on"><a:fontRef idx="minor"><a:prstClr val="black"/></a:fontRef>
<a:schemeClr val="lt1"/></a:tcTxStyle><a:tcStyle><a:tcBdr><a:bottom><a:ln w="38100">
<a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:bottom></a:tcBdr><a:fill>
<a:solidFill><a:schemeClr val="{acc}"/></a:solidFill></a:fill></a:tcStyle></a:firstRow>
</a:tblStyle>"""

#: Сетка без заливки — второй по частоте встроенный стиль.
_GRID = """<a:tblStyle xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 styleId="{sid}" styleName="No Style, Table Grid"><a:wholeTbl><a:tcTxStyle>
<a:fontRef idx="minor"><a:scrgbClr r="0" g="0" b="0"/></a:fontRef><a:schemeClr val="tx1"/>
</a:tcTxStyle><a:tcStyle><a:tcBdr>{sides}</a:tcBdr><a:fill><a:noFill/></a:fill></a:tcStyle>
</a:wholeTbl></a:tblStyle>"""
_GRID_SIDE = ('<a:{s}><a:ln w="12700"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill>'
              '</a:ln></a:{s}>')

#: Встроенные стили Office, которые узнаём по GUID: акцент «Medium Style 2».
_BUILTIN = {
    "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}": ("medium2", "accent1"),
    "{21E4AEA4-8DFA-4A89-87EB-49C32662AFE8}": ("medium2", "accent2"),
    "{F5AB1C69-6EDB-4FF4-983F-18BD219EF322}": ("medium2", "accent3"),
    "{00A15C55-8517-42AA-B614-E9B94910E393}": ("medium2", "accent4"),
    "{7DF18680-E054-41AD-8BC1-D1AEF772440D}": ("medium2", "accent5"),
    "{93296810-A885-4BE3-A3E7-6D5BEEA58F35}": ("medium2", "accent6"),
    "{073A0DAA-6AF3-43AB-8588-CEC1D06C72B9}": ("medium2", "dk1"),
    "{5940675A-B579-460E-94D1-54222C63F5DA}": ("grid", ""),
    "{2D5ABB26-0587-4C30-8999-92F81FD0307C}": ("none", ""),
}


#: Так PowerPoint называет свои стили, когда пишет их описание в файл.
_BUILTIN_NAME = re.compile(r"^(Themed|Light|Medium|Dark|No) Style", re.IGNORECASE)


def _style(ctx: Ctx, sid: str | None) -> ET.Element | None:
    """Описание стиля таблицы или None — рисовать без стиля.

    PowerPoint рисует только свои встроенные стили. Описание встроенного он
    сам кладёт в `tableStyles.xml` (имя вида «Medium Style 2 - Accent 1»),
    а чужое описание — у шаблонов из Google Slides это «Table_0» — не рисует
    вовсе: ни заливок, ни границ. Так на рендерах PowerPoint 26 сентября и в
    шаблоне VK Education (слайд 38), и в VK WorkSpace (`compose/visual.py`)."""
    if not sid:
        return None
    part = "/ppt/tableStyles.xml"
    pkg = ctx.doc.pkg
    if pkg.has_part(part):
        for st in pkg.xml(part).findall(qn("a:tblStyle")):
            if st.get("styleId") == sid:
                if sid in _BUILTIN or _BUILTIN_NAME.match(st.get("styleName") or ""):
                    return st
                return None
    kind, acc = _BUILTIN.get(sid, ("", ""))
    if kind == "none":
        return None
    if kind == "grid":
        sides = "".join(_GRID_SIDE.format(s=s) for s in
                        ("left", "right", "top", "bottom", "insideH", "insideV"))
        return ET.fromstring(_GRID.format(sid=sid, sides=sides))
    if not kind:
        ctx.doc.note(f"table style {sid}")
        acc = "accent1"
    return ET.fromstring(_MEDIUM2.format(sid=sid, acc=acc))


def _parts_for(r: int, c: int, rows: int, cols: int, flags: dict[str, bool]) -> list[str]:
    """Части стиля, действующие на ячейку, от слабой к сильной."""
    out = ["wholeTbl"]
    body_r = r - (1 if flags["firstRow"] else 0)
    body_c = c - (1 if flags["firstCol"] else 0)
    if flags["bandRow"] and body_r >= 0:
        out.append("band1H" if body_r % 2 == 0 else "band2H")
    if flags["bandCol"] and body_c >= 0:
        out.append("band1V" if body_c % 2 == 0 else "band2V")
    if flags["lastCol"] and c == cols - 1:
        out.append("lastCol")
    if flags["firstCol"] and c == 0:
        out.append("firstCol")
    if flags["lastRow"] and r == rows - 1:
        out.append("lastRow")
    if flags["firstRow"] and r == 0:
        out.append("firstRow")
    return out


def render(tbl: ET.Element, ctx: Ctx, x: float, y: float, frame_w: float,
           base_styles: TextStyles) -> str:
    """`a:tbl` в `<table>` с левым верхним углом в (x, y) EMU.

    Ширина таблицы — сумма `a:gridCol`, а не ширина рамки: PowerPoint рисует
    по сетке, а размер `p:graphicFrame` в шаблонах бывает устаревшим (в шаблоне
    VK WorkSpace рамка 3 млн EMU при сетке во всю ширину слайда). Высота строк —
    минимальная: строка растёт под текст, как у PowerPoint."""
    doc = ctx.doc
    pr = tbl.find(qn("a:tblPr"))
    flags = {k: (pr is not None and pr.get(k) in ("1", "true"))
             for k in ("firstRow", "firstCol", "lastRow", "lastCol", "bandRow", "bandCol")}
    sid_el = pr.find(qn("a:tableStyleId")) if pr is not None else None
    style = _style(ctx, (sid_el.text or "").strip() if sid_el is not None else None)
    if pr is not None and pr.find(qn("a:tableStyle")) is not None:
        style = pr.find(qn("a:tableStyle"))

    grid = [max(integer(g.get("w"), 0), 0)
            for g in tbl.findall(f"{qn('a:tblGrid')}/{qn('a:gridCol')}")]
    total = sum(grid) or int(frame_w) or 1
    rows = tbl.findall(qn("a:tr"))
    nrows, ncols = len(rows), len(grid)

    table_fill = css_background(fill_of(find_fill(pr), ctx)) if pr is not None else ""
    box = f"left:{doc.px(x)};top:{doc.py(y)};width:{doc.px(total)}"
    out = [f'<div class="s" style="{box}"><table class="tb" style="{table_fill}"><colgroup>']
    out += [f'<col style="width:{_pct(g, total)}">' for g in grid]
    out.append("</colgroup>")
    for r, tr in enumerate(rows):
        out.append(f'<tr style="height:{doc.cq(max(integer(tr.get("h"), 0), 0))}">')
        c = 0
        for tc in tr.findall(qn("a:tc")):
            if tc.get("hMerge") in ("1", "true") or tc.get("vMerge") in ("1", "true"):
                c += 1
                continue
            out.append(_cell(tc, r, c, nrows, ncols, flags, style, ctx, base_styles))
            c += 1
        out.append("</tr>")
    out.append("</table></div>")
    return "".join(out)


def _pct(w: int, total: int) -> str:
    return f"{w / total * 100:.3f}".rstrip("0").rstrip(".") + "%"


def _cell(tc: ET.Element, r: int, c: int, nrows: int, ncols: int, flags: dict[str, bool],
          style: ET.Element | None, ctx: Ctx, base_styles: TextStyles) -> str:
    doc = ctx.doc
    parts = [style.find(qn(f"a:{p}")) for p in _parts_for(r, c, nrows, ncols, flags)] \
        if style is not None else []
    parts = [p for p in parts if p is not None]

    # Заливка: последняя часть стиля, где она есть, затем своя.
    fill_css = ""
    for part in parts:
        ts = part.find(qn("a:tcStyle"))
        if ts is None:
            continue
        holder = ts.find(qn("a:fill"))
        if holder is not None:
            fill_css = css_background(fill_of(find_fill(holder), ctx)) or "background:none;"
        ref = ts.find(qn("a:fillRef"))
        if ref is not None:
            fill_css = css_background(style_fill(ref, ctx))
    tcpr = tc.find(qn("a:tcPr"))
    own = fill_of(find_fill(tcpr), ctx) if tcpr is not None else None
    if own is not None:
        fill_css = css_background(own) or "background:none;"

    # Границы: край таблицы берёт left/right/top/bottom, середина — inside*.
    rs = max(integer(tc.get("rowSpan"), 1), 1)
    cs = max(integer(tc.get("gridSpan"), 1), 1)
    side_src = {
        "left": "left" if c == 0 else "insideV",
        "right": "right" if c + cs >= ncols else "insideV",
        "top": "top" if r == 0 else "insideH",
        "bottom": "bottom" if r + rs >= nrows else "insideH",
    }
    borders = []
    for side, src in side_src.items():
        ln_el = None
        for part in parts:
            bdr = part.find(f"{qn('a:tcStyle')}/{qn('a:tcBdr')}")
            if bdr is None:
                continue
            holder = bdr.find(qn(f"a:{src}"))
            if holder is None and src.startswith("inside"):
                continue
            if holder is not None and holder.find(qn("a:ln")) is not None:
                ln_el = holder.find(qn("a:ln"))
        if tcpr is not None:
            mine = tcpr.find(qn("a:ln" + side[0].upper()))
            if mine is not None:
                ln_el = mine
        line = line_of([ln_el], None, ctx, ctx) if ln_el is not None else None
        if line is not None:
            borders.append(f"border-{side}:{doc.cq(max(line.width, 3175))} solid {css(line.color)}")

    # Текст: свойства части стиля (жирность, цвет) встают плоским уровнем.
    b = None
    color_el = None
    for part in parts:
        tx = part.find(qn("a:tcTxStyle"))
        if tx is None:
            continue
        if tx.get("b") is not None:
            b = tx.get("b") in ("on", "1", "true")
        for child in tx:
            if local_name(child.tag) in ("schemeClr", "srgbClr", "prstClr", "sysClr", "scrgbClr"):
                color_el = child
    styles = base_styles
    if b is not None or color_el is not None:
        extra = pseudo_level(b=b, color_el=_copy(color_el))
        styles = TextStyles(((extra, True),) + base_styles.containers)

    def mar(name: str, default: int) -> int:
        return integer(tcpr.get(name), default) if tcpr is not None else default

    pad = (f"padding:{doc.cq(mar('marT', 45720))} {doc.cq(mar('marR', 91440))} "
           f"{doc.cq(mar('marB', 45720))} {doc.cq(mar('marL', 91440))}")
    anchor = tcpr.get("anchor") if tcpr is not None else None
    valign = {"ctr": "middle", "b": "bottom"}.get(anchor or "t", "top")
    body = tc.find(qn("a:txBody"))
    inner = paragraphs(body, ctx, styles) if body is not None else ""
    span = (f' rowspan="{rs}"' if rs > 1 else "") + (f' colspan="{cs}"' if cs > 1 else "")
    decl = ";".join([pad, f"vertical-align:{valign}"] + borders) + ";" + fill_css
    return f'<td{span} style="{decl}">{inner}</td>'


def _copy(el: ET.Element | None) -> ET.Element | None:
    if el is None:
        return None
    return ET.fromstring(ET.tostring(el))
