"""Общее для экспорта в HTML: контекст документа и слайда, единицы, цвет,
реестр картинок.

Единицы (`DOM-GEOM §1`): внутри всё в EMU, на выходе — проценты холста для
положения фигур и `cqw` (процент ширины слайда-контейнера) для всех длин. Так
слайд масштабируется вместе с окном без единой строки скрипта.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import posixpath
import re
from collections import Counter
from dataclasses import dataclass, replace
from xml.etree import ElementTree as ET

from ..analyze.color import ColorContext, find_color_child, resolve_color
from ..analyze.deck import LayoutInfo, MasterInfo
from ..analyze.theme import Theme
from ..opc.package import Package
from ..oxml.ns import qn
from ._geom import num

EMU_PER_PT = 12700

_PCT_TEXT = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*%\s*$")


def pct(value: str | None, default: float) -> float:
    """Доля из процента DrawingML. Обычно это тысячные доли процента
    (`"90000"`), но строгий OOXML и часть генераторов пишут `"90%"` — шаблон
    организаторов тоже; `int("100%")` ронял выгрузку целиком."""
    if value is None:
        return default
    m = _PCT_TEXT.match(value)
    try:
        return float(m.group(1)) / 100 if m else int(value) / 100000
    except ValueError:
        return default


def integer(value: str | None, default: int = 0) -> int:
    """Целое из атрибута; мусор — умолчание, а не исключение."""
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        try:
            return int(float(value))
        except ValueError:
            return default


# --- картинки -----------------------------------------------------------------

#: Что браузер покажет сам. EMF/WMF/TIFF/PDF — нет, их считаем и пропускаем.
_MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jpe": "image/jpeg",
    ".gif": "image/gif", ".bmp": "image/bmp", ".svg": "image/svg+xml", ".webp": "image/webp",
}
_UNSHOWABLE = {".emf": "EMF", ".wmf": "WMF", ".tif": "TIFF", ".tiff": "TIFF", ".pdf": "PDF",
               ".wdp": "HD Photo", ".jxr": "HD Photo", ".eps": "EPS", ".pict": "PICT"}


def sniff(data: bytes, part: str) -> tuple[str | None, str]:
    """Тип картинки по сигнатуре, а не по расширению: клоны донора и
    сгенерированные картинки не обязаны называться честно. Возвращает
    (mime или None, подпись для отчёта)."""
    head = data[:64]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "PNG"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "JPEG"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "GIF"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp", "WEBP"
    if head[40:44] == b" EMF":
        return None, "EMF"
    if head.startswith(b"\xd7\xcd\xc6\x9a"):
        return None, "WMF"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return None, "TIFF"
    if head.startswith(b"%PDF"):
        return None, "PDF"
    if head.startswith(b"BM"):
        return "image/bmp", "BMP"
    stripped = data[:4096].lstrip()
    if stripped.startswith(b"\xef\xbb\xbf"):
        stripped = stripped[3:].lstrip()
    if stripped.startswith((b"<svg", b"<?xml", b"<!--", b"<!DOCTYPE svg")) and b"<svg" in data[:8192]:
        return "image/svg+xml", "SVG"
    ext = posixpath.splitext(part)[1].lower()
    if ext in _UNSHOWABLE:
        return None, _UNSHOWABLE[ext]
    return _MIME_BY_EXT.get(ext), ext.lstrip(".").upper() or "?"


class Media:
    """Каждая картинка встраивается в файл один раз — как класс CSS с
    `background-image`, а фигуры на неё ссылаются. Колоды несут десятки
    мегабайт картинок, и повтор по месту употребления удвоил бы файл.

    Одинаковые байты под разными именами частей тоже дают один класс: клон
    донора часто несёт копию той же картинки."""

    def __init__(self, pkg: Package) -> None:
        self._pkg = pkg
        self._by_part: dict[str, str | None] = {}
        self._label: dict[str, str] = {}
        self._by_hash: dict[str, str] = {}
        self.rules: list[str] = []

    def use(self, part: str, doc: Doc) -> str | None:
        """Класс CSS картинки или None, если её не показать (и тогда — в отчёт)."""
        if part in self._by_part:
            cls = self._by_part[part]
            if cls is None:
                doc.note(self._label.get(part, "picture"))
            return cls
        if not self._pkg.has_part(part):
            self._by_part[part] = None
            self._label[part] = "missing picture part"
            doc.note("missing picture part")
            return None
        data = self._pkg.read(part)
        mime, label = sniff(data, part)
        if mime is None:
            self._by_part[part] = None
            self._label[part] = f"{label} picture"
            doc.note(f"{label} picture")
            return None
        digest = hashlib.sha1(data).hexdigest()
        cls = self._by_hash.get(digest)
        if cls is None:
            cls = f"m{len(self._by_hash)}"
            self._by_hash[digest] = cls
            b64 = base64.b64encode(data).decode("ascii")
            self.rules.append(f'.{cls}{{background-image:url("data:{mime};base64,{b64}")}}')
        self._by_part[part] = cls
        return cls

    @property
    def count(self) -> int:
        return len(self._by_hash)


# --- контексты ----------------------------------------------------------------


class Doc:
    """Состояние на весь документ: размеры холста, картинки, счётчики."""

    def __init__(self, pkg: Package, cx: int, cy: int) -> None:
        self.pkg = pkg
        self.cx = cx or 12192000
        self.cy = cy or 6858000
        self.media = Media(pkg)
        self.notes: Counter[str] = Counter()
        self.text_chars = 0
        self.slide_ids: dict[str, int] = {}   # часть слайда -> номер, для ссылок
        self._ids = 0

    def note(self, what: str, n: int = 1) -> None:
        self.notes[what] += n

    def uid(self, prefix: str) -> str:
        """Id для SVG (`defs`, `clipPath`): сквозной счётчик, детерминированный."""
        self._ids += 1
        return f"{prefix}{self._ids}"

    # Единицы. Проценты — только для положения фигур от холста; всё прочее
    # в cqw, чтобы отношение сторон не искажало толщины и кегли.
    def px(self, emu: float) -> str:
        return num(emu / self.cx * 100) + "%"

    def py(self, emu: float) -> str:
        return num(emu / self.cy * 100) + "%"

    def cq(self, emu: float) -> str:
        return num(emu / self.cx * 100) + "cqw"

    def cq_pt(self, pt: float) -> str:
        return self.cq(pt * EMU_PER_PT)


@dataclass(frozen=True)
class Fmt:
    """Матрица стилей темы: откуда `p:style` берёт заливку и линию."""

    fills: tuple[ET.Element, ...] = ()
    lines: tuple[ET.Element, ...] = ()
    bg_fills: tuple[ET.Element, ...] = ()


def load_fmt(pkg: Package, theme_part: str | None) -> Fmt:
    if not theme_part or not pkg.has_part(theme_part):
        return Fmt()
    fmt = pkg.xml(theme_part).find(f"{qn('a:themeElements')}/{qn('a:fmtScheme')}")
    if fmt is None:
        return Fmt()

    def kids(tag: str) -> tuple[ET.Element, ...]:
        holder = fmt.find(qn(tag))
        return tuple(holder) if holder is not None else ()

    return Fmt(fills=kids("a:fillStyleLst"), lines=kids("a:lnStyleLst"),
               bg_fills=kids("a:bgFillStyleLst"))


@dataclass(frozen=True)
class Ctx:
    """Всё, что нужно, чтобы нарисовать фигуру одного слайда.

    `part` — часть, по связям которой разрешаются `r:embed`: у декора макета
    и мастера это макет и мастер, а не слайд. Карта цветов при этом слайдовая:
    она действует на всё, что видно на слайде (`DOM-COLOR §2`)."""

    doc: Doc
    part: str
    colors: ColorContext
    theme: Theme | None
    fmt: Fmt
    master: MasterInfo | None
    layout: LayoutInfo | None
    default_text: ET.Element | None
    slide_no: int = 1
    count_text: bool = False

    def with_part(self, part: str, count_text: bool) -> Ctx:
        return replace(self, part=part, count_text=count_text)

    def with_ph(self, hex_: str | None) -> Ctx:
        return replace(self, colors=replace(self.colors, ph_hex=hex_))


# --- цвет ---------------------------------------------------------------------


Color = tuple[str, float]  # "#RRGGBB", альфа 0..1


def color(el: ET.Element | None, ctx: Ctx) -> Color | None:
    """Цветовой элемент (`a:srgbClr`, `a:schemeClr`, …) в цвет с альфой."""
    if el is None:
        return None
    unhandled: list[tuple[str, str]] = []
    try:
        rc = resolve_color(el, ctx.colors, unhandled)
    except ValueError:
        # Проценты вида "50%" в модификаторах: разборщик анализа их не знает,
        # приводим копию к тысячным долям и пробуем ещё раз.
        fixed = copy.deepcopy(el)
        for node in fixed.iter():
            for k, v in list(node.attrib.items()):
                m = _PCT_TEXT.match(v)
                if m:
                    node.set(k, str(round(float(m.group(1)) * 1000)))
        try:
            rc = resolve_color(fixed, ctx.colors, unhandled)
        except ValueError:
            ctx.doc.note("unreadable color")
            return None
    for kind, value in unhandled:
        ctx.doc.note(f"{kind} {value}")
    if rc is None:
        return None
    return rc.hex, rc.alpha


def child_color(holder: ET.Element | None, ctx: Ctx) -> Color | None:
    """Цвет внутри контейнера вроде `a:solidFill` или `a:buClr`."""
    return color(find_color_child(holder), ctx)


def css(c: Color | None) -> str:
    if c is None:
        return "transparent"
    hex_, alpha = c
    if alpha >= 0.999:
        return hex_
    r, g, b = int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16)
    return f"rgba({r},{g},{b},{num(max(alpha, 0.0), 3)})"


def mix(c: Color, other: str, t: float) -> Color:
    """Смесь двух цветов: доля `t` второго. Для приближений узоров и
    осветления граней формы."""
    a = [int(c[0][i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    m = [round(x + (y - x) * t) for x, y in zip(a, b)]
    return "#{:02X}{:02X}{:02X}".format(*m), c[1]
