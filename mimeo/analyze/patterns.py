"""Библиотека паттернов: типовые раскладки слайдов шаблона.

Источник раскладок — реальные слайды, а не макеты (`ADR-0004`). Кластеризация
своя, без внешних зависимостей (`ADR-0007`). Когда слайдов слишком мало, есть
фолбэк на макеты (`ADR-0006`).

Порядок: сигнатура слайда → кластеры → донор в каждом кластере → назначение
паттерна по донору → слоты из фигур донора. Донор выбирается **раньше**
классификации: клонировать будем именно его, значит и метку он должен задавать.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model import Pattern, PatternLibrary, PatternSource, Slot
from ..oxml.ns import qn
from .deck import Deck, Rect
from .fitting import estimate
from .shapes import CHROME_PH, ShapeObs, SlideObs
from .tokens import is_numeric_text

#: Разрешение сигнатуры. Ячейка считается занятой, если внутрь попадает её
#: ЦЕНТР: при проверке «задета ли ячейка» три карточки в ряд покрывают ровно те
#: же ячейки, что и один широкий блок, и раскладки становятся неразличимы.
_SIG_COLS, _SIG_ROWS = 16, 10

#: Число блоков в полосе — признак различающий, а не градуальный: две колонки и
#: три карточки это разные раскладки, и «похожи на 0.19» тут ничего не значит.
#: Замерено на размеченной фикстуре: при градуальном сравнении списки, карточки
#: и две колонки сцеплялись в одну цепочку через расстояния 0.19 и 0.39.
#: Поэтому полосы сравниваются на точное совпадение, а расстоянием меряется
#: только покрытие холста.

#: Порог слияния кластеров: доля несовпадающих ячеек сигнатуры.
_MERGE_THRESHOLD = 0.40

#: Меньше этого числа слайдов — кластеризовать нечего, идём на макеты.
_MIN_SLIDES = 3

#: Текст короче этого — подпись или метка, а не абзац.
_SHORT_TEXT = 60

_TITLE_PH = {"title", "ctrTitle"}
_BODY_PH = {"body", "obj", "subTitle"}

_LAYOUT_KIND = {
    "title": "cover",
    "secHead": "section",
    "obj": "text",
    "tx": "text",
    "twoObj": "two_column",
    "twoTxTwoObj": "two_column",
    "objTx": "image_text",
    "picTx": "image_text",
    "chart": "chart",
    "tbl": "table",
    "objOnly": "text",
    "blank": "other",
    "titleOnly": "section",
    "vertTx": "text",
}


# --- сигнатура ---------------------------------------------------------


def _content_class(shape: ShapeObs) -> str:
    if shape.has_chart:
        return "chart"
    if shape.has_table:
        return "table"
    if shape.kind == "pic" or shape.fill_kind == "picture":
        return "media"
    if shape.has_text:
        return "text"
    return "decor"


def _cells(rect: Rect, cx: int, cy: int) -> set[tuple[int, int]]:
    """Ячейки, центр которых лежит внутри прямоугольника."""
    cols = [
        c for c in range(_SIG_COLS)
        if rect.x <= (c + 0.5) * cx / _SIG_COLS < rect.right
    ]
    rows = [
        r for r in range(_SIG_ROWS)
        if rect.y <= (r + 0.5) * cy / _SIG_ROWS < rect.bottom
    ]
    return {(c, r) for c in cols for r in rows}


def _band_of(rect: Rect, cy: int) -> int:
    """Горизонтальная полоса, в которой стоит центр фигуры."""
    mid = rect.y + rect.cy // 2
    return max(0, min(_SIG_ROWS - 1, mid * _SIG_ROWS // max(1, cy)))


def _shapes_of(slide: SlideObs, cx: int, cy: int) -> list[ShapeObs]:
    """Фигуры, определяющие раскладку. Колонтитулы и служебное — мимо."""
    out = []
    for s in slide.shapes:
        if s.hidden or s.rect is None or s.ph_type in CHROME_PH:
            continue
        if s.rect.cx <= 0 or s.rect.cy <= 0:
            continue
        if s.rect.right <= 0 or s.rect.bottom <= 0 or s.rect.x >= cx or s.rect.y >= cy:
            continue
        if _content_class(s) == "decor" and s.rect.area < cx * cy // 200:
            continue  # мелкий декор раскладку не определяет
        out.append(s)
    return sorted(out, key=lambda s: (s.rect.y, s.rect.x, s.shape_id))


@dataclass(frozen=True)
class Signature:
    """Что именно сравнивается при кластеризации.

    `cells` — чем занят холст, `bands` — на сколько частей разбита каждая
    горизонтальная полоса. Без второго «три карточки» и «один блок»
    неразличимы: они занимают одни и те же ячейки.
    """

    cells: frozenset[tuple[str, int, int]]
    bands: tuple[int, ...]

    @property
    def split(self) -> tuple[int, ...]:
        """Дробление без привязки к вертикали: одна и та же раскладка,
        сдвинутая на полосу вниз, остаётся той же раскладкой."""
        return tuple(sorted(n for n in self.bands if n))

    def __bool__(self) -> bool:
        return bool(self.cells)


def _signature(shapes: list[ShapeObs], cx: int, cy: int) -> Signature:
    marks: set[tuple[str, int, int]] = set()
    bands = [0] * _SIG_ROWS
    for s in shapes:
        if s.rect is None:
            continue
        kind = _content_class(s)
        for col, row in _cells(s.rect, cx, cy):
            marks.add((kind, col, row))
        bands[_band_of(s.rect, cy)] += 1
    return Signature(cells=frozenset(marks), bands=tuple(min(n, 4) for n in bands))


def _distance(a: Signature, b: Signature) -> float:
    if a.split != b.split:
        return 1.0  # разное дробление полос — разные раскладки
    union = len(a.cells | b.cells)
    return 1.0 - len(a.cells & b.cells) / union if union else 0.0


# --- кластеризация -----------------------------------------------------


def _cluster_subset(
    signatures: list[Signature], indices: list[int], threshold: float
) -> list[list[int]]:
    """Кластеризует подмножество, сохраняя исходные индексы."""
    if not indices:
        return []
    sub = [signatures[i] for i in indices]
    return [[indices[k] for k in group] for group in _cluster(sub, threshold)]


def _cluster(signatures: list[Signature], threshold: float) -> list[list[int]]:
    """Агломеративная кластеризация со средней связью.

    Своя, а не из sklearn: стадия ANALYZE не имеет внешних зависимостей
    (`ADR-0001`, `ADR-0007`). Слайдов меньше сотни, квадратичная сложность роли
    не играет. Порядок обхода фиксирован, значит результат детерминирован.
    """
    n = len(signatures)
    if n <= 1:
        return [[i] for i in range(n)]

    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = _distance(signatures[i], signatures[j])
            dist[i][j] = dist[j][i] = d

    groups = [[i] for i in range(n)]
    while len(groups) > 1:
        best: tuple[float, int, int] | None = None
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                pairs = [dist[i][j] for i in groups[a] for j in groups[b]]
                d = sum(pairs) / len(pairs)
                if best is None or d < best[0]:
                    best = (d, a, b)
        if best is None or best[0] > threshold:
            break
        _, a, b = best
        groups[a] = sorted(groups[a] + groups[b])
        del groups[b]
    return sorted(groups, key=lambda g: g[0])


def _donor(members: list[int], per_slide: dict[int, list[ShapeObs]],
           signatures: list[Signature]) -> tuple[int, str]:
    """Самый типичный слайд кластера, а не первый попавшийся. `ADR-0004`."""
    if len(members) == 1:
        return members[0], "единственный слайд кластера"

    def typicality(i: int) -> float:
        others = [j for j in members if j != i]
        return sum(_distance(signatures[i], signatures[j]) for j in others) / len(others)

    scored = sorted(
        members,
        key=lambda i: (
            -sum(1 for s in per_slide[i] if s.has_text),  # больше заполненных слотов
            typicality(i),                                # ближе к центру кластера
            i,
        ),
    )
    chosen = scored[0]
    return chosen, (
        f"из {len(members)} слайдов кластера: больше всего заполненных слотов "
        f"({sum(1 for s in per_slide[chosen] if s.has_text)}), "
        f"отклонение от центра {typicality(chosen):.2f}"
    )


# --- классификация -----------------------------------------------------


def _row_groups(shapes: list[ShapeObs], cy: int) -> list[list[ShapeObs]]:
    """Фигуры, стоящие в один визуальный ряд."""
    rows: list[list[ShapeObs]] = []
    tolerance = cy // 12
    for s in sorted(shapes, key=lambda s: (s.rect.y, s.rect.x)):
        placed = False
        for row in rows:
            if abs(row[0].rect.y - s.rect.y) <= tolerance:
                row.append(s)
                placed = True
                break
        if not placed:
            rows.append([s])
    return rows


def _classify(shapes: list[ShapeObs], cx: int, cy: int, index: int, total: int) -> str:
    area = cx * cy
    texts = [s for s in shapes if s.has_text]
    media = [s for s in shapes if _content_class(s) == "media"]

    if any(s.has_chart for s in shapes):
        return "chart"
    if any(s.has_table for s in shapes):
        return "table"
    if any(s.rect.area >= area * 0.55 for s in media):
        return "image_full"

    numeric = [s for s in texts if is_numeric_text(s.text)]
    if numeric and len(texts) <= 6:
        return "metric"

    quotes = [s for s in texts if any(r.italic for r in s.runs) and len(s.text) > _SHORT_TEXT]
    if quotes and len(texts) <= 3:
        return "quote"

    for row in _row_groups([s for s in shapes if s.rect], cy):
        with_text = [s for s in row if s.has_text]
        if len(with_text) >= 3:
            widths = sorted(s.rect.cx for s in with_text)
            if widths[-1] <= widths[0] * 1.35:  # карточки одной ширины
                return "cards"
        if len(with_text) == 2:
            a, b = sorted(with_text, key=lambda s: s.rect.x)
            wide = a.rect.cx >= cx * 0.25 and b.rect.cx >= cx * 0.25
            similar = max(a.rect.cx, b.rect.cx) <= min(a.rect.cx, b.rect.cx) * 1.4
            if wide and similar and len(a.text) > _SHORT_TEXT and len(b.text) > _SHORT_TEXT:
                return "two_column"

    if media and texts:
        return "image_text"

    if any(s.para_count >= 3 for s in texts):
        return "bullets"

    long_text = [s for s in texts if len(s.text) > _SHORT_TEXT]
    if not long_text and len(texts) <= 3:
        if index == 0:
            return "cover"
        if index == total - 1:
            return "closing"
        return "section"

    return "text"


# --- слоты -------------------------------------------------------------


def _dominant_run(shape: ShapeObs):
    if not shape.runs:
        return None
    return max(shape.runs, key=lambda r: len(r.text))


def _slot_role(shape: ShapeObs, body_size: float, is_first_text: bool) -> tuple[str, str]:
    if shape.has_chart:
        return "chart", "chart"
    if shape.has_table:
        return "table", "table"
    if _content_class(shape) == "media":
        return "image", "image"
    if not shape.has_text:
        return "decor", "none"

    run = _dominant_run(shape)
    size = run.size_pt if run and run.size_pt else body_size
    text = shape.text

    if shape.ph_type in _TITLE_PH:
        return "title", "text"
    if shape.ph_type == "subTitle":
        return "subtitle", "text"
    if is_numeric_text(text) and size >= body_size * 1.6:
        return "metric_value", "number"
    if run and run.italic and len(text) > _SHORT_TEXT:
        return "quote", "text"
    if shape.para_count >= 3 and len(text) > _SHORT_TEXT:
        return "bullet_list", "list"
    if size >= body_size * 1.6 and len(text) <= _SHORT_TEXT:
        return ("title" if is_first_text else "label"), "text"
    if len(text) <= _SHORT_TEXT and size < body_size:
        return "caption", "text"
    return "body", "text"


def _type_index(design_system) -> dict[tuple, str]:
    return {
        (t.latin or "", t.size_pt, t.bold, t.italic, t.caps or "none"): t.id
        for t in design_system.type_scale
    }


def _slots(shapes: list[ShapeObs], design_system, body_size: float) -> tuple[Slot, ...]:
    """Слоты — это то, что стадия PLAN наполняет.

    Декоративные фигуры сюда не попадают намеренно: слайд собирается
    клонированием донора (`ADR-0004`), поэтому весь декор приезжает вместе с
    ним бесплатно. Описывать его отдельно — значит раздувать библиотеку тем, чем
    никто не пользуется: на реальных шаблонах декор давал до 80 слотов из 142.
    """
    index = _type_index(design_system)
    out: list[Slot] = []
    seen_text = False
    n = 0
    for shape in shapes:
        if shape.rect is None:
            continue
        role, content_type = _slot_role(shape, body_size, not seen_text)
        if content_type == "none":
            continue
        n += 1
        if content_type in ("text", "list", "number"):
            seen_text = True

        run = _dominant_run(shape)
        type_role = None
        capacity = None
        if run is not None:
            key = (run.latin or "", round((run.size_pt or body_size) * 2) / 2,
                   run.bold, run.italic, run.caps or "none")
            type_role = index.get(key)
            capacity = estimate(
                cx_emu=shape.rect.cx,
                cy_emu=shape.rect.cy,
                size_pt=run.size_pt or body_size,
                insets=shape.insets,
                line_spacing_pct=run.line_spacing_pct,
                caps=run.caps,
                bold=run.bold,
                wrap=shape.wrap,
                donor_chars=len(shape.text) or None,
                donor_items=shape.para_count if content_type == "list" else None,
            )
        out.append(
            Slot(
                id=f"s{n:02d}",
                shape_id=shape.shape_id,
                role=role,
                content_type=content_type,
                rect=shape.rect,
                type_role=type_role,
                capacity=capacity,
                required=content_type in ("text", "list", "number"),
            )
        )
    return tuple(out)


# --- сборка ------------------------------------------------------------


def _body_size(design_system) -> float:
    for t in design_system.type_scale:
        if t.role == "body":
            return t.size_pt
    if design_system.type_scale:
        sizes = sorted(t.size_pt for t in design_system.type_scale)
        return sizes[len(sizes) // 2]
    return 18.0


def _from_layouts(deck: Deck, design_system, body: float) -> tuple[Pattern, ...]:
    """Фолбэк, когда слайдов слишком мало или их нет вовсе. `ADR-0006`."""
    out: list[Pattern] = []
    for n, part in enumerate(sorted(deck.layouts), 1):
        layout = deck.layouts[part]
        placeholders = [
            ph for ph in layout.by_type.values()
            if ph.rect is not None and ph.ph_type not in CHROME_PH
        ]
        if not placeholders:
            continue
        slots: list[Slot] = []
        for m, ph in enumerate(sorted(placeholders, key=lambda p: (p.rect.y, p.rect.x)), 1):
            role = "title" if ph.ph_type in _TITLE_PH else (
                "subtitle" if ph.ph_type == "subTitle" else "body"
            )
            nv = ph.sp.find(f"{qn('p:nvSpPr')}/{qn('p:cNvPr')}")
            slots.append(
                Slot(
                    id=f"s{m:02d}",
                    shape_id=(nv.get("id") if nv is not None else "") or "",
                    role=role,
                    content_type="text",
                    rect=ph.rect,
                    type_role=None,
                    capacity=estimate(cx_emu=ph.rect.cx, cy_emu=ph.rect.cy, size_pt=body),
                    required=True,
                )
            )
        out.append(
            Pattern(
                id=f"p{n:02d}",
                kind=_LAYOUT_KIND.get(layout.layout_type or "", "other"),
                donor_part=part,
                donor_index=-1,
                slots=tuple(slots),
                members=(),
                cohesion=None,
                donor_reason="выведен из макета: слайдов в шаблоне недостаточно",
                source="layouts",
                **dict(zip(("exclusive", "unknown_parts"), _donor_parts(deck, part))),
            )
        )
    return tuple(out)


#: Типы частей, которые два клона одного донора **разделить не могут**.
#: Каждый добыт замером, а не спецификацией: донор с такой частью, поставленный
#: в колоду дважды, даёт файл, который PowerPoint отказывается открывать
#: (`Z-44`, `WORKLOG/2026-09-19-z38-baseline.md`, `DOM-PKG §9`).
_EXCLUSIVE_RELS = ("chart", "oleObject", "vmlDrawing")

#: Типы, про которые известно, что разделяются законно: картинку можно
#: показать хоть на пяти слайдах, и замер это подтвердил (`p12` ×3 — цел).
_SHAREABLE_RELS = (
    "image", "hdphoto", "slideLayout", "notesSlide", "tags", "hyperlink",
    "themeOverride",
)


def _donor_parts(deck: Deck, part: str) -> tuple[bool, tuple[str, ...]]:
    """Что несёт донорский слайд: исключительное и незнакомое.

    **Список исключительных типов неполон по построению** — он собран из
    встреченных файлов, а не из спецификации. Поэтому возвращается ещё и
    перечень типов, которых нет ни в одном из двух списков: повтор такого
    донора не запрещается, но сопровождается предупреждением. Иначе новый тип
    исключительной части проявится тем же способом, каким нашёлся этот, —
    неоткрывающимся файлом у эксперта (`PLAN-6.1`, обратный план, п. 7).
    """
    try:
        rels = deck.pkg.rels(part)
    except Exception:                                    # noqa: BLE001
        return False, ()
    kinds = {r.type.rsplit("/", 1)[-1] for r in rels.values() if not r.external}
    exclusive = bool(kinds & set(_EXCLUSIVE_RELS))
    unknown = tuple(sorted(kinds - set(_EXCLUSIVE_RELS) - set(_SHAREABLE_RELS)))
    return exclusive, unknown


def build_pattern_library(
    deck: Deck, slides: list[SlideObs], design_system, filename: str
) -> PatternLibrary:
    cx, cy = deck.slide_cx, deck.slide_cy
    body = _body_size(design_system)
    source = PatternSource(filename=filename, sha256=deck.pkg.sha256)

    per_slide = {s.index: _shapes_of(s, cx, cy) for s in slides}
    usable = [s.index for s in slides if per_slide[s.index]]

    if len(usable) < _MIN_SLIDES:
        return PatternLibrary(
            source=source,
            patterns=_from_layouts(deck, design_system, body),
            notes=(
                f"Слайдов с содержимым {len(usable)}, это меньше {_MIN_SLIDES}: "
                f"паттерны выведены из макетов (ADR-0006).",
            ),
        )

    signatures = [_signature(per_slide[i], cx, cy) for i in usable]
    # Обложка и финал особенные по месту в колоде, а не по геометрии: они часто
    # неотличимы от разделителя, но роль у них другая. Держим их отдельно.
    pinned = {0, len(usable) - 1} if len(usable) >= 4 else set()
    free = [i for i in range(len(usable)) if i not in pinned]
    groups = [[i] for i in sorted(pinned)]
    groups += _cluster_subset(signatures, free, _MERGE_THRESHOLD)
    groups.sort(key=lambda g: g[0])

    patterns: list[Pattern] = []
    for n, group in enumerate(groups, 1):
        members = [usable[i] for i in group]
        local_sigs = signatures
        donor_local, reason = _donor(
            group, {i: per_slide[usable[i]] for i in group}, local_sigs
        )
        donor_index = usable[donor_local]
        donor_shapes = per_slide[donor_index]

        cohesion = None
        if len(group) > 1:
            pairs = [
                1.0 - _distance(signatures[a], signatures[b])
                for i, a in enumerate(group)
                for b in group[i + 1:]
            ]
            cohesion = round(sum(pairs) / len(pairs), 3)

        patterns.append(
            Pattern(
                id=f"p{n:02d}",
                kind=_classify(donor_shapes, cx, cy, donor_index, len(slides)),
                donor_part=slides[donor_index].part,
                donor_index=donor_index,
                slots=_slots(donor_shapes, design_system, body),
                members=tuple(members),
                cohesion=cohesion,
                donor_reason=reason,
                source="slides",
                **dict(zip(("exclusive", "unknown_parts"),
                           _donor_parts(deck, slides[donor_index].part))),
            )
        )

    notes: list[str] = []
    singles = sum(1 for p in patterns if len(p.members) == 1)
    if singles == len(patterns) and len(patterns) > 2:
        notes.append(
            "Ни один слайд не слился с другим: в шаблоне все раскладки разные. "
            "Это допустимый исход, а не ошибка."
        )
    return PatternLibrary(source=source, patterns=tuple(patterns), notes=tuple(notes))
