"""Своя таблица и диаграмма на месте фигуры-хозяина — `Z-32`, `ADR-0026`.

Первый случай, когда сборка **строит** объект, а не клонирует его: в трёх
выданных шаблонах диаграмм OOXML ноль (`WORKLOG/2026-09-15-tz-templates.md`).
Стиль при этом берётся у шаблона, а не придумывается:

* место — фигура-хозяин из раскладки донора (`plan/matching.py`,
  `visual_host`): рамка встаёт в её координаты и на её место в порядке фигур,
  отступив от мест, куда лёг наш текст (`clear_of`);
* таблица — встроенным стилем PowerPoint: стиль шаблона по умолчанию, если он
  встроенный, иначе «Средний стиль 2 — акцент 1», который PowerPoint ставит
  новой таблице сам; цвета у обоих — акценты темы шаблона (`table_style`);
* диаграмма — цвета рядов из акцентов темы, текст — цветом и кеглем текста
  хозяина, шрифт — текстовый шрифт темы (`+mn-lt`);
* кегль таблицы подбирается под высоту места: PowerPoint растит строку под
  текст, и таблица уехала бы за слайд, а проверка вёрстки видит только надписи
  (`PLAN-10.0`, проверка 1, п. 5).

Диаграмме нужна книга `.xlsx` — без неё «Изменить данные» в PowerPoint не
откроется; книга собирается здесь же стандартной библиотекой, с фиксированными
метками времени (`ADR-0003`).
"""

from __future__ import annotations

import copy
import io
import math
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from ..oxml.ns import NS, qn
from ..plan.visual import ChartData, TableData, number
from .package import PackageWriter

for _prefix in ("a", "p", "r", "c"):
    ET.register_namespace(_prefix, NS[_prefix])

CT_CHART = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
CT_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
RT_CHART = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"
RT_PACKAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package"
_URI_TABLE = "http://schemas.openxmlformats.org/drawingml/2006/table"
_URI_CHART = "http://schemas.openxmlformats.org/drawingml/2006/chart"

#: «Средний стиль 2 — акцент 1»: встроенный стиль, который PowerPoint ставит
#: новой таблице. Определение не обязано лежать в пакете — PowerPoint знает
#: встроенные стили по идентификатору.
DEFAULT_TABLE_STYLE = "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"

#: Слайд 13.33 × 7.5 дюйма — высота 6 858 000 EMU. Кегли ниже заданы для него
#: и масштабируются высотой слайда: холст VK Tech вдвое крупнее обычного.
_BASE_SLIDE_CY = 6858000
_EMU_PER_PT = 12700
#: Кегль таблицы, сотые пункта: не крупнее, не мельче (на базовом слайде).
_TABLE_MAX_SZ = 1800
_TABLE_MIN_SZ = 1000
#: Кегль подписей диаграммы: доля кегля хозяина, в тех же границах.
_CHART_SZ_SHARE = 0.75
_CHART_MAX_SZ = 1600
_CHART_MIN_SZ = 1000
#: Средняя ширина знака в долях кегля — кириллица и латиница рубленым шрифтом;
#: жирная шапка шире. Оценка, а не замер: судья — растр.
_CHAR_WIDTH = 0.56
_CHAR_WIDTH_BOLD = 0.62
_LINE = 1.2
#: Поля ячейки таблицы по умолчанию (`a:tcPr`): 0.1 дюйма слева и справа,
#: 0.05 — сверху и снизу.
_CELL_MARGIN_X = 91440
_CELL_MARGIN_Y = 45720


# --- общее -------------------------------------------------------------------


def slide_height(writer: PackageWriter) -> int:
    """Высота слайда из `presentation.xml`; нет — базовая."""
    try:
        size = writer.xml("/ppt/presentation.xml").find(qn("p:sldSz"))
        return int(size.get("cy")) if size is not None else _BASE_SLIDE_CY
    except (KeyError, TypeError, ValueError):
        return _BASE_SLIDE_CY


def _scale(slide_cy: int) -> float:
    return max(slide_cy, 1) / _BASE_SLIDE_CY


def host_size(shape: ET.Element) -> int | None:
    """Кегль текста хозяина, сотые пункта: первый `sz` в его тексте."""
    body = shape.find(qn("p:txBody"))
    if body is None:
        return None
    for tag in ("a:rPr", "a:endParaRPr", "a:defRPr"):
        for el in body.iter(qn(tag)):
            if el.get("sz", "").isdigit():
                return int(el.get("sz"))
    return None


def host_fill(shape: ET.Element) -> ET.Element | None:
    """Заливка текста хозяина (`a:solidFill` первого прогона): на тёмном
    доноре текст бывает покрашен явно, и `tx1` диаграммы там пропал бы
    (`PLAN-10.0`, проверка 1, п. 6)."""
    body = shape.find(qn("p:txBody"))
    if body is None:
        return None
    for tag in ("a:rPr", "a:endParaRPr", "a:defRPr"):
        for el in body.iter(qn(tag)):
            fill = el.find(qn("a:solidFill"))
            if fill is not None and len(fill):
                return copy.deepcopy(fill)
    return None


def _next_id(tree: ET.Element) -> int:
    ids = [int(el.get("id")) for el in tree.iter(qn("p:cNvPr")) if (el.get("id") or "").isdigit()]
    return max(ids, default=1) + 1


def _put_in_place(tree: ET.Element, host: ET.Element, frame: ET.Element) -> None:
    """Рамка встаёт на место хозяина в порядке фигур. Хозяин в группе —
    рамка встаёт сразу за этой группой верхнего уровня: координаты у рамки
    абсолютные, а у группы свои."""
    sp_tree = tree.find(f"{qn('p:cSld')}/{qn('p:spTree')}")
    parents = {child: parent for parent in tree.iter() for child in parent}
    parent = parents.get(host)
    if parent is None or sp_tree is None:
        return
    if parent is sp_tree:
        at = list(sp_tree).index(host)
        sp_tree.remove(host)
        sp_tree.insert(at, frame)
        return
    top = host
    while parents.get(top) is not None and parents[top] is not sp_tree:
        top = parents[top]
    parent.remove(host)
    at = list(sp_tree).index(top) + 1 if top in list(sp_tree) else len(sp_tree)
    sp_tree.insert(at, frame)


def _frame(shape_id: int, name: str, rect, graphic_data: ET.Element, lock: bool) -> ET.Element:
    frame = ET.Element(qn("p:graphicFrame"))
    nv = ET.SubElement(frame, qn("p:nvGraphicFramePr"))
    ET.SubElement(nv, qn("p:cNvPr"), {"id": str(shape_id), "name": f"{name} {shape_id}"})
    locks = ET.SubElement(nv, qn("p:cNvGraphicFramePr"))
    if lock:
        ET.SubElement(locks, qn("a:graphicFrameLocks"), {"noGrp": "1"})
    ET.SubElement(nv, qn("p:nvPr"))
    xfrm = ET.SubElement(frame, qn("p:xfrm"))
    ET.SubElement(xfrm, qn("a:off"), {"x": str(rect[0]), "y": str(rect[1])})
    ET.SubElement(xfrm, qn("a:ext"), {"cx": str(rect[2]), "cy": str(rect[3])})
    graphic = ET.SubElement(frame, qn("a:graphic"))
    graphic.append(graphic_data)
    return frame


# --- таблица -----------------------------------------------------------------


def table_style(writer: PackageWriter) -> str:
    """Стиль таблиц по умолчанию у шаблона (`tableStyles.xml@def`), если это
    **встроенный** стиль PowerPoint, иначе «Средний стиль 2 — акцент 1».

    Свой стиль, описанный в файле шаблона, не берём: растр 26 сентября —
    стиль WorkSpace (шаблон выгружен из Google Slides) PowerPoint не нарисовал
    вовсе, ни заливки, ни границ, и таблица на тёмном слайде пропала целиком,
    хотя PowerPoint её «видел» (COM: видима, 470 × 137 пт). Встроенный стиль
    PowerPoint знает всегда, а цвета у него — акценты темы шаблона. Встроенный
    от своего отличаем тем, что его описания в файле нет."""
    name = "/ppt/tableStyles.xml"
    if writer.has(name):
        try:
            styles = writer.xml(name)
        except ET.ParseError:
            return DEFAULT_TABLE_STYLE
        value = styles.get("def")
        own = {el.get("styleId") for el in styles.iter(qn("a:tblStyle"))}
        if value and value not in own:
            return value
    return DEFAULT_TABLE_STYLE


def column_widths(data: TableData, width: int) -> list[int]:
    """Ширины колонок — по самой длинной ячейке колонки, но не уже половины
    средней: узкая колонка чисел не должна сжаться в столбик."""
    rows = (data.header,) + data.rows
    need = [max(len(r[i]) for r in rows) + 2 for i in range(len(data.header))]
    floor = sum(need) / len(need) / 2
    need = [max(n, floor) for n in need]
    total = sum(need)
    widths = [int(width * n / total) for n in need]
    widths[-1] = width - sum(widths[:-1])
    return widths


def _lines(text: str, width: int, sz: int, bold: bool) -> int:
    """Сколько строк займёт текст ячейки при кегле `sz` (сотые пункта)."""
    usable = max(width - 2 * _CELL_MARGIN_X, 1)
    per_char = (_CHAR_WIDTH_BOLD if bold else _CHAR_WIDTH) * sz / 100 * _EMU_PER_PT
    lines, current = 1, 0.0
    for word in text.split() or [""]:
        w = len(word) * per_char
        space = per_char if current else 0.0
        if current and current + space + w > usable:
            lines += 1
            current = w
        else:
            current += space + w
        while current > usable:          # слово длиннее ячейки рвётся
            lines += 1
            current -= usable
    return lines


def row_heights(data: TableData, widths: list[int], sz: int) -> list[int]:
    heights = []
    for n, row in enumerate((data.header,) + data.rows):
        lines = max(_lines(c, w, sz, n == 0) for c, w in zip(row, widths))
        heights.append(int(lines * _LINE * sz / 100 * _EMU_PER_PT + 2 * _CELL_MARGIN_Y))
    return heights


def fit_table(data: TableData, width: int, height: int, base: int | None, slide_cy: int) -> tuple[int, list[int], list[int]]:
    """Кегль (сотые пункта), ширины колонок и высоты строк: самый крупный
    кегль не крупнее текста хозяина, при котором таблица по оценке влезает в
    высоту места. Не влезает и при наименьшем — наименьший: мельче таблицу не
    прочтут, а решать это будет растр."""
    scale = _scale(slide_cy)
    top = int(_TABLE_MAX_SZ * scale)
    low = int(_TABLE_MIN_SZ * scale)
    start = min(base, top) if base else top
    start = max(start, low)
    widths = column_widths(data, width)
    sz = start
    while True:
        heights = row_heights(data, widths, sz)
        if sum(heights) <= height or sz <= low:
            return sz, widths, heights
        sz = max(low, sz - max(50, int(100 * scale)))


def _cell(text: str, sz: int, align: str | None) -> ET.Element:
    tc = ET.Element(qn("a:tc"))
    body = ET.SubElement(tc, qn("a:txBody"))
    ET.SubElement(body, qn("a:bodyPr"))
    ET.SubElement(body, qn("a:lstStyle"))
    para = ET.SubElement(body, qn("a:p"))
    if align:
        ET.SubElement(para, qn("a:pPr"), {"algn": align})
    if text:
        run = ET.SubElement(para, qn("a:r"))
        ET.SubElement(run, qn("a:rPr"), {"lang": "ru-RU", "sz": str(sz), "dirty": "0"})
        ET.SubElement(run, qn("a:t")).text = text
    else:
        ET.SubElement(para, qn("a:endParaRPr"), {"lang": "ru-RU", "sz": str(sz), "dirty": "0"})
    ET.SubElement(tc, qn("a:tcPr"), {"anchor": "ctr"})
    return tc


def table_frame(shape_id: int, rect, data: TableData, sz: int, widths: list[int],
                heights: list[int], style: str) -> ET.Element:
    tbl = ET.Element(qn("a:tbl"))
    pr = ET.SubElement(tbl, qn("a:tblPr"), {"firstRow": "1", "bandRow": "1"})
    ET.SubElement(pr, qn("a:tableStyleId")).text = style
    grid = ET.SubElement(tbl, qn("a:tblGrid"))
    for w in widths:
        ET.SubElement(grid, qn("a:gridCol"), {"w": str(w)})
    # Колонка, где все ячейки строк — числа, выровнена вправо: так числа
    # читаются разрядами.
    numeric = [all(number(r[i]) is not None for r in data.rows if r[i]) and any(r[i] for r in data.rows)
               for i in range(len(data.header))]
    for n, (row, h) in enumerate(zip((data.header,) + data.rows, heights)):
        tr = ET.SubElement(tbl, qn("a:tr"), {"h": str(h)})
        for i, text in enumerate(row):
            tr.append(_cell(text, sz, "r" if numeric[i] else None))
    data_el = ET.Element(qn("a:graphicData"), {"uri": _URI_TABLE})
    data_el.append(tbl)
    x, y, cx, _ = rect
    return _frame(shape_id, "Таблица", (x, y, cx, sum(heights)), data_el, lock=True)


# --- диаграмма ---------------------------------------------------------------


def number_format(series) -> str:
    """Формат чисел диаграммы по тому, как они записаны в тексте: разряды,
    знаки после запятой, процент. Разделители подставит PowerPoint по языку
    системы — «41 200» на русской."""
    labels = [lab for s in series for lab in s.labels]
    values = [v for s in series for v in s.values]
    decimals = 0
    for lab in labels:
        digits = lab.replace(" ", "").replace(" ", "").rstrip("%₽$€ ")
        for sep in (",", "."):
            if sep in digits:
                decimals = max(decimals, len(digits.rsplit(sep, 1)[1].rstrip("%")))
    grouped = any(abs(v) >= 10000 for v in values) or any(" " in lab.strip() for lab in labels)
    body = ("#,##0" if grouped else "0") + ("." + "0" * decimals if decimals else "")
    if any(lab.strip().endswith("%") for lab in labels):
        body += '"%"'
    return body


def _v(value: float) -> str:
    return str(int(value)) if value == int(value) else repr(value)


def _color(fill: ET.Element | None, alpha: int | None = None) -> str:
    """`a:solidFill` цветом текста хозяина (или `tx1`), с прозрачностью."""
    if fill is not None and len(fill):
        colour = copy.deepcopy(fill[0])
        if alpha is not None:
            for old in colour.findall(qn("a:alpha")):
                colour.remove(old)
            ET.SubElement(colour, qn("a:alpha"), {"val": str(alpha)})
        inner = ET.tostring(colour, encoding="unicode")
    else:
        tail = f'<a:alpha val="{alpha}"/>' if alpha is not None else ""
        inner = f'<a:schemeClr val="tx1">{tail}</a:schemeClr>' if tail else '<a:schemeClr val="tx1"/>'
    return f"<a:solidFill>{inner}</a:solidFill>"


def _txpr(sz: int, fill: str, bold: bool = False, rot: int | None = None) -> str:
    body = f' rot="{rot}" vert="horz"' if rot is not None else ""
    b = ' b="1"' if bold else ' b="0"'
    return (f"<c:txPr><a:bodyPr{body}/><a:lstStyle/><a:p><a:pPr>"
            f'<a:defRPr sz="{sz}"{b}>{fill}<a:latin typeface="+mn-lt"/><a:cs typeface="+mn-cs"/>'
            f'</a:defRPr></a:pPr><a:endParaRPr lang="ru-RU"/></a:p></c:txPr>')


def _accent(k: int) -> str:
    """Цвет k-го ряда или доли: акценты темы по кругу, второй круг темнее."""
    base = f"accent{k % 6 + 1}"
    return f'<a:schemeClr val="{base}"/>' if k < 6 else f'<a:schemeClr val="{base}"><a:lumMod val="60000"/></a:schemeClr>'


def _col(n: int) -> str:
    return "ABCDEFGHIJ"[n]


def _str_cache(ref: str, values) -> str:
    pts = "".join(f'<c:pt idx="{i}"><c:v>{escape(v)}</c:v></c:pt>' for i, v in enumerate(values))
    return f"<c:strRef><c:f>{ref}</c:f><c:strCache><c:ptCount val=\"{len(values)}\"/>{pts}</c:strCache></c:strRef>"


def _num_cache(ref: str, values, fmt: str) -> str:
    pts = "".join(f'<c:pt idx="{i}"><c:v>{_v(v)}</c:v></c:pt>' for i, v in enumerate(values))
    return (f"<c:numRef><c:f>{ref}</c:f><c:numCache><c:formatCode>{escape(fmt)}</c:formatCode>"
            f'<c:ptCount val="{len(values)}"/>{pts}</c:numCache></c:numRef>')


def _labels(fmt: str, sz: int, fill: str, position: str | None, pie: bool = False) -> str:
    pos = f'<c:dLblPos val="{position}"/>' if position else ""
    cat = "1" if pie else "0"
    return (f'<c:dLbls><c:numFmt formatCode="{escape(fmt, {chr(34): "&quot;"})}" sourceLinked="0"/>'
            f"<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr>{_txpr(sz, fill)}{pos}"
            f'<c:showLegendKey val="0"/><c:showVal val="1"/><c:showCatName val="{cat}"/>'
            f'<c:showSerName val="0"/><c:showPercent val="0"/><c:showBubbleSize val="0"/>'
            + ('<c:separator>: </c:separator><c:showLeaderLines val="1"/>' if pie else "")
            + "</c:dLbls>")


def chart_xml(data: ChartData, sz: int, fill_el: ET.Element | None) -> bytes:
    """Часть `/ppt/charts/chartN.xml`: столбцы, полосы, линия или круг.

    Порядок элементов — по схеме DrawingML Chart: PowerPoint не открывает файл,
    где он нарушен, а `python-pptx` и наш разбор этого не заметят."""
    kind = data.type
    categories, series = list(data.categories), list(data.series)
    if kind == "bar":
        # Полосы рисуются снизу вверх: первая подпись текста встала бы
        # последней. Переворачиваем данные, а не ось — книга останется
        # согласованной с кэшем.
        categories = categories[::-1]
        series = [type(s)(s.name, s.values[::-1], s.labels[::-1]) for s in series]
    n = len(categories)
    fmt = number_format(series)
    text = _color(fill_el)
    grid = _color(fill_el, 25000)
    axis_line = _color(fill_el, 45000)
    cat_ref = f"Sheet1!$A$2:$A${n + 1}"

    sers = []
    for k, s in enumerate(series):
        col = _col(k + 1)
        head = (f'<c:idx val="{k}"/><c:order val="{k}"/>'
                f"<c:tx>{_str_cache(f'Sheet1!${col}$1', [s.name or data.unit or 'Значения'])}</c:tx>")
        cat = f"<c:cat>{_str_cache(cat_ref, categories)}</c:cat>"
        val = f"<c:val>{_num_cache(f'Sheet1!${col}$2:${col}${n + 1}', s.values, fmt)}</c:val>"
        if kind in ("column", "bar"):
            sers.append(f"<c:ser>{head}<c:spPr><a:solidFill>{_accent(k)}</a:solidFill></c:spPr>"
                        f'<c:invertIfNegative val="0"/>{_labels(fmt, sz, text, "outEnd")}{cat}{val}</c:ser>')
        elif kind == "line":
            sers.append(f"<c:ser>{head}<c:spPr><a:ln w=\"{int(38100 * sz / 1400)}\" cap=\"rnd\">"
                        f"<a:solidFill>{_accent(k)}</a:solidFill><a:round/></a:ln></c:spPr>"
                        f'<c:marker><c:symbol val="circle"/><c:size val="7"/><c:spPr><a:solidFill>{_accent(k)}'
                        f"</a:solidFill><a:ln><a:noFill/></a:ln></c:spPr></c:marker>"
                        f'{_labels(fmt, sz, text, "t")}{cat}{val}<c:smooth val="0"/></c:ser>')
        else:
            points = "".join(
                f'<c:dPt><c:idx val="{i}"/><c:bubble3D val="0"/><c:spPr><a:solidFill>{_accent(i)}</a:solidFill>'
                f'<a:ln w="19050"><a:solidFill><a:schemeClr val="bg1"/></a:solidFill></a:ln></c:spPr></c:dPt>'
                for i in range(n))
            sers.append(f"<c:ser>{head}{points}{_labels(fmt, sz, text, 'bestFit', pie=True)}{cat}{val}</c:ser>")

    ax_cat, ax_val = 505050, 606060
    legend = len(series) > 1
    if kind == "pie":
        plot = f'<c:pieChart><c:varyColors val="1"/>{"".join(sers)}<c:firstSliceAng val="0"/></c:pieChart>'
        axes = ""
    else:
        if kind == "line":
            plot = (f'<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>{"".join(sers)}'
                    f'<c:marker val="1"/><c:axId val="{ax_cat}"/><c:axId val="{ax_val}"/></c:lineChart>')
        else:
            direction = "col" if kind == "column" else "bar"
            plot = (f'<c:barChart><c:barDir val="{direction}"/><c:grouping val="clustered"/>'
                    f'<c:varyColors val="0"/>{"".join(sers)}<c:gapWidth val="60"/>'
                    f'<c:overlap val="-10"/><c:axId val="{ax_cat}"/><c:axId val="{ax_val}"/></c:barChart>')
        cat_pos, val_pos = ("l", "b") if kind == "bar" else ("b", "l")
        title = ""
        if data.unit:
            rot = None if kind == "bar" else -5400000
            rot_attr = f' rot="{rot}" vert="horz"' if rot is not None else ""
            title = (f"<c:title><c:tx><c:rich><a:bodyPr{rot_attr}/><a:lstStyle/><a:p><a:pPr>"
                     f'<a:defRPr sz="{sz}" b="0">{text}<a:latin typeface="+mn-lt"/></a:defRPr></a:pPr>'
                     f'<a:r><a:rPr lang="ru-RU" sz="{sz}" b="0">{text}<a:latin typeface="+mn-lt"/></a:rPr>'
                     f"<a:t>{escape(data.unit)}</a:t></a:r></a:p></c:rich></c:tx>"
                     f'<c:overlay val="0"/></c:title>')
        axes = (f'<c:catAx><c:axId val="{ax_cat}"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
                f'<c:delete val="0"/><c:axPos val="{cat_pos}"/><c:numFmt formatCode="General" sourceLinked="0"/>'
                f'<c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/>'
                f'<c:spPr><a:ln w="9525">{axis_line}</a:ln></c:spPr>{_txpr(sz, text)}'
                f'<c:crossAx val="{ax_val}"/><c:crosses val="autoZero"/><c:auto val="1"/>'
                f'<c:lblAlgn val="ctr"/><c:lblOffset val="100"/><c:noMultiLvlLbl val="0"/></c:catAx>'
                f'<c:valAx><c:axId val="{ax_val}"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
                f'<c:delete val="0"/><c:axPos val="{val_pos}"/>'
                f'<c:majorGridlines><c:spPr><a:ln w="9525">{grid}</a:ln></c:spPr></c:majorGridlines>'
                f'{title}<c:numFmt formatCode="{escape(fmt, {chr(34): "&quot;"})}" sourceLinked="0"/>'
                f'<c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/>'
                f"<c:spPr><a:ln><a:noFill/></a:ln></c:spPr>{_txpr(sz, text)}"
                f'<c:crossAx val="{ax_cat}"/><c:crosses val="autoZero"/>'
                f'<c:crossBetween val="between"/></c:valAx>')
    legend_xml = (f'<c:legend><c:legendPos val="b"/><c:overlay val="0"/>{_txpr(sz, text)}</c:legend>'
                  if legend else "")
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<c:chartSpace xmlns:c="{NS["c"]}" xmlns:a="{NS["a"]}" xmlns:r="{NS["r"]}">'
        '<c:date1904 val="0"/><c:lang val="ru-RU"/><c:roundedCorners val="0"/>'
        f'<c:chart><c:autoTitleDeleted val="1"/><c:plotArea><c:layout/>{plot}{axes}'
        '<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr></c:plotArea>'
        f'{legend_xml}<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart>'
        '<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr>'
        f"{_txpr(sz, text)}"
        '<c:externalData r:id="rId1"><c:autoUpdate val="0"/></c:externalData>'
        "</c:chartSpace>"
    )
    return xml.encode("utf-8")


#: Метка времени записей книги: ZIP не знает дат раньше 1980 года.
_XLSX_TIME = (1980, 1, 1, 0, 0, 0)
_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def workbook(data: ChartData) -> bytes:
    """Книга `.xlsx` с данными диаграммы: подписи в колонке A, ряды — B, C…
    Строки — встроенными строками (`inlineStr`), без общей таблицы строк."""
    categories = list(data.categories)
    series = list(data.series)
    if data.type == "bar":
        categories = categories[::-1]
        series = [type(s)(s.name, s.values[::-1], s.labels[::-1]) for s in series]

    def text_cell(ref: str, value: str) -> str:
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'

    rows = ['<row r="1">' + "".join(text_cell(f"{_col(k + 1)}1", s.name or data.unit or "Значения")
                                    for k, s in enumerate(series)) + "</row>"]
    for i, cat in enumerate(categories):
        cells = text_cell(f"A{i + 2}", cat) + "".join(
            f'<c r="{_col(k + 1)}{i + 2}"><v>{_v(s.values[i])}</v></c>' for k, s in enumerate(series))
        rows.append(f'<row r="{i + 2}">{cells}</row>')
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.styles+xml"/></Types>'),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{_DOC_REL}/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{_SHEET_NS}" xmlns:r="{_DOC_REL}">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{_DOC_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="rId2" Type="{_DOC_REL}/styles" Target="styles.xml"/>'
            "</Relationships>"),
        "xl/styles.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<styleSheet xmlns="{_SHEET_NS}">'
            '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            "</styleSheet>"),
        "xl/worksheets/sheet1.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{_SHEET_NS}"><sheetData>{"".join(rows)}</sheetData></worksheet>'),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(parts):
            info = zipfile.ZipInfo(name, date_time=_XLSX_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            zf.writestr(info, parts[name].encode("utf-8"))
    return buf.getvalue()


def chart_size(base: int | None, slide_cy: int) -> int:
    scale = _scale(slide_cy)
    sz = int(base * _CHART_SZ_SHARE) if base else int(_CHART_MAX_SZ * scale)
    return max(int(_CHART_MIN_SZ * scale), min(int(_CHART_MAX_SZ * scale), sz))


# --- сборка ------------------------------------------------------------------

#: Зазор между рамкой и соседним текстом: 0.1 дюйма.
_GAP = 91440


def clear_of(rect, others) -> tuple[int, int, int, int]:
    """Место `rect`, от которого отрезано всё, что накрывает наш текст.

    Растр 26 сентября (VK Tech): текстовое место слева заходило под место
    таблицы на 0.2 дюйма, план счёл это перекрытие малым и положил туда тезис —
    конец строки «показал 0» спрятался под шапкой таблицы. Для каждого
    накрытого прямоугольника берётся тот из четырёх срезов — слева, справа,
    сверху, снизу, — что оставляет рамке больше площади."""
    x, y, cx, cy = rect
    for ox, oy, ocx, ocy in others:
        if min(x + cx, ox + ocx) <= max(x, ox) or min(y + cy, oy + ocy) <= max(y, oy):
            continue
        cuts = [
            (ox + ocx + _GAP, y, x + cx - (ox + ocx + _GAP), cy),     # текст слева
            (x, y, ox - _GAP - x, cy),                                 # текст справа
            (x, oy + ocy + _GAP, cx, y + cy - (oy + ocy + _GAP)),     # текст сверху
            (x, y, cx, oy - _GAP - y),                                 # текст снизу
        ]
        best = max(cuts, key=lambda r: max(r[2], 0) * max(r[3], 0))
        if best[2] <= 0 or best[3] <= 0:
            return rect                     # срезать нечего — место как было
        x, y, cx, cy = best
    return x, y, cx, cy


def place(writer: PackageWriter, slide_part: str, tree: ET.Element, host: ET.Element,
          rect, data, index: int, slide_cy: int, others=()) -> str | None:
    """Ставит таблицу или диаграмму `data` на место фигуры `host`.

    `rect` — (x, y, cx, cy) места в EMU, абсолютные координаты слайда;
    `others` — места слайда, куда лёг наш текст: рамка от них отступает
    (`clear_of`); `index` — сквозной номер диаграммы по колоде: имя части от
    него (урок `Z-28a` — две картинки одного слайда делили одну часть).
    Возвращает текст предупреждения или `None`."""
    rect = clear_of(rect, others)
    shape_id = _next_id(tree)
    base = host_size(host)
    if isinstance(data, TableData):
        sz, widths, heights = fit_table(data, rect[2], rect[3], base, slide_cy)
        frame = table_frame(shape_id, rect, data, sz, widths, heights, table_style(writer))
        _put_in_place(tree, host, frame)
        if sum(heights) > rect[3]:
            return (f"таблица {len(data.rows) + 1}×{len(data.header)} по оценке выше места "
                    f"на {math.ceil((sum(heights) - rect[3]) / 12700)} пт и при наименьшем кегле")
        return None
    if isinstance(data, ChartData):
        part = f"/ppt/charts/mimeo-chart{index}.xml"
        book = f"/ppt/embeddings/mimeo-chart{index}.xlsx"
        writer.write(part, chart_xml(data, chart_size(base, slide_cy), host_fill(host)), CT_CHART)
        writer.write(book, workbook(data))
        writer.content_types.ensure_default("xlsx", CT_XLSX)
        chart_rels = writer.rels(part)
        chart_rels.add(RT_PACKAGE, writer.relative(part, book))
        writer.put_rels(part, chart_rels)
        rels = writer.rels(slide_part)
        rid = rels.add(RT_CHART, writer.relative(slide_part, part))
        writer.put_rels(slide_part, rels)
        graphic = ET.Element(qn("a:graphicData"), {"uri": _URI_CHART})
        ET.SubElement(graphic, qn("c:chart"), {qn("r:id"): rid})
        _put_in_place(tree, host, _frame(shape_id, "Диаграмма", rect, graphic, lock=False))
        return None
    return "неизвестные данные таблицы или диаграммы — место оставлено донору"
