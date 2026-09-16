"""Вывод дизайн-системы из наблюдений. DOM-COLOR §6, DOM-TEXT §7, DOM-GEOM §6.

Всё здесь — статистика по реальным слайдам, а не чтение объявленного в мастере.
Причина в DOM-TEXT §7: мастер и слайды в живых шаблонах расходятся, а эксперт
смотрит на слайды.

Два отбора фигур, а не один (`DOM-GEOM §7`):

* **содержательные** — несут текст или видимое оформление. По ним считается
  палитра, типографика и словарь форм.
* **позиционированные** — просто стоят на холсте, включая пустые плейсхолдеры.
  По ним выводится сетка: пустая рамка заголовка тоже говорит, где заголовок.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..model import (
    DesignSystem,
    Evidence,
    Grid,
    ObservedColor,
    ShapeToken,
    SlideSize,
    SourceInfo,
    ThemeColor,
    TypeRole,
    Unhandled,
)
from ..oxml.units import GRID_QUANT_EMU, aspect_ratio, quantize
from .color import THEME_ROLES
from .deck import Deck, Rect
from .shapes import CHROME_PH, RunObs, ShapeObs, SlideObs

#: Имена ролей типографической шкалы выше и ниже основного текста.
_ABOVE_BODY = ("display", "title", "subtitle", "lead")
_BELOW_BODY = ("caption", "label", "footnote")

#: Сколько типографических ролей и форм выносим в артефакт. Хвост из
#: единичных начертаний — это не шкала, а случайности вёрстки.
_MAX_TYPE_ROLES = 16
_MAX_SHAPES = 24
_MAX_CORE_COLORS = 24

#: Кандидаты в число колонок. Дробить холст на 5, 7 или 9 никто не станет.
_COLUMN_CANDIDATES = (1, 2, 3, 4, 6, 12)

#: Высота «обычного» холста в пунктах (7.5 дюйма). К ней нормируются кегли:
#: холсты бывают вдвое больше, и абсолютные пункты между шаблонами несравнимы.
#: DOM-GEOM §8.
_REFERENCE_CANVAS_PT = 540.0

#: Текст, состоящий только из цифр и обрамления. Считается по ФИГУРАМ, а не по
#: прогонам: PowerPoint режет одно визуальное число на несколько прогонов, и на
#: уровне прогона признак не работает. Замерено на трёх шаблонах — настоящая
#: метрика даёт 100% по фигурам при 50% по прогонам, ложная 0% при тех же 50%.
#: DOM-TEXT §8.
_NUMERICISH = re.compile(r"^[\s\d.,%+\-–—×xX/№$€₽]+$")
_METRIC_NUMERIC_SHARE = 0.8
_METRIC_MAX_LEN = 12

#: Ниже этой средней длины прогона кластер — набор заголовков, а не основной
#: текст. Замерено: у настоящего body средняя длина 55-108 символов, у ложного 9.
_BODY_MIN_AVG_LEN = 15

#: Плейсхолдер заголовка бывает занят мелким текстом. Повышаем до title только
#: при заметном отрыве от основного текста.
_TITLE_MIN_RATIO = 1.3

#: Сколько самых крупных начертаний удерживать в шкале независимо от частоты.
#: Display-кегль по своей природе встречается один-два раза за колоду, и отбор
#: по частоте выбрасывает именно его.
_KEEP_LARGEST = 3


# --- отбор фигур -------------------------------------------------------


def _is_positioned(shape: ShapeObs, cx: int, cy: int) -> bool:
    """Фигура реально стоит на холсте. Основа для вывода сетки."""
    if shape.hidden or shape.rect is None or shape.rotated:
        return False
    r = shape.rect
    if r.cx <= 0 or r.cy <= 0:
        return False
    if shape.ph_type in CHROME_PH:
        return False  # колонтитулы есть везде и портят статистику полей
    if r.right <= 0 or r.bottom <= 0 or r.x >= cx or r.y >= cy:
        return False  # целиком за холстом — «склад» заготовок
    return True


def _is_content(shape: ShapeObs, cx: int, cy: int) -> bool:
    """Фигура что-то показывает: текст, заливку или обводку."""
    if not _is_positioned(shape, cx, cy):
        return False
    return shape.has_text or shape.fill_kind not in (None, "none") or shape.line is not None


def _select(slides: list[SlideObs], cx: int, cy: int, predicate) -> list[ShapeObs]:
    return [s for slide in slides for s in slide.shapes if predicate(s, cx, cy)]


# --- палитра -----------------------------------------------------------


def _theme_palette(deck: Deck) -> tuple[ThemeColor, ...]:
    out: list[ThemeColor] = []
    for master_part in sorted(deck.masters):
        master = deck.masters[master_part]
        theme = deck.themes.get(master.theme_part or "")
        if theme is None:
            continue
        for role in THEME_ROLES:
            hexval = theme.scheme.get(role)
            if hexval:
                out.append(ThemeColor(role=role, hex=hexval, master=master_part))
    return tuple(out)


def _observed_palette(
    slides: list[SlideObs], content: list[ShapeObs], canvas_area: int
) -> tuple[tuple[ObservedColor, ...], tuple[str, ...]]:
    """Возвращает (полный список, ядро палитры).

    Полный список честен, но на иллюстративных шаблонах в нём сотни цветов из
    векторной графики. Ядро — то, что действительно является дизайн-системой.
    """
    counts: Counter[str] = Counter()
    alphas: dict[str, float] = {}
    roles: dict[str, str | None] = {}
    contexts: defaultdict[str, set[str]] = defaultdict(set)
    on_slides: defaultdict[str, set[int]] = defaultdict(set)
    max_area: defaultdict[str, int] = defaultdict(int)

    def note(color, context: str, slide_index: int, area: int = 0) -> None:
        if color is None:
            return
        counts[color.hex] += 1
        alphas.setdefault(color.hex, color.alpha)
        if color.theme_role and not roles.get(color.hex):
            roles[color.hex] = color.theme_role
        roles.setdefault(color.hex, None)
        contexts[color.hex].add(context)
        on_slides[color.hex].add(slide_index)
        if area > max_area[color.hex]:
            max_area[color.hex] = area

    for slide in slides:
        note(slide.background, "background", slide.index, canvas_area)
    for shape in content:
        area = shape.rect.area if shape.rect else 0
        note(shape.fill, "gradient" if shape.fill_kind == "gradient" else "fill",
             shape.slide_index, area)
        note(shape.line, "line", shape.slide_index)
        for run in shape.runs:
            if run.color_hex:
                counts[run.color_hex] += 1
                alphas.setdefault(run.color_hex, 1.0)
                roles.setdefault(run.color_hex, None)
                contexts[run.color_hex].add("text")
                on_slides[run.color_hex].add(shape.slide_index)

    # Сортировка по убыванию частоты, затем по hex — детерминированность.
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    observed = tuple(
        ObservedColor(
            hex=hexval,
            alpha=alphas.get(hexval, 1.0),
            count=count,
            theme_role=roles.get(hexval),
            contexts=tuple(sorted(contexts[hexval])),
        )
        for hexval, count in ordered
    )

    def significant(hexval: str) -> bool:
        return (
            roles.get(hexval) is not None            # восходит к роли темы
            or len(on_slides[hexval]) >= 2           # повторяется между слайдами
            or "text" in contexts[hexval]            # им набран текст
            or "background" in contexts[hexval]
            or max_area[hexval] >= canvas_area // 50  # занимает заметную площадь
        )

    core = tuple(c.hex for c in observed if significant(c.hex))[:_MAX_CORE_COLORS]
    return observed, core


# --- типографика -------------------------------------------------------


def is_numeric_text(text: str) -> bool:
    """Текст целиком является числом. Считается по тексту ФИГУРЫ. DOM-TEXT §8."""
    t = text.strip()
    return bool(t) and len(t) <= _METRIC_MAX_LEN and bool(_NUMERICISH.match(t)) and any(
        c.isdigit() for c in t
    )


def _numeric_share(texts: list[str]) -> float:
    """Доля текстов, которые целиком являются числом.

    На вход подаётся текст, собранный ПО ФИГУРАМ: «89,526,124» и «$» — это два
    прогона одного визуального числа, и по отдельности каждый второй из них
    признак проваливает. DOM-TEXT §8.
    """
    if not texts:
        return 0.0
    return sum(1 for t in texts if is_numeric_text(t)) / len(texts)


def _type_scale(content: list[ShapeObs], canvas_cy: int) -> tuple[tuple[TypeRole, ...], int]:
    """Кластеризация прогонов в типографическую шкалу.

    Цвет намеренно не входит в ключ: один и тот же стиль, набранный в пяти
    цветах, — это одна роль, а не пять. Кегль округляется до половины пункта,
    иначе `normAutofit` разносит 17.9 и 18.0 по разным кластерам.
    """
    buckets: defaultdict[tuple, list[RunObs]] = defaultdict(list)
    shape_texts: defaultdict[tuple, list[str]] = defaultdict(list)
    for shape in content:
        in_shape: defaultdict[tuple, list[RunObs]] = defaultdict(list)
        for run in shape.runs:
            if run.size_pt:
                key = (
                    run.latin or "",
                    round(run.size_pt * 2) / 2,
                    run.bold,
                    run.italic,
                    run.caps or "none",
                )
                in_shape[key].append(run)
        for key, runs in in_shape.items():
            buckets[key].extend(runs)
            shape_texts[key].append("".join(r.text for r in runs).strip())
    if not buckets:
        return (), 0

    clusters = []
    for key, runs in buckets.items():
        seen: list[str] = []
        for r in runs:
            text = r.text.strip()
            if text and text not in seen:
                seen.append(text[:60])
            if len(seen) == 3:
                break
        colors = Counter(r.color_hex for r in runs if r.color_hex)
        aligns = Counter(r.align for r in runs if r.align)
        chars = sum(len(r.text) for r in runs)
        clusters.append(
            {
                "key": key,
                "size": key[1],
                "runs": len(runs),
                "chars": chars,
                "avg_len": chars / len(runs),
                "numeric": _numeric_share(shape_texts[key]),
                "first": runs[0],
                "examples": tuple(seen),
                "color": colors.most_common(1)[0][0] if colors else None,
                "align": aligns.most_common(1)[0][0] if aligns else None,
                "in_title_ph": any(r.in_placeholder in ("title", "ctrTitle") for r in runs),
            }
        )

    # Отбор в два приёма. Сначала безусловно берём самые крупные начертания:
    # display-кегль редок по определению, и отбор по частоте выбрасывает именно
    # его. Затем добираем самыми частыми — это и есть рабочая часть шкалы.
    largest = sorted(clusters, key=lambda c: (-c["size"], -c["runs"]))[:_KEEP_LARGEST]
    frequent = sorted(
        (c for c in clusters if c["runs"] >= 2), key=lambda c: (-c["runs"], -c["size"])
    )
    kept, seen = [], set()
    for c in largest + frequent + sorted(clusters, key=lambda c: -c["size"]):
        if id(c) not in seen:
            seen.add(id(c))
            kept.append(c)
        if len(kept) >= _MAX_TYPE_ROLES:
            break
    dropped = len(clusters) - len(kept)
    if not kept:
        return (), dropped

    # Основной текст определяется объёмом набранного, а не числом прогонов:
    # у шаблона с множеством коротких заголовков прогонов больше именно у них.
    prose = [c for c in kept if c["avg_len"] >= _BODY_MIN_AVG_LEN]
    body = max(prose or kept, key=lambda c: (c["chars"], -c["size"]))
    body_size = body["size"]

    above = sorted((c for c in kept if c["size"] > body_size), key=lambda c: -c["size"])
    below = sorted((c for c in kept if c["size"] < body_size), key=lambda c: -c["size"])

    roles: dict[int, str] = {id(body): "body"}
    for i, c in enumerate(above):
        roles[id(c)] = _ABOVE_BODY[i] if i < len(_ABOVE_BODY) else "other"
    for i, c in enumerate(below):
        roles[id(c)] = _BELOW_BODY[i] if i < len(_BELOW_BODY) else "other"
    for c in kept:
        roles.setdefault(id(c), "other")

    # Уточнения поверх ранга по кеглю.
    for c in kept:
        if roles[id(c)] == "body":
            continue
        if c["size"] >= body_size * 2 and c["numeric"] >= _METRIC_NUMERIC_SHARE:
            roles[id(c)] = "metric"
        elif c["key"][3] and c["size"] > body_size:  # курсив крупнее тела
            roles[id(c)] = "quote"
        elif (
            c["in_title_ph"]
            and c["size"] >= body_size * _TITLE_MIN_RATIO
            and roles[id(c)] == "other"
        ):
            roles[id(c)] = "title"

    canvas_pt = max(1.0, canvas_cy / 12700.0)
    out = []
    for n, c in enumerate(sorted(kept, key=lambda c: (-c["size"], -c["runs"], c["key"])), 1):
        first: RunObs = c["first"]
        out.append(
            TypeRole(
                id=f"t{n:02d}",
                role=roles[id(c)],
                latin=first.latin,
                cyrl=first.cyrl,
                size_pt=c["size"],
                size_norm=round(c["size"] * _REFERENCE_CANVAS_PT / canvas_pt, 1),
                bold=first.bold,
                italic=first.italic,
                caps=first.caps,
                spacing_pt=first.spacing_pt,
                line_spacing_pct=first.line_spacing_pct,
                color_hex=c["color"],
                align=c["align"],
                count=c["runs"],
                examples=c["examples"],
            )
        )
    return tuple(out), dropped


# --- сетка -------------------------------------------------------------


def _margin(values: list[int]) -> int:
    """Поле — это самый левый ПОВТОРЯЮЩИЙСЯ край, а не самый частый.

    Мода находит самую населённую колонку. На текстовом деске она совпадает с
    полем, на дизайнерском — нет: замерено 10.56 дюйма «поля» на холсте 26.66,
    потому что большинство блоков стояло во второй половине слайда. Требование
    повторяемости отсекает единичные вылеты, минимум даёт настоящий край.
    """
    if not values:
        return 0
    counts = Counter(values)
    recurring = [v for v, c in counts.items() if c >= 2]
    return min(recurring or list(counts))


def _mode(values: list[int]) -> int | None:
    """Мода с детерминированным разрешением ничьей — меньшее значение."""
    if not values:
        return None
    counts = Counter(values)
    best = max(counts.values())
    return min(v for v, c in counts.items() if c == best)


def _columns(lefts: list[int], margin_left: int, usable_width: int) -> int | None:
    """Число колонок подбором из кандидатов.

    Считать число кластеров левых краёв нельзя: у иллюстративного шаблона их
    десятки. Вместо этого проверяем, какая сетка объясняет наблюдаемые края, и
    берём **наименьшую** подходящую — у большего числа колонок совпадений
    всегда не меньше, так что максимум по счёту выбрал бы всегда 12.
    """
    if not lefts or usable_width <= 0:
        return None
    for n in _COLUMN_CANDIDATES:
        step = usable_width / n
        tolerance = max(GRID_QUANT_EMU, step * 0.12)
        starts = [margin_left + i * step for i in range(n)]
        hits = sum(1 for x in lefts if any(abs(x - s) <= tolerance for s in starts))
        if hits / len(lefts) >= 0.75:
            return n
    return None


def _grid(positioned: list[ShapeObs], content: list[ShapeObs], cx: int, cy: int) -> Grid:
    rects: list[Rect] = [s.rect for s in positioned if s.rect is not None]
    if not rects:
        return Grid(0, 0, 0, 0, None, None, None, 0)

    q = GRID_QUANT_EMU
    # Поля — это безопасная зона ТЕКСТА, а не габарит графики. Плашки и фото
    # уходят под обрез намеренно, и по ним поле всегда получается нулевым;
    # текст к краю не ставят никогда. Замерено на десяти шаблонах: по всем
    # фигурам выходило 0.00, по текстовым — осмысленные 0.3-0.9 дюйма.
    text_rects = [s.rect for s in content if s.rect is not None and s.has_text]
    base = text_rects if len(text_rects) >= 4 else rects
    # Фигуры «в подложку» во всю ширину или высоту поля не определяют.
    h_rects = [r for r in base if r.cx <= cx * 0.9] or base
    v_rects = [r for r in base if r.cy <= cy * 0.9] or base

    lefts = [quantize(max(0, r.x), q) for r in h_rects]
    rights = [quantize(max(0, cx - r.right), q) for r in h_rects]
    tops = [quantize(max(0, r.y), q) for r in v_rects]
    bottoms = [quantize(max(0, cy - r.bottom), q) for r in v_rects]

    margin_left = _margin(lefts)
    margin_right = _margin(rights)
    margin_top = _margin(tops)
    margin_bottom = _margin(bottoms)

    # Колонки определяет текст, а не декор: у иллюстраций свои края.
    text_lefts = [
        quantize(max(0, s.rect.x), q) for s in content if s.rect is not None and s.has_text
    ]
    columns = _columns(text_lefts or lefts, margin_left, cx - margin_left - margin_right)

    # Межколонник: мода зазоров между соседними по X фигурами одной строки.
    gaps: list[int] = []
    by_row: defaultdict[int, list[Rect]] = defaultdict(list)
    for r in rects:
        by_row[quantize(r.y, q)].append(r)
    for row in by_row.values():
        row.sort(key=lambda r: r.x)
        for a, b in zip(row, row[1:]):
            gap = b.x - a.right
            if q <= gap <= cx // 4:
                gaps.append(quantize(gap, q))
    gutter = _mode(gaps)

    # Базовая линия: мода расстояний между соседними уровнями верхних краёв.
    levels = sorted(set(tops))
    steps = [b - a for a, b in zip(levels, levels[1:]) if b - a >= q]
    baseline = _mode(steps)

    return Grid(
        margin_left_emu=margin_left,
        margin_right_emu=margin_right,
        margin_top_emu=margin_top,
        margin_bottom_emu=margin_bottom,
        columns=columns,
        gutter_emu=gutter,
        baseline_emu=baseline,
        samples=len(rects),
    )


# --- словарь форм ------------------------------------------------------


def _shape_tokens(content: list[ShapeObs]) -> tuple[tuple[ShapeToken, ...], int]:
    counts: Counter[tuple] = Counter()
    for s in content:
        if not s.geom:
            continue
        if s.fill_kind in (None, "none") and s.line is None:
            continue
        counts[
            (
                s.geom,
                s.fill_kind,
                s.fill.hex if s.fill else None,
                s.line.hex if s.line else None,
                s.line_w_emu,
                None if s.corner_radius_pct is None else round(s.corner_radius_pct, 4),
                s.has_shadow,
            )
        ] += 1

    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], tuple(str(x) for x in kv[0])))
    kept = ordered[:_MAX_SHAPES]
    return (
        tuple(
            ShapeToken(
                geom=key[0],
                fill_kind=key[1],
                fill_hex=key[2],
                line_hex=key[3],
                line_w_emu=key[4],
                corner_radius_pct=key[5],
                has_shadow=key[6],
                count=count,
            )
            for key, count in kept
        ),
        len(ordered) - len(kept),
    )


# --- сборка ------------------------------------------------------------


def build_design_system(
    deck: Deck,
    slides: list[SlideObs],
    unhandled: list[tuple[str, str]],
    filename: str,
) -> DesignSystem:
    cx, cy = deck.slide_cx, deck.slide_cy
    canvas_area = max(1, cx * cy)
    positioned = _select(slides, cx, cy, _is_positioned)
    content = _select(slides, cx, cy, _is_content)

    shapes_total = sum(len(s.shapes) for s in slides)
    runs_total = sum(len(sh.runs) for s in slides for sh in s.shapes)

    observed, core = _observed_palette(slides, content, canvas_area)
    type_scale, type_dropped = _type_scale(content, cy)
    shape_tokens, shapes_dropped = _shape_tokens(content)

    grouped: Counter[tuple[str, str]] = Counter(unhandled)
    notes: list[str] = []
    if not slides:
        notes.append("В шаблоне нет слайдов: дизайн-система выведена только из темы.")
    if positioned and len(positioned) < 5:
        notes.append("Мало пригодных фигур — сетка и типографика выведены ненадёжно.")
    if type_dropped:
        notes.append(
            f"Отброшено {type_dropped} единичных начертаний: это случайности вёрстки, "
            f"а не типографическая шкала."
        )
    if shapes_dropped:
        notes.append(f"Отброшено {shapes_dropped} редких форм сверх предела словаря.")
    if len(observed) > len(core) * 3 and len(observed) > 30:
        notes.append(
            f"Из {len(observed)} встреченных цветов в ядро палитры попало {len(core)}: "
            f"остальные — векторная графика, а не дизайн-система."
        )

    return DesignSystem(
        source=SourceInfo(
            filename=filename,
            sha256=deck.pkg.sha256,
            masters=len(deck.masters),
            layouts=len(deck.layouts),
            slides=len(deck.slides),
        ),
        slide=SlideSize(
            cx_emu=cx, cy_emu=cy, aspect=aspect_ratio(cx, cy), type=deck.slide_size_type
        ),
        theme_palette=_theme_palette(deck),
        observed_palette=observed,
        core_palette=core,
        type_scale=type_scale,
        grid=_grid(positioned, content, cx, cy),
        shapes=shape_tokens,
        evidence=Evidence(
            slides_analyzed=len(slides),
            shapes_total=shapes_total,
            shapes_used=len(content),
            shapes_positioned=len(positioned),
            runs_total=runs_total,
            unhandled=tuple(
                Unhandled(kind=k, detail=d or None, count=c)
                for (k, d), c in sorted(grouped.items(), key=lambda kv: (-kv[1], kv[0]))
            ),
            notes=tuple(notes),
        ),
    )
