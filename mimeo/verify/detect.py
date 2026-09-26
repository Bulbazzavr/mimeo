"""Детектор дефектов вёрстки. Шаги 2–3 плана `PLAN-4.0`.

Превращает габариты, полученные от движка (`verify.metrics`), в список дефектов
с адресами **в координатах плана**: какой слайд плана, какой слот. Ремонт правит
план (`ADR-0005`), поэтому иных координат ему не нужно.

Здесь нет ни одного обращения к COM: детектор не знает, чем измеряли. Появится
LibreOffice (`Z-03`) — он отдаст ту же `Measurement`, и этот модуль не заметит
разницы.

## Что считается дефектом

Замер по корпусу (`WORKLOG/2026-09-12-detector.md`) задал две границы.

**Только наши слоты.** В самих шаблонах, до всякой нашей работы, не влезает 14%
текстовых фигур, у одного — 45%. Донорская фигура переполнена не по нашей вине и
чинить её нельзя: это чужой дизайн. Но и молчать нельзя — эксперту всё равно,
чья вина, — поэтому такие случаи получают отдельный вид и пометку «не наше».

**Режим автоподбора ничего не гарантирует.** Три переполненных слота уже имели
`normAutofit`: PowerPoint ужал текст, и этого не хватило. Пропускать их по
признаку режима нельзя.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model import DeckPlan, PatternLibrary
from ..oxml.units import EMU_PER_POINT
from .metrics import TOLERANCE, Measurement, ShapeMetric
from .space import OCCLUSION_SHARE, height_ratio, occluders

#: Текст выше своего места — чиним шкалой шрифта.
OVERFLOW_HEIGHT = "overflow_height"
#: Текст шире своего места. Измеряем и показываем; ремонт отложен (`PLAN-4.0`).
OVERFLOW_WIDTH = "overflow_width"
#: Переполнена фигура донора, которую мы не трогали. Не наше и не чиним.
DONOR_OVERFLOW = "donor_overflow"
#: Текст закрыт непрозрачной фигурой, лежащей **поверх** него (`Z-47`).
#: Ремонтом не чинится: текст в боксе переносится, и при меньшем кегле строка
#: всё равно тянется до правого края бокса — то есть по-прежнему уходит под
#: картинку. Меньше кегль — меньше строк, а не у́же строка.
OCCLUDED = "occluded"
#: Слово шире строки, и PowerPoint рвёт его посередине, без дефиса (`Z-56`):
#: «сортировк / и». Мерки высоты и ширины его не видят — после разрыва всё
#: влезает. Чинится шкалой шрифта: ширина слова падает как кегль.
BROKEN_WORD = "broken_word"


@dataclass(frozen=True)
class Defect:
    """Один дефект вёрстки по адресу плана."""

    kind: str
    slide_index: int          # индекс слайда в плане, а не номер в файле
    slot_id: str              # пусто для донорских фигур
    shape_id: str
    role: str
    #: У переполнений — во сколько раз содержимое больше места (больше
    #: единицы плохо). **У заслонения смысл другой:** доля закрытого текста,
    #: 0..1. Одно поле, два смысла — поэтому словами их различает `describe`,
    #: а не оставляет читателю догадываться (`Z-47`, проверка 1, находка 3).
    ratio: float
    repairable: bool

    @property
    def ours(self) -> bool:
        return self.kind != DONOR_OVERFLOW

    def describe(self) -> str:
        where = f"слайд {self.slide_index}"
        who = f"слот {self.slot_id} ({self.role})" if self.slot_id else f"фигура {self.shape_id}"
        if self.kind == OCCLUDED:
            # Доля, а не кратность: «в 0.68 раза» здесь читалось бы как
            # «влезает с запасом», а значит это «две трети текста не видно».
            return (f"{where}, {who}: {self.ratio:.0%} текста закрыто фигурой "
                    f"поверх него — ужатие кегля тут не поможет")
        what = {
            OVERFLOW_HEIGHT: "текст выше места",
            OVERFLOW_WIDTH: "текст шире места",
            DONOR_OVERFLOW: "переполнена фигура донора",
            BROKEN_WORD: "слово шире строки и разорвано",
        }.get(self.kind, self.kind)
        return f"{where}, {who}: {what} в {self.ratio:.2f} раза"


@dataclass(frozen=True)
class Inspection:
    """Итог осмотра одной колоды."""

    deck: str
    status: str                     # повторяет статус измерения
    defects: tuple[Defect, ...] = ()
    checked_slots: int = 0
    note: str = ""

    @property
    def measured(self) -> bool:
        """Замер состоялся. **Не** то же самое, что «дефектов нет».

        Отказ измерителя обязан отличаться от чистого результата: пустой список
        дефектов при несостоявшемся замере — та же ложь, что «0 проблем» у файла,
        который не открывается (`Z-20`).
        """
        return self.status == "ok"

    @property
    def occluded(self) -> tuple[Defect, ...]:
        """Заслонённый текст. Отдельно от всего прочего намеренно: он не
        чинится ремонтом и не относится к донору (`Z-47`). Остальное
        нечинимое тоже идёт своими списками — `overflow_width` и
        `donor_overflow`, а не остатком от вычитания (`Z-52`)."""
        return tuple(d for d in self.defects if d.kind == OCCLUDED)

    @property
    def repairable(self) -> tuple[Defect, ...]:
        return tuple(d for d in self.defects if d.repairable)

    @property
    def overflow_width(self) -> tuple[Defect, ...]:
        """Наш текст шире своего места. Ремонт ширину не берёт (`PLAN-4.0`).

        До 22 сентября это жило только в остатке `defects - ours - occluded`,
        и сводка звала его «в фигурах донора — мы в них ничего не
        подставляли». Замер по корпусу: из 27 таких «донорских» **все 27**
        были нашими надписями шире места, донорских — ноль (`Z-52`,
        `WORKLOG/2026-09-22-z52-baseline.md`).
        """
        return tuple(d for d in self.defects if d.kind == OVERFLOW_WIDTH)

    @property
    def donor_overflow(self) -> tuple[Defect, ...]:
        """Переполненные фигуры донора — те, куда мы ничего не подставляли."""
        return tuple(d for d in self.defects if d.kind == DONOR_OVERFLOW)


def _slot_index(
    plan: DeckPlan, library: PatternLibrary, slides_written: tuple[int, ...]
) -> dict[tuple[int, str], tuple[int, str, str]]:
    """(номер слайда в файле, shape_id) -> (индекс слайда плана, слот, роль).

    Номер слайда в файле берётся из `slides_written`, а не из позиции в плане:
    слайд, который не собрался, в файл не попадает, и нумерация разъезжается.
    """
    patterns = {p.id: p for p in library.patterns}
    by_index = {s.index: s for s in plan.slides}
    out: dict[tuple[int, str], tuple[int, str, str]] = {}

    for position, plan_index in enumerate(slides_written, 1):
        planned = by_index.get(plan_index)
        if planned is None:
            continue
        pattern = patterns.get(planned.pattern_id)
        if pattern is None:
            continue
        slots = {s.id: s for s in pattern.slots}
        for fill in planned.fills:
            slot = slots.get(fill.slot_id)
            if slot is not None:
                out[(position, slot.shape_id)] = (plan_index, slot.id, slot.role)
    return out


def _opaque_index(
    plan: DeckPlan, library: PatternLibrary, slides_written: tuple[int, ...]
) -> dict[tuple[int, str], tuple[tuple[float, float, float, float], ...]]:
    """Непрозрачные куски декора донора, в пунктах и по номеру слайда в файле.

    Стадия ANALYZE разобрала картинки по пикселям и знает, где они на самом
    деле что-то закрывают (`Z-48`). Детектор без этого судил по габаритному
    боксу и называл заслонённым текст, который читается.
    """
    patterns = {p.id: p for p in library.patterns}
    by_index = {s.index: s for s in plan.slides}
    out: dict[tuple[int, str], list[tuple[float, float, float, float]]] = {}
    for position, plan_index in enumerate(slides_written, 1):
        planned = by_index.get(plan_index)
        pattern = patterns.get(planned.pattern_id) if planned else None
        if pattern is None:
            continue
        for region in pattern.opaque:
            out.setdefault((position, region.shape_id), []).append((
                region.rect.x / EMU_PER_POINT, region.rect.y / EMU_PER_POINT,
                region.rect.cx / EMU_PER_POINT, region.rect.cy / EMU_PER_POINT,
            ))
    return {k: tuple(v) for k, v in out.items()}


def _slide_of_position(slides_written: tuple[int, ...], position: int) -> int:
    return slides_written[position - 1] if 1 <= position <= len(slides_written) else -1


def find_defects(
    measurement: Measurement,
    plan: DeckPlan,
    library: PatternLibrary,
    slides_written: tuple[int, ...],
    tolerance: float = TOLERANCE,
) -> Inspection:
    """Дефекты одной колоды. Порядок детерминирован: слайд, слот, вид."""
    if not measurement.ok:
        return Inspection(
            deck=measurement.deck,
            status=measurement.status,
            note=measurement.note,
        )

    index = _slot_index(plan, library, slides_written)
    opaque = _opaque_index(plan, library, slides_written)
    # Ключ обязан нести номер слайда: `id` фигур повторяются от слайда к
    # слайду, и словарь по одному `id` молча склеивал бы разные фигуры
    # (`Z-47`, поймано в самой мерке).
    texts = frozenset(
        (s.slide, s.shape_id) for s in measurement.shapes if s.chars
    )
    boxes = {(b.slide, b.shape_id): b for b in measurement.boxes}
    defects: list[Defect] = []
    checked = 0

    for shape in measurement.shapes:
        # Мерка одна для всех: не «текст выше бокса», а «тексту некуда
        # деться» (`ADR-0015`). Без боксов от зонда возвращается прежнее
        # отношение к боксу, то есть поведение до `Z-24`.
        ratio = height_ratio(shape, measurement)
        known = index.get((shape.slide, shape.shape_id))
        if known is None:
            if ratio > tolerance:
                defects.append(
                    Defect(
                        kind=DONOR_OVERFLOW,
                        slide_index=_slide_of_position(slides_written, shape.slide),
                        slot_id="",
                        shape_id=shape.shape_id,
                        role="",
                        ratio=round(ratio, 3),
                        repairable=False,
                    )
                )
            continue

        plan_index, slot_id, role = known
        checked += 1
        defects.extend(_ours(shape, plan_index, slot_id, role, tolerance, ratio))

        # Заслонение считается отдельно от переполнения: это другой дефект и
        # другая причина. Слот может влезать идеально и при этом наполовину
        # прятаться под декоративной картинкой донора (`Z-47`, `PLAN-7.7`).
        # Бокс берётся **из замера**, а не строится из метрики: `_box_of`
        # ставит `z = 0`, и тогда «поверх нас» оказывается что угодно. Так и
        # вышло в первой редакции — 13 заслонений вместо одного. Нет бокса —
        # нет и суждения: «проверить не смог» это не «чисто».
        own = boxes.get((shape.slide, shape.shape_id))
        share = 0.0 if own is None else occluders(
            (shape.left + shape.margin_left, shape.top + shape.margin_top,
             shape.text_width, shape.text_height),
            own, measurement.boxes, texts, opaque,
        )
        if share > OCCLUSION_SHARE:
            defects.append(
                Defect(
                    kind=OCCLUDED,
                    slide_index=plan_index,
                    slot_id=slot_id,
                    shape_id=shape.shape_id,
                    role=role,
                    ratio=round(share, 3),
                    repairable=False,
                )
            )

    defects.sort(key=lambda d: (d.slide_index, d.slot_id, d.shape_id, d.kind))
    return Inspection(
        deck=measurement.deck,
        status=measurement.status,
        defects=tuple(defects),
        checked_slots=checked,
        note=measurement.note,
    )


def _ours(
    shape: ShapeMetric, plan_index: int, slot_id: str, role: str, tolerance: float,
    ratio: float,
) -> list[Defect]:
    """`ratio` — отношение к доступному месту, а не к боксу (`ADR-0015`)."""
    found: list[Defect] = []
    if ratio > tolerance:
        found.append(
            Defect(
                kind=OVERFLOW_HEIGHT,
                slide_index=plan_index,
                slot_id=slot_id,
                shape_id=shape.shape_id,
                role=role,
                ratio=round(ratio, 3),
                repairable=True,
            )
        )
    elif shape.broken_words:
        # После высоты: ужатие по высоте нередко снимает и разрыв. До ширины:
        # разорванная строка влезает, и мерка ширины молчит (`Z-56`).
        found.append(
            Defect(
                kind=BROKEN_WORD,
                slide_index=plan_index,
                slot_id=slot_id,
                shape_id=shape.shape_id,
                role=role,
                ratio=round(max(shape.broken_width / shape.usable_width, 1.0), 3),
                repairable=True,
            )
        )
    elif shape.width_ratio > tolerance:
        # Только когда по высоте всё в порядке: иначе это один и тот же дефект,
        # и ремонт по высоте уберёт оба. Замер: таких семь на корпус.
        found.append(
            Defect(
                kind=OVERFLOW_WIDTH,
                slide_index=plan_index,
                slot_id=slot_id,
                shape_id=shape.shape_id,
                role=role,
                ratio=round(shape.width_ratio, 3),
                repairable=False,
            )
        )
    return found
