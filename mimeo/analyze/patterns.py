"""Библиотека паттернов: типовые раскладки слайдов шаблона.

Источник раскладок — реальные слайды, а не макеты (`ADR-0004`). Кластеризация
своя, без внешних зависимостей (`ADR-0007`). Когда слайдов слишком мало, есть
фолбэк на макеты (`ADR-0006`).

Порядок: сигнатура слайда → кластеры → донор в каждом кластере → назначение
паттерна по донору → слоты из фигур донора. Донор выбирается **раньше**
классификации: клонировать будем именно его, значит и метку он должен задавать.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from ..model import OpaqueRegion, Pattern, PatternLibrary, PatternSource, Slot
from ..oxml.ns import qn
from .captions import CaptionConfig, caption_kind
from .captions import config_path as caption_config_path
from .captions import load_config as load_caption_config
from .deck import Deck, Rect
from .fitting import estimate
from .picture import FRAME, classify_slots, find_frames
from .picture import config_path as picture_config_path
from .picture import load_config as load_picture_config
from .occlusion import occluding_rects, visible_rect
from .shapes import CHROME_PH, ShapeObs, SlideObs
from .tokens import is_numeric_text
from .typeface import TypefaceConfig
from .typeface import classify as classify_typeface
from .typeface import load_config as load_typeface_config

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


#: Высота полосы, внутри которой фигуры считаются стоящими в одной строке
#: (`Z-31`, `PLAN-7.6`). Сортировка по **точному** `y` рассыпала строку
#: карточек: соседи по строке отличаются по высоте на десяток тысяч EMU, и
#: порядок слева направо превращался в случайный. Замер: точный `y` и полоса
#: дают разный порядок на **90 слайдах из 209**.
#:
#: 200 000 EMU — это 0.22 дюйма, меньше высоты строки самого мелкого текста в
#: корпусе, то есть заведомо внутри одной визуальной строки. Порог проверен на
#: устойчивость: от 50 000 до 400 000 ответ меняется на один слайд из сорока
#: (`WORKLOG/2026-09-20-z31-baseline.md`, замер 7).
_ROW_BAND = 200_000


def _shapes_of(slide: SlideObs, cx: int, cy: int) -> list[ShapeObs]:
    """Фигуры, определяющие раскладку, **в порядке чтения**.

    Порядок здесь — не украшение: он становится порядком слотов, а тот —
    порядком, в котором стадия PLAN раскладывает тезисы. Сверен с независимым
    свидетелем: десять доноров корпуса содержат авторскую нумерацию карточек, и
    порядок «полоса по `y`, затем `x`» угадал её **9 раз из 10**. Порядок XML —
    только 5 из 10, поэтому сортируем, а не берём как есть (`Z-31`).

    Колонтитулы и служебное — мимо.
    """
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
    return sorted(out, key=lambda s: (s.rect.y // _ROW_BAND, s.rect.x, s.shape_id))


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


def _classify(shapes: list[ShapeObs], cx: int, cy: int, index: int, total: int,
              captions: CaptionConfig | None = None) -> str:
    area = cx * cy
    texts = [s for s in shapes if s.has_text]
    media = [s for s in shapes if _content_class(s) == "media"]

    if any(s.has_chart for s in shapes):
        return "chart"
    if any(s.has_table for s in shapes):
        return "table"
    # Подпись дизайнера — раньше геометрии (`Z-58`): финал «Спасибо» на фоне
    # фотографии иначе ушёл бы в `image_full`, оглавление из пунктов — в
    # `cards`. Позиционное «последний слайд — финал» ниже остаётся запасным,
    # когда подписи нет.
    caption = caption_kind(texts, captions, _SHORT_TEXT) if captions else None
    if caption:
        return caption
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


def _slots(
    shapes: list[ShapeObs],
    design_system,
    body_size: float,
    typo: TypefaceConfig | None = None,
    slide: SlideObs | None = None,
    image_bytes: Callable[[str | None], bytes | None] | None = None,
    unhandled: list[tuple[str, str]] | None = None,
    opaque: dict[tuple, OpaqueRegion] | None = None,
    pictures=None,
) -> tuple[Slot, ...]:
    """Слоты — это то, что стадия PLAN наполняет.

    Декоративные фигуры сюда не попадают намеренно: слайд собирается
    клонированием донора (`ADR-0004`), поэтому весь декор приезжает вместе с
    ним бесплатно. Описывать его отдельно — значит раздувать библиотеку тем, чем
    никто не пользуется: на реальных шаблонах декор давал до 80 слотов из 142.
    """
    index = _type_index(design_system)
    typo = typo or load_typeface_config()
    # Порядок отрисовки — это порядок фигур в `p:spTree`, а `shapes` здесь уже
    # пересортированы в порядок чтения (`Z-31`) и прорежены от мелкого декора.
    # Значит «что лежит поверх» спрашивается у слайда целиком: как раз мелкий
    # декор чаще всего и закрывает текст (`Z-48`).
    #
    # Ключ — сам объект, а не `shape_id`: у фигуры без `p:cNvPr` идентификатор
    # вырождается в «?», и такие молча склеились бы. Списки здесь — виды на
    # один и тот же `slide.shapes`, копий никто не делает.
    order = {id(s): n for n, s in enumerate(slide.shapes)} if slide else {}
    # Рамка под фото и подсказка дизайнера в ней (`Z-55`, `analyze/picture.py`):
    # рамка — место под картинку, хоть и фигура без текста; подсказка — не
    # место под текст, но остаётся слотом, чтобы сборка стёрла «Вставить фото».
    size = getattr(design_system, "slide", None)
    frames = find_frames(shapes, size.cx_emu, size.cy_emu, pictures) if size else []
    frame_ids = {id(f) for f, _ in frames}
    hint_ids = {id(h) for _, h in frames}
    out: list[Slot] = []
    seen_text = False
    n = 0
    for shape in shapes:
        if shape.rect is None:
            continue
        if id(shape) in frame_ids:
            role, content_type = "image", "image"
        elif id(shape) in hint_ids:
            role, content_type = "decor", "none"
        else:
            role, content_type = _slot_role(shape, body_size, not seen_text)
            if content_type == "none":
                continue
        n += 1
        if content_type in ("text", "list", "number"):
            seen_text = True
        frame = id(shape) in frame_ids

        # Видимая полоса: что от бокса остаётся, когда сверху лежит
        # непрозрачное. Ёмкость считается по ней, а не по боксу (`Z-48`).
        # У рамки ёмкости нет, и заслонители её в `opaque` не добавляются.
        visible = shape.rect
        if slide is not None and image_bytes is not None and id(shape) in order and not frame:
            mine = order[id(shape)]
            above = [s for s in slide.shapes if order.get(id(s), -1) > mine]
            blockers = occluding_rects(shape, above, image_bytes, unhandled)
            visible = visible_rect(shape.rect, blockers)
            if opaque is not None:
                # Ключ — фигура и её кусок: одна картинка накрывает несколько
                # слотов, и запоминать её надо один раз.
                for shape_id, rect in blockers:
                    opaque[(shape_id, rect.x, rect.cx)] = OpaqueRegion(
                        shape_id=shape_id, rect=rect
                    )

        # У рамки в `txBody` бывает пустой прогон — ёмкость ей не нужна.
        run = None if frame else _dominant_run(shape)
        # Вид гарнитуры считается по фигуре, а не по прогону (`DOM-TEXT §8`):
        # гарнитура берётся у доминирующего прогона, текст — весь, какой есть.
        # Без прогона остаётся `None` — «судить не по чему», а не `prose`.
        typeface_kind = None
        type_role = None
        capacity = None
        if run is not None:
            typeface_kind = classify_typeface(run.latin, shape.text, typo)
            key = (run.latin or "", round((run.size_pt or body_size) * 2) / 2,
                   run.bold, run.italic, run.caps or "none")
            type_role = index.get(key)
            capacity = estimate(
                cx_emu=visible.cx,
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
                typeface_kind=typeface_kind,
                capacity=capacity,
                # Рамка под фото обязательна: пустая белая карточка на слайде
                # видна так же, как пустой текстовый слот (`Z-55`).
                required=frame or content_type in ("text", "list", "number"),
                picture_kind=FRAME if frame else None,
                visible=visible,
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


def _mark_pictures(patterns, cx: int, cy: int, notes: list[str]) -> tuple:
    """Проставляет `Slot.picture_kind` по геометрии (`Z-28a`, `PLAN-7.10`).

    Отдельным проходом по готовым раскладкам, а не внутри `_slots`, по двум
    причинам. Первая: вид слота зависит от **других слотов той же раскладки**
    — подложку видно только по тому, что на ней лежит текст. Вторая: так
    признак ставится и раскладкам из макетов (`_from_layouts`), а они идут
    другим путём.
    """
    cfg = load_picture_config()
    if not cfg.loaded:
        notes.append(
            f"Конфиг видов картинки не прочитан, работают встроенные значения: "
            f"{picture_config_path()}"
        )
    out = []
    for pattern in patterns:
        kinds = classify_slots(pattern.slots, cx, cy, cfg)
        if not kinds:
            out.append(pattern)
            continue
        out.append(replace(pattern, slots=tuple(
            replace(s, picture_kind=kinds[s.id]) if s.id in kinds else s
            for s in pattern.slots
        )))
    return tuple(out)


def build_pattern_library(
    deck: Deck, slides: list[SlideObs], design_system, filename: str
) -> PatternLibrary:
    cx, cy = deck.slide_cx, deck.slide_cy
    body = _body_size(design_system)
    source = PatternSource(filename=filename, sha256=deck.pkg.sha256)
    # Один раз на шаблон, а не на каждую раскладку: файл один и тот же.
    typo = load_typeface_config()
    captions = load_caption_config()
    pictures = load_picture_config()

    # Байты картинки по имени части, с кэшем: одна и та же картинка стоит на
    # разных слайдах и разбирать её дважды незачем (`PLAN-7.8`, дыра 4).
    cache: dict[str, bytes | None] = {}

    def image_bytes(part: str | None) -> bytes | None:
        if not part:
            return None
        if part not in cache:
            try:
                cache[part] = deck.pkg.read(part)
            except (KeyError, OSError):
                cache[part] = None
        return cache[part]

    opacity_unhandled: list[tuple[str, str]] = []
    per_slide = {s.index: _shapes_of(s, cx, cy) for s in slides}
    usable = [s.index for s in slides if per_slide[s.index]]

    if len(usable) < _MIN_SLIDES:
        layout_notes = [
            f"Слайдов с содержимым {len(usable)}, это меньше {_MIN_SLIDES}: "
            f"паттерны выведены из макетов (ADR-0006).",
        ]
        return PatternLibrary(
            source=source,
            patterns=_mark_pictures(
                _from_layouts(deck, design_system, body), cx, cy, layout_notes
            ),
            notes=tuple(layout_notes),
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

        opaque: dict[tuple, OpaqueRegion] = {}
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
                kind=_classify(donor_shapes, cx, cy, donor_index, len(slides), captions),
                donor_part=slides[donor_index].part,
                donor_index=donor_index,
                slots=_slots(donor_shapes, design_system, body, typo,
                             slide=slides[donor_index], image_bytes=image_bytes,
                             unhandled=opacity_unhandled, opaque=opaque,
                             pictures=pictures),
                members=tuple(members),
                cohesion=cohesion,
                donor_reason=reason,
                source="slides",
                opaque=tuple(opaque[k] for k in sorted(opaque)),
                **dict(zip(("exclusive", "unknown_parts"),
                           _donor_parts(deck, slides[donor_index].part))),
            )
        )

    notes: list[str] = []
    if not captions.loaded:
        notes.append(
            f"Конфиг подписей оглавления и финала не прочитан, работают встроенные "
            f"значения: {caption_config_path()}"
        )
    if opacity_unhandled:
        # Молчаливый пропуск неотличим от «проверено и чисто», поэтому факт
        # называется вслух: слот не сужен, потому что судить было не по чему.
        # Два вида считаются **порознь**: у картинки не разобран формат, у
        # диаграммы и таблицы заливки нет вовсе. Смешивать их в одну строку
        # значит назвать диаграмму картинкой (`Z-50`).
        files = sorted({m.split()[2] for kind, m in opacity_unhandled
                        if kind == "image_opacity"})
        frames = sum(1 for kind, _ in opacity_unhandled if kind == "frame_opacity")
        if files:
            notes.append(
                f"Непрозрачность {len(files)} картинок не разобрана "
                f"({', '.join(files[:3])}{', …' if len(files) > 3 else ''}): "
                "слоты под ними не сужены."
            )
        if frames:
            notes.append(
                f"Диаграмм и таблиц поверх текстовых слотов: {frames}. "
                "Заливки у них нет, судить о непрозрачности не по чему — "
                "слоты под ними не сужены."
            )
    singles = sum(1 for p in patterns if len(p.members) == 1)
    if singles == len(patterns) and len(patterns) > 2:
        notes.append(
            "Ни один слайд не слился с другим: в шаблоне все раскладки разные. "
            "Это допустимый исход, а не ошибка."
        )
    marked = _mark_pictures(tuple(patterns), cx, cy, notes)
    return PatternLibrary(source=source, patterns=marked, notes=tuple(notes))
