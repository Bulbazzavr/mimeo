"""Текст фигуры в HTML. DOM-TEXT §1–§6.

Текст остаётся текстом: его можно выделить и найти поиском браузера. Кегли и
отступы — в `cqw`, поэтому строки ломаются примерно там же, где у PowerPoint,
при любой ширине окна; точного совпадения нет и быть не может — метрики
шрифтов браузера не те же.

Цепочка наследования (`DOM-TEXT §2`) строится вызывающим как список
контейнеров стилей уровней — `TextStyles`; свойства разрешаются по одному.
`a:pPr/a:defRPr` самого абзаца в цепочку прогона не входит: PowerPoint его при
показе не использует.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from ..oxml.ns import RT, local_name, qn
from ._base import Color, Ctx, child_color, css, integer, pct
from ._geom import num
from ._paint import FILL_TAGS, fill_of, first_color

_FILL_QN = tuple(qn(f"a:{t}") for t in FILL_TAGS)
_CYRILLIC = re.compile("[Ѐ-ӿ]")


@dataclass(frozen=True)
class TextStyles:
    """Контейнеры стилей уровней после самого абзаца, от сильного к слабому.

    Контейнер — `a:lstStyle`, `p:titleStyle` и подобные (уровни `a:lvlNpPr`)
    либо «плоский» элемент, общий для всех уровней: так в цепочку встают
    `a:fontRef` стиля фигуры и текстовый стиль ячейки таблицы."""

    containers: tuple[tuple[ET.Element, bool], ...] = ()

    def levels(self, lvl: int) -> list[ET.Element]:
        out: list[ET.Element] = []
        tag = qn(f"a:lvl{lvl + 1}pPr")
        for holder, flat in self.containers:
            if flat:
                out.append(holder)
                continue
            el = holder.find(tag)
            if el is not None:
                out.append(el)
            d = holder.find(qn("a:defPPr"))
            if d is not None:
                out.append(d)
        return out


def pseudo_level(b: bool | None = None, i: bool | None = None,
                 color_el: ET.Element | None = None, latin: str | None = None) -> ET.Element:
    """Плоский уровень из свойств, которых нет в `a:lstStyle`: цвет и
    гарнитура `a:fontRef`, жирность ячейки из стиля таблицы."""
    lvl = ET.Element(qn("a:lvl1pPr"))
    attrs = {}
    if b is not None:
        attrs["b"] = "1" if b else "0"
    if i is not None:
        attrs["i"] = "1" if i else "0"
    rpr = ET.SubElement(lvl, qn("a:defRPr"), attrs)
    if color_el is not None:
        ET.SubElement(rpr, qn("a:solidFill")).append(color_el)
    if latin:
        ET.SubElement(rpr, qn("a:latin"), {"typeface": latin})
    return lvl


# --- гарнитуры -------------------------------------------------------------------

#: Метрически совместимые замены: у экспертов Linux, а там нет Arial, но есть
#: Liberation Sans той же ширины — строки ломаются там же.
_FALLBACKS = {
    "arial": ("Liberation Sans", "Arimo", "Helvetica"),
    "helvetica": ("Arial", "Liberation Sans"),
    "times new roman": ("Liberation Serif", "Tinos", "Times"),
    "courier new": ("Liberation Mono", "Cousine", "Courier"),
    "calibri": ("Carlito",),
    "cambria": ("Caladea",),
    "georgia": ("Gelasio",),
    "arial narrow": ("Liberation Sans Narrow",),
}
_SERIF = ("times", "georgia", "garamond", "cambria", "palatino", "baskerville", "book antiqua",
          "constantia", "didot", "bodoni", "century schoolbook")
_MONO = ("mono", "courier", "consolas", "menlo", "monaco")

#: Высота строки при «одинарном» интервале в долях кегля. Замер по рендерам
#: PowerPoint шаблонов организаторов 26 сентября: 1.17–1.22 кегля и для Arial,
#: и для Play, от 9 до 48 пт — берём одно число для всех гарнитур.
LINE_FACTOR = 1.2
#: Верхний и нижний выносы шрифта в долях кегля (Arial, он же запасной у
#: браузера): по ним браузер ставит базовую линию в строке.
_ASC = 0.905
_DESC = 0.212


#: Запас по классу гарнитуры: если шрифта шаблона у зрителя нет (встроенный
#: в .pptx браузеру не достаётся), лучше метрически знакомый Arial или его
#: двойник, чем `sans-serif` Linux — DejaVu Sans заметно шире, и строки
#: переносились бы не там, где у PowerPoint.
_CLASS_FALLBACKS = {
    "sans-serif": ("Arial", "Liberation Sans", "Arimo", "Helvetica"),
    "serif": ("Times New Roman", "Liberation Serif", "Tinos", "Times"),
    "monospace": ("Courier New", "Liberation Mono", "Cousine", "Courier"),
}


def font_stack(face: str | None) -> str:
    """Список гарнитур для CSS. Имена — в одинарных кавычках: стек стоит
    внутри атрибута `style="…"`, и двойная кавычка его бы оборвала."""
    low = (face or "").lower()
    generic = "sans-serif"
    if any(m in low for m in _MONO):
        generic = "monospace"
    elif ("serif" in low and "sans" not in low) or any(s in low for s in _SERIF):
        generic = "serif"
    parts: list[str] = []
    for name in ([face] if face else []) + list(_FALLBACKS.get(low, ())) + list(
            _CLASS_FALLBACKS[generic]):
        if name.lower() not in (p.lower() for p in parts):
            parts.append(name)
    quoted = ",".join("'" + re.sub(r"[\"'<>&;\\]", "", p) + "'" for p in parts)
    return f"{quoted},{generic}"


#: Маркеры из символьных шрифтов в Юникод: у браузера нет Wingdings.
_WINGDINGS = {
    "l": "●", "n": "■", "q": "❑", "u": "◆", "v": "❖", "w": "⬥",
    "§": "▪", "Ø": "➢", "ü": "✔", "è": "➔", "à": "➔", "o": "□",
    "p": "◻", "Ÿ": "•", "¨": "◻", "û": "✘", "ý": "☒", "þ": "☑",
    "ð": "⇒", "ß": "⇐", "Ü": "➔", "ª": "✦", "«": "★", "°": "✧",
    "Ö": "→", "¡": "○", "¤": "◉", "£": "▣",
}
_SYMBOL = {"·": "•", "Þ": "⇒", "Ü": "⇐", "®": "→", "¾": "—",
           "ç": "│", "Ö": "√", "×": "⋅"}


def _bullet_char(ch: str, font: str | None) -> tuple[str, bool]:
    """(знак, заменён ли шрифт). Символы из области частного использования
    `F0xx` — это те же коды Wingdings со сдвигом."""
    low = (font or "").lower()
    if ch and 0xF020 <= ord(ch[0]) <= 0xF0FF:
        ch = chr(ord(ch[0]) - 0xF000) + ch[1:]
    if low.startswith("wingdings") or low == "webdings":
        return _WINGDINGS.get(ch, "•"), True
    if low == "symbol":
        return _SYMBOL.get(ch, ch), True
    return ch, False


# --- автонумерация ----------------------------------------------------------------


def _roman(n: int) -> str:
    vals = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
            (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


def _alpha(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(97 + r) + s
    return s


def autonum(kind: str, n: int) -> str:
    if kind.startswith("alphaLc"):
        body = _alpha(n)
    elif kind.startswith("alphaUc"):
        body = _alpha(n).upper()
    elif kind.startswith("romanLc"):
        body = _roman(n)
    elif kind.startswith("romanUc"):
        body = _roman(n).upper()
    else:
        body = str(n)
    if kind.endswith("ParenBoth"):
        return f"({body})"
    if kind.endswith("ParenR"):
        return f"{body})"
    if kind.endswith("Plain"):
        return body
    if kind.endswith("Minus"):
        return f"- {body} -"
    return f"{body}."


# --- разрешение свойств ---------------------------------------------------------------


def _attr(chain: list[ET.Element], name: str) -> str | None:
    for el in chain:
        v = el.get(name)
        if v is not None:
            return v
    return None


def _child(chain: list[ET.Element], *tags: str) -> ET.Element | None:
    qs = tuple(qn(t) for t in tags)
    for el in chain:
        for c in el:
            if c.tag in qs:
                return c
    return None


def _flag(v: str | None) -> bool:
    return v in ("1", "true", "on")


@dataclass
class _Run:
    size: float           # пункты, уже со шкалой автоподбора
    face: str | None
    style: str            # объявления CSS
    color: Color | None


class _Writer:
    """Один текстовый блок: общий счётчик нумерации и параметры автоподбора."""

    def __init__(self, ctx: Ctx, styles: TextStyles, scale: float = 1.0, ln_red: float = 0.0,
                 spc_first_last: bool = True) -> None:
        self.ctx = ctx
        self.styles = styles
        self.scale = scale
        self.ln_red = ln_red
        self.spc_first_last = spc_first_last
        self.counters: dict[int, tuple[str, int]] = {}

    # -- прогон --

    def _rchain(self, rpr: ET.Element | None, levels: list[ET.Element]) -> list[ET.Element]:
        chain = [rpr] if rpr is not None else []
        for lv in levels:
            d = lv.find(qn("a:defRPr"))
            if d is not None:
                chain.append(d)
        return chain

    def _face(self, chain: list[ET.Element], text: str) -> str | None:
        """Гарнитура прогона. Для кириллицы PowerPoint берёт слот `a:latin`;
        токены темы разрешаются, и если у темы есть гарнитура для `Cyrl`, для
        русского текста действует она (`DOM-TEXT §3`)."""
        el = _child(chain, "a:latin")
        token = el.get("typeface") if el is not None else None
        if not token:
            token = "+mn-lt"
        theme = self.ctx.theme
        if token.startswith("+"):
            if theme is None:
                return None
            if _CYRILLIC.search(text or ""):
                cyr = theme.script_face(token, "Cyrl")
                if cyr:
                    return cyr
            return theme.typeface(token)
        return token

    def run(self, rpr: ET.Element | None, levels: list[ET.Element], text: str,
            link: bool = False) -> _Run:
        chain = self._rchain(rpr, levels)
        ctx = self.ctx
        size = integer(_attr(chain, "sz"), 1800) / 100 * self.scale
        face = self._face(chain, text)
        # Гарнитуру ставит абзац; прогону — только если у него своя.
        parts = [f"font-size:{ctx.doc.cq_pt(size)}"]
        if _flag(_attr(chain, "b")):
            parts.append("font-weight:700")
        if _flag(_attr(chain, "i")):
            parts.append("font-style:italic")
        fill_el = _child(chain, *[f"a:{t}" for t in FILL_TAGS])
        col: Color | None
        if link:
            col = self._scheme("hlink")
        elif fill_el is not None:
            f = fill_of(fill_el, ctx)
            col = ("#000000", 0.0) if f is not None and f.kind == "none" else first_color(f)
            if f is not None and f.kind == "grad":
                ctx.doc.note("gradient text")
        else:
            col = self._scheme("tx1")
        parts.append(f"color:{css(col or ('#000000', 1.0))}")
        deco = []
        u = _attr(chain, "u")
        if link or (u and u != "none"):
            deco.append("underline")
        strike = _attr(chain, "strike")
        if strike and strike != "noStrike":
            deco.append("line-through")
        if deco:
            line = " ".join(deco)
            if u == "dbl":
                line += " double"
            elif u and u.startswith("wavy"):
                line += " wavy"
            elif u and u.startswith("dotted"):
                line += " dotted"
            elif u and u.startswith("dash"):
                line += " dashed"
            parts.append(f"text-decoration:{line}")
        cap = _attr(chain, "cap")
        if cap == "all":
            parts.append("text-transform:uppercase")
        elif cap == "small":
            parts.append("font-variant:small-caps")
        spc = integer(_attr(chain, "spc"), 0)
        if spc:
            parts.append(f"letter-spacing:{ctx.doc.cq_pt(spc / 100)}")
        base = pct(_attr(chain, "baseline"), 0.0)
        if base:
            # Индекс PowerPoint рисует уменьшенным; сдвиг — в долях кегля.
            parts[0] = f"font-size:{ctx.doc.cq_pt(size * 0.67)}"
            parts.append(f"vertical-align:{ctx.doc.cq_pt(size * base)}")
        hl = _child(chain, "a:highlight")
        if hl is not None:
            hc = child_color(hl, ctx)
            if hc:
                parts.append(f"background:{css(hc)}")
        return _Run(size=size, face=face, style=";".join(parts), color=col)

    def _scheme(self, role: str) -> Color | None:
        el = ET.Element(qn("a:schemeClr"), {"val": role})
        return child_color(_wrap(el), self.ctx)

    # -- ссылки --

    def _href(self, rpr: ET.Element | None) -> str | None:
        if rpr is None:
            return None
        h = rpr.find(qn("a:hlinkClick"))
        if h is None:
            return None
        rid = h.get(qn("r:id"))
        if not rid:
            return None
        rel = self.ctx.doc.pkg.rels(self.ctx.part).get(rid)
        if rel is None:
            return None
        if rel.external:
            target = rel.target.strip()
            if re.match(r"(?i)^(https?:|mailto:)", target):
                return target
            return None
        if rel.type == RT.SLIDE and rel.target_part in self.ctx.doc.slide_ids:
            return f"#slide-{self.ctx.doc.slide_ids[rel.target_part]}"
        return None

    # -- абзац --

    def paragraph(self, p: ET.Element, first: bool, last: bool) -> str:
        ctx = self.ctx
        ppr = p.find(qn("a:pPr"))
        lvl = min(max(integer(ppr.get("lvl") if ppr is not None else None, 0), 0), 8)
        levels = self.styles.levels(lvl)
        pchain = ([ppr] if ppr is not None else []) + levels

        items: list[tuple[_Run, str, str | None] | None] = []   # None — разрыв строки
        runs: list[_Run] = []
        has_text = False
        for child in _inline(p):
            tag = local_name(child.tag)
            if tag in ("r", "fld"):
                t = child.find(qn("a:t"))
                text = (t.text or "") if t is not None else ""
                if tag == "fld" and (child.get("type") or "") == "slidenum":
                    text = str(ctx.slide_no)
                if not text:
                    continue
                has_text = True
                rpr = child.find(qn("a:rPr"))
                href = self._href(rpr)
                run = self.run(rpr, levels, text, link=href is not None)
                runs.append(run)
                if ctx.count_text:
                    ctx.doc.text_chars += len(text)
                items.append((run, _escape(text), href))
            elif tag == "br":
                items.append(None)

        pieces: list[str] = []
        p_face = runs[0].face if runs else None
        for item in items:
            if item is None:
                pieces.append("<br>")
                continue
            run, body, href = item
            decl = run.style if run.face == p_face else (
                f"font-family:{font_stack(run.face)};{run.style}")
            if href is not None:
                pieces.append(f'<a href="{html.escape(href, quote=True)}" style="{decl}">{body}</a>')
            else:
                pieces.append(f'<span style="{decl}">{body}</span>')

        end = p.find(qn("a:endParaRPr"))
        # Кегль абзаца задаёт «подпорку» каждой строки. Берём наименьший:
        # строка из 24 пт после строки из 54 пт (через a:br) иначе получала
        # высоту 54-пунктовой. Высоту строки дают её собственные прогоны.
        if runs:
            size = min(r.size for r in runs)
            lead = runs[0].size
            face = runs[0].face
        else:
            end_run = self.run(end, levels, "")
            size, face = end_run.size, end_run.face
            lead = size

        style = []
        algn = _attr(pchain, "algn") or "l"
        style.append("text-align:" + {"ctr": "center", "r": "right", "just": "justify",
                                      "dist": "justify", "justLow": "justify",
                                      "thaiDist": "justify"}.get(algn, "left"))
        if _flag(_attr(pchain, "rtl")):
            style.append("direction:rtl")
        mar_l = integer(_attr(pchain, "marL"), 0)
        indent = integer(_attr(pchain, "indent"), 0)
        mar_r = integer(_attr(pchain, "marR"), 0)
        if mar_l:
            style.append(f"padding-left:{ctx.doc.cq(mar_l)}")
        if mar_r:
            style.append(f"padding-right:{ctx.doc.cq(mar_r)}")
        if indent:
            style.append(f"text-indent:{ctx.doc.cq(indent)}")
        # Гарнитура абзаца — та же, что у прогона: «подпорка» строки берётся
        # из шрифта абзаца, и чужая метрика раздувала межстрочный шаг.
        style.append(f"font-family:{font_stack(face)}")
        style.append(f"font-size:{ctx.doc.cq_pt(size)}")

        factor = LINE_FACTOR
        ln = _child(pchain, "a:lnSpc")
        ln_pct = ln.find(qn("a:spcPct")) if ln is not None else None
        ln_pts = ln.find(qn("a:spcPts")) if ln is not None else None
        if ln_pts is not None:
            val = integer(ln_pts.get("val"), 1200) / 100 * (1 - self.ln_red)
            style.append(f"line-height:{ctx.doc.cq_pt(val)}")
            pitch = val / lead if lead else 1.0
        else:
            val = pct(ln_pct.get("val"), 1.0) if ln_pct is not None else 1.0
            val = max(val - self.ln_red, 0.1)
            style.append(f"line-height:{num(val * factor, 4)}")
            pitch = val * factor
        # Где в строке базовая линия. PowerPoint сжимает (растягивает) строку
        # целиком: базовая линия делит шаг в пропорции верхнего и нижнего
        # выноса шрифта. CSS же ставит содержимое по центру строки. При
        # интервале 28% у кегля 96 пт разница — половина высоты цифры; формулу
        # подтвердили замеры по рендерам PowerPoint для 16%, 28%, 90% и 100%.
        shift = (pitch * (_ASC / (_ASC + _DESC) - 0.5) - (_ASC - _DESC) / 2) * lead
        if abs(shift) > 0.01:
            style.append(f"position:relative;top:{ctx.doc.cq_pt(shift)}")
        for tag, prop, skip in (("a:spcBef", "margin-top", first), ("a:spcAft", "margin-bottom", last)):
            if skip and not self.spc_first_last:
                continue
            el = _child(pchain, tag)
            if el is None:
                continue
            sp_pts = el.find(qn("a:spcPts"))
            sp_pct = el.find(qn("a:spcPct"))
            if sp_pts is not None and integer(sp_pts.get("val")):
                style.append(f"{prop}:{ctx.doc.cq_pt(integer(sp_pts.get('val')) / 100)}")
            elif sp_pct is not None and pct(sp_pct.get("val"), 0.0):
                # Доля строки первого прогона — в абсолютных единицах, а не em:
                # кегль абзаца теперь наименьший, а не ведущий.
                gap = pct(sp_pct.get("val"), 0.0) * factor * lead
                style.append(f"{prop}:{ctx.doc.cq_pt(gap)}")

        bullet = self._bullet(pchain, lvl, runs, has_text, indent)
        if not pieces or not has_text:
            pieces = ["<br>"]
        return f'<p style="{";".join(style)}">{bullet}{"".join(pieces)}</p>'

    def _bullet(self, pchain: list[ET.Element], lvl: int, runs: list[_Run], has_text: bool,
                indent: int) -> str:
        ctx = self.ctx
        kind = _child(pchain, "a:buNone", "a:buChar", "a:buAutoNum", "a:buBlip")
        tag = local_name(kind.tag) if kind is not None else "buNone"
        if not has_text:
            return ""
        for deeper in [k for k in self.counters if k > lvl]:
            del self.counters[deeper]
        if tag == "buNone":
            self.counters.pop(lvl, None)
            return ""
        if tag == "buBlip":
            ctx.doc.note("picture bullet")
            self.counters.pop(lvl, None)
            return ""
        first = runs[0] if runs else None
        font_el = _child(pchain, "a:buFont", "a:buFontTx")
        font = None
        if font_el is not None and local_name(font_el.tag) == "buFont":
            font = font_el.get("typeface")
            if font and font.startswith("+") and ctx.theme is not None:
                font = ctx.theme.typeface(font)
        if tag == "buChar":
            text, swapped = _bullet_char(kind.get("char") or "•", font)
            if swapped:
                font = None
            self.counters.pop(lvl, None)
        else:
            num_kind = kind.get("type") or "arabicPeriod"
            start = integer(kind.get("startAt"), 1)
            prev = self.counters.get(lvl)
            n = prev[1] + 1 if prev and prev[0] == num_kind else start
            self.counters[lvl] = (num_kind, n)
            text = autonum(num_kind, n)
        base = first.size if first else 18.0
        sz_el = _child(pchain, "a:buSzPct", "a:buSzPts", "a:buSzTx")
        size = base
        if sz_el is not None and local_name(sz_el.tag) == "buSzPct":
            size = base * pct(sz_el.get("val"), 1.0)
        elif sz_el is not None and local_name(sz_el.tag) == "buSzPts":
            size = integer(sz_el.get("val"), 1800) / 100 * self.scale
        clr_el = _child(pchain, "a:buClr", "a:buClrTx")
        col = first.color if first else None
        if clr_el is not None and local_name(clr_el.tag) == "buClr":
            col = child_color(clr_el, ctx) or col
        style = [f"font-size:{ctx.doc.cq_pt(size)}", f"color:{css(col or ('#000000', 1.0))}",
                 f"font-family:{font_stack(font or (first.face if first else None))}"]
        if indent < 0:
            style.append(f"min-width:{ctx.doc.cq(-indent)}")
        else:
            style.append("margin-right:.4em")
        return f'<span class="bu" style="{";".join(style)}">{_escape(text)}</span>'


def _inline(p: ET.Element):
    """Дети абзаца по порядку; `mc:AlternateContent` (формула `a14:m` и
    подобное) раскрывается в запасную ветку — там прогоны с тем же текстом."""
    for child in p:
        if local_name(child.tag) == "AlternateContent":
            fallback = next((b for b in child if local_name(b.tag) == "Fallback"), None)
            if fallback is not None:
                yield from fallback
            continue
        yield child


def _wrap(color_el: ET.Element) -> ET.Element:
    holder = ET.Element(qn("a:solidFill"))
    holder.append(color_el)
    return holder


def _escape(text: str) -> str:
    """Текст прогона в HTML. Управляющие символы HTML не пропускает, а
    вертикальная табуляция у PowerPoint — мягкий перенос строки."""
    text = re.sub(r"[\x00-\x08\x0c\x0e-\x1f]", "", text)
    out = html.escape(text, quote=False)
    return re.sub(r"\r\n|[\r\n\x0b]", "<br>", out)


def has_text(body: ET.Element | None) -> bool:
    if body is None:
        return False
    for t in body.iter(qn("a:t")):
        if t.text:
            return True
    return False


def paragraphs(body: ET.Element, ctx: Ctx, styles: TextStyles, scale: float = 1.0,
               ln_red: float = 0.0, spc_first_last: bool = True) -> str:
    """Абзацы тела текста подряд — для фигуры и для ячейки таблицы."""
    writer = _Writer(ctx, styles, scale, ln_red, spc_first_last)
    items = body.findall(qn("a:p"))
    return "".join(writer.paragraph(p, i == 0, i == len(items) - 1) for i, p in enumerate(items))


# --- текстовый слой фигуры -------------------------------------------------------


_ANCHOR = {"t": "flex-start", "ctr": "center", "b": "flex-end", "just": "center",
           "dist": "center"}
_VERT = {"vert": 90.0, "eaVert": 90.0, "wordArtVert": 90.0, "mongolianVert": 90.0,
         "wordArtVertRtl": 90.0, "vert270": 270.0}


def body_layer(body: ET.Element, ctx: Ctx, styles: TextStyles, body_chain: list[ET.Element],
               w: float, h: float, text_rect: tuple[float, float, float, float] | None,
               upside_down: bool = False) -> str:
    """Текстовый слой фигуры w×h (EMU) или пустая строка, если текста нет.

    `body_chain` — `a:bodyPr` фигуры и её плейсхолдеров макета и мастера:
    поля, привязка и перенос наследуются так же, как стили текста. Шкала
    автоподбора берётся только своя — её посчитал PowerPoint для этого текста.
    """
    if not has_text(body):
        return ""
    own = body.find(qn("a:bodyPr"))
    doc = ctx.doc

    def bp(name: str) -> str | None:
        return _attr(body_chain, name)

    l_ins = integer(bp("lIns"), 91440)
    t_ins = integer(bp("tIns"), 45720)
    r_ins = integer(bp("rIns"), 91440)
    b_ins = integer(bp("bIns"), 45720)
    scale, ln_red = 1.0, 0.0
    fit = own.find(qn("a:normAutofit")) if own is not None else None
    if fit is not None:
        scale = pct(fit.get("fontScale"), 1.0)
        ln_red = pct(fit.get("lnSpcReduction"), 0.0)

    x0, y0, x1, y1 = text_rect if text_rect else (0.0, 0.0, w, h)
    x0, y0, x1, y1 = x0 + l_ins, y0 + t_ins, x1 - r_ins, y1 - b_ins
    tw, th = max(x1 - x0, 0.0), max(y1 - y0, 0.0)

    rot = 0.0
    vert = bp("vert") or "horz"
    if vert in _VERT:
        rot += _VERT[vert]
        doc.note("vertical text")
    rot += integer(bp("rot"), 0) / 60000
    if upside_down:
        rot += 180.0
    if rot % 180 == 90:
        cx, cy = x0 + tw / 2, y0 + th / 2
        tw, th = th, tw
        x0, y0 = cx - tw / 2, cy - th / 2

    warp = own.find(qn("a:prstTxWarp")) if own is not None else None
    if warp is not None and (warp.get("prst") or "textNoShape") != "textNoShape":
        doc.note(f"text warp {warp.get('prst')}")

    wrap_none = bp("wrap") == "none"
    anchor = bp("anchor") or "t"
    style = [f"left:{doc.cq(x0)}", f"top:{doc.cq(y0)}", f"width:{doc.cq(tw)}",
             f"height:{doc.cq(th)}", f"justify-content:{_ANCHOR.get(anchor, 'flex-start')}"]
    first_p = body.find(qn("a:p"))
    if wrap_none or _flag(bp("anchorCtr")):
        algn = None
        if first_p is not None:
            ppr = first_p.find(qn("a:pPr"))
            lvl = integer(ppr.get("lvl") if ppr is not None else None, 0)
            algn = _attr(([ppr] if ppr is not None else []) + styles.levels(lvl), "algn")
        style.append("align-items:" + ({"ctr": "center", "r": "flex-end"}.get(algn or "l", "flex-start")
                                       if not _flag(bp("anchorCtr")) else "center"))
    if wrap_none:
        style.append("white-space:pre")
    tab = integer(_attr(styles.levels(0), "defTabSz"), 914400)
    style.append(f"tab-size:{doc.cq(tab)}")
    if rot % 360:
        style.append(f"transform:rotate({num(rot % 360, 2)}deg)")
    inner = paragraphs(body, ctx, styles, scale, ln_red, _flag(bp("spcFirstLastPara")))
    cols = integer(bp("numCol"), 1)
    if cols > 1:
        gap = integer(bp("spcCol"), 0)
        inner = (f'<div style="column-count:{cols};column-gap:{doc.cq(gap)};column-fill:auto;'
                 f'height:100%">{inner}</div>')
    cls = "t nw" if wrap_none else "t"
    return f'<div class="{cls}" style="{";".join(style)}">{inner}</div>'
