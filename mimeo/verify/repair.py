"""Ремонт плана по найденным дефектам. Шаг 4 плана `PLAN-4.0`.

Правится **план**, а не собранный файл: сборка обязана остаться чистой функцией
от шаблона и плана (`ADR-0005`). Ремонт возвращает новый `DeckPlan`, в котором у
переполненных слотов проставлена `Fill.font_scale`; записывает её в XML уже
COMPOSE, как и любое другое содержимое слота.

## Откуда числа

Всё измерено на корпусе из одиннадцати шаблонов
(`WORKLOG/2026-09-12-repair.md`), ни одно не назначено.

**Шаг.** Высота набранного текста падает примерно как квадрат шкалы: мельче
шрифт — и строки ниже, и символов в строке больше. Отсюда `k = 1/√отношение`.
Один такой шаг закрывает около половины случаев, дальше он применяется к
остатку.

**Цель с запасом.** Метить в «ровно влезло» нельзя: при отношении около 1.03 шаг
даёт полтора процента, и слот подползает к цели бесконечно. Замер: с целью 1.0
после четырёх раундов остаётся девять переполнений, с целью 0.9 — одно.

**Предел в пунктах, а не в процентах.** Кегли переполненных слотов начинаются с
12 pt. 58% от 44 pt — это 25 pt и нормально; 58% от 12 pt — 7 pt и читать нечем.

## Группы: почему шкала считается не на фигуру (`PLAN-4.1`, `Z-22`)

Шкала на каждую фигуру отдельно давала на одном слайде четыре разных кегля
подряд: текст везде помещался, детектор был доволен, выглядело небрежно.
Слоты, образующие визуальную группу, получают **общую** шкалу.

Признак группы измерен, а не назначен (`WORKLOG/2026-09-13-group-scale.md`):
**роль плюс высота бокса**. Позиционный признак — «одна полоса» — отпал на
замере: общий `y` есть лишь у 2 групп из 29, а 20 из 29 это лесенки и сетки.
Ширина отпала тоже: внутри настоящей группы она расходится до 2.92 дюйма,
потому что подписи растянуты по длине своего текста.

Главный вклад в разнобой давал не разброс вычисленных шкал, а слот **без
дефекта**: он не получал шкалы вовсе и оставался на 100% рядом с ужатым до 74%.
Поэтому ремонт идёт по группам, а не по дефектам, и назначает шкалу всем
участникам группы — включая тех, у кого дефекта нет.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

from ..model import DeckPlan, Fill, PatternLibrary, PlannedSlide, Slot
from .detect import BROKEN_WORD, Defect, Inspection
from .metrics import Measurement, ShapeMetric

#: Метим ниже порога: запас гасит ступенчатость переноса строк и сходимость
#: перестаёт быть бесконечной. Замер: 0.9 против 1.0 — три раунда против четырёх
#: с остатком.
TARGET_RATIO = 0.9

#: Ниже этого видимого кегля текст на слайде не читают. Значение продуктовое,
#: а не вычисленное: замер даёт только распределение кеглей (от 12 pt).
MIN_VISIBLE_PT = 10.0

#: Кегль неизвестен (PowerPoint отдаёт -2 при разных размерах в фигуре).
#: Считаем консервативно — как мелкий, чтобы предел сработал раньше, а не позже.
ASSUMED_PT_WHEN_UNKNOWN = 12.0

#: Ниже этой шкалы не опускаемся ни при каком кегле: PowerPoint сам не уходит
#: дальше, и ниже начинается неразборчивое.
HARD_MIN_SCALE = 25

#: Допуск, внутри которого две высоты бокса считаются одной, EMU (0.05 дюйма).
#: Тем же допуском мерился корпус (`WORKLOG/2026-09-13-group-scale.md`).
GROUP_HEIGHT_TOL = 45720


@dataclass(frozen=True)
class Repair:
    """Что ремонт сделал с одним слотом."""

    slide_index: int
    slot_id: str
    was: int            # шкала до, проценты
    now: int            # шкала после
    ratio: float        # во сколько раз текст не влезал
    at_floor: bool      # упёрлись в предел читаемости


@dataclass(frozen=True)
class RepairResult:
    plan: DeckPlan
    repairs: tuple[Repair, ...] = ()
    unresolved: tuple[Defect, ...] = ()

    @property
    def changed(self) -> bool:
        """Изменилось ли что-нибудь. Без этого петля не остановится: слот,
        упёршийся в предел, остаётся дефектом навсегда."""
        return any(r.now != r.was for r in self.repairs)


def _lowest_scale(metric: ShapeMetric | None, current: int, floor_pt: float) -> int:
    """Наименьшая допустимая шкала для этой фигуры, проценты.

    Движок отдаёт **видимый** кегль — уже с применённой шкалой, а не номинал
    (проверено замером: 16 pt при шкале 62% приходит как 10). Поэтому номинал
    восстанавливается делением на текущую шкалу. Без этого предел съезжал бы с
    каждым раундом, слоты упирались бы в него раньше времени, а присвоение
    предела могло бы даже **увеличить** шкалу обратно к сотне.
    """
    visible = metric.font_size if metric and metric.font_size > 0 else ASSUMED_PT_WHEN_UNKNOWN
    nominal = visible * 100.0 / max(current, 1)
    by_size = int(math.ceil(floor_pt / max(nominal, 1e-6) * 100))
    return max(HARD_MIN_SCALE, min(100, by_size))


def _text_slots(slide: PlannedSlide, pattern) -> tuple[Slot, ...]:
    """Слоты раскладки, которым план дал **текст**.

    Признак берётся из плана, а не из перечня ролей: кегль есть у текста, и
    картинке с диаграммой шкала неприменима. Списка ролей здесь и не может
    быть — он зависел бы от шаблона, а проверять будут на неизвестном.
    """
    by_id = {s.id: s for s in pattern.slots}
    out = []
    for fill in slide.fills:
        if fill.text is None and fill.items is None:
            continue
        slot = by_id.get(fill.slot_id)
        if slot is not None and slot.rect is not None:
            # Без геометрии слот не с чем сравнивать: он идёт послотным путём.
            out.append(slot)
    return tuple(out)


def _groups(slots: tuple[Slot, ...]) -> list[tuple[Slot, ...]]:
    """Визуальные группы: одна роль, одна высота бокса (`PLAN-4.1`).

    Порядок обхода задан явно — от него зависит разбиение, а значит и выходной
    файл, который обязан быть побайтово тем же.
    """
    buckets: list[list[Slot]] = []
    for slot in sorted(slots, key=lambda s: (s.role, s.rect.cy, s.rect.y, s.rect.x, s.id)):
        for bucket in buckets:
            head = bucket[0]
            if head.role == slot.role and abs(head.rect.cy - slot.rect.cy) <= GROUP_HEIGHT_TOL:
                bucket.append(slot)
                break
        else:
            buckets.append([slot])
    return [tuple(b) for b in buckets if len(b) > 1]


@dataclass(frozen=True)
class _Member:
    """Участник группы со всем, что нужно, чтобы назначить ему общую шкалу."""

    slot: Slot
    defect: Defect | None
    current: int
    wanted: int          # какую шкалу он хотел бы сам по себе
    floor: int           # ниже неё его текст нечитаем
    ratio: float


def repair_plan(
    plan: DeckPlan,
    inspection: Inspection,
    measurement: Measurement,
    slides_written: tuple[int, ...],
    library: PatternLibrary | None = None,
    target: float = TARGET_RATIO,
    floor_pt: float = MIN_VISIBLE_PT,
) -> RepairResult:
    """Новый план с назначенными шкалами и список того, что починить не вышло.

    Замер не состоялся — плана не трогаем: пустой список дефектов при неудачном
    замере означает «не смогли проверить», а не «всё хорошо».

    Без `library` группы не строятся и ремонт идёт послотно, как до `Z-22`:
    геометрия слотов живёт в раскладке, и знать её больше неоткуда.
    """
    if not inspection.measured:
        return RepairResult(plan=plan)

    position_of = {index: n for n, index in enumerate(slides_written, 1)}
    metrics = {(s.slide, s.shape_id): s for s in measurement.shapes}
    scale_of = {
        (slide.index, fill.slot_id): (fill.font_scale if fill.font_scale is not None else 100)
        for slide in plan.slides
        for fill in slide.fills
    }
    defect_of = {
        (d.slide_index, d.slot_id): d
        for d in inspection.defects
        if d.repairable and d.slot_id
    }
    patterns = {p.id: p for p in library.patterns} if library else {}

    wanted: dict[tuple[int, str], int] = {}
    repairs: list[Repair] = []
    unresolved: list[Defect] = [d for d in inspection.defects if not d.repairable]
    grouped: set[tuple[int, str]] = set()

    def member(slide_index: int, slot: Slot) -> _Member | None:
        """Участник или `None`, если про его кегль ничего не известно."""
        metric = metrics.get((position_of.get(slide_index, -1), slot.shape_id))
        if metric is None:
            # Фигура не дошла до замера. Догадка о чужом кегле утянула бы вниз
            # всю группу — такой слот из группы исключается (`PLAN-4.1`).
            return None
        current = scale_of.get((slide_index, slot.id), 100)
        defect = defect_of.get((slide_index, slot.id))
        ratio = defect.ratio if defect else metric.height_ratio
        want = _wanted(current, defect, target) if defect else current
        return _Member(
            slot=slot,
            defect=defect,
            current=current,
            wanted=want,
            floor=_lowest_scale(metric, current, floor_pt),
            ratio=ratio,
        )

    # --- ремонт по группам: шкала общая, и её получают все участники
    for slide in plan.slides:
        pattern = patterns.get(slide.pattern_id)
        if pattern is None:
            continue
        for group in _groups(_text_slots(slide, pattern)):
            members = [m for m in (member(slide.index, s) for s in group) if m is not None]
            if len(members) < 2 or not any(m.defect for m in members):
                # Группы нет либо ужимать в ней нечего: участники пойдут
                # послотным путём, байт в байт как раньше.
                continue
            # Минимум желаемых — но не ниже предела самого стеснённого
            # участника и не выше того, что у участника уже стоит.
            common = max(min(m.wanted for m in members), max(m.floor for m in members))
            common = min(common, min(m.current for m in members))
            for m in members:
                key = (slide.index, m.slot.id)
                grouped.add(key)
                wanted[key] = common
                repairs.append(
                    Repair(
                        slide_index=slide.index,
                        slot_id=m.slot.id,
                        was=m.current,
                        now=common,
                        ratio=m.ratio,
                        at_floor=common <= m.floor,
                    )
                )
                if m.defect is not None and (common <= m.floor or common > m.wanted):
                    # Ужать сколько нужно не дали либо предел, либо группа.
                    # Шкалу всё равно применяем — частичное улучшение лучше
                    # отказа, — но дефект остаётся в отчёте.
                    unresolved.append(m.defect)

    # --- одиночные слоты: прежний путь, без изменений
    for defect in inspection.defects:
        if not defect.repairable or (defect.slide_index, defect.slot_id) in grouped:
            continue

        current = scale_of.get((defect.slide_index, defect.slot_id), 100)
        metric = metrics.get((position_of.get(defect.slide_index, -1), defect.shape_id))
        lowest = _lowest_scale(metric, current, floor_pt)

        # Шкала только уменьшается: иначе слот, влезший с запасом, получил бы
        # увеличение, снова переполнился, и петля закачалась бы. Ограничение
        # действует и на присвоение предела — оно тоже не имеет права поднять
        # шкалу обратно.
        step = _wanted(current, defect, target)
        at_floor = step <= lowest
        if at_floor:
            step = min(current, lowest)

        wanted[(defect.slide_index, defect.slot_id)] = step
        repairs.append(
            Repair(
                slide_index=defect.slide_index,
                slot_id=defect.slot_id,
                was=current,
                now=step,
                ratio=defect.ratio,
                at_floor=at_floor,
            )
        )
        if at_floor:
            # Шкалу всё равно применяем — частичное улучшение лучше отказа, —
            # но дефект остаётся в отчёте: «влезло, но не читается» не победа.
            unresolved.append(defect)

    repairs.sort(key=lambda r: (r.slide_index, r.slot_id))
    unresolved.sort(key=lambda d: (d.slide_index, d.slot_id, d.kind))
    return RepairResult(
        plan=_with_scales(plan, wanted),
        repairs=tuple(repairs),
        unresolved=tuple(unresolved),
    )


def _step(current: int, ratio: float, target: float) -> int:
    """Шкала, при которой текст должен влезть. Никогда не больше нынешней."""
    step = int(round(current / math.sqrt(max(ratio, 1e-6) / target)))
    return min(step, current)


#: Цель для разорванного слова (`Z-56`): слово в 0.97 ширины строки. Запас
#: меньше, чем у высоты, — ширина слова падает ровно как кегль, без ступенек
#: переноса, — но не ноль: ширину отдают округлённой до сотых пункта.
WORD_TARGET = 0.97


def _wanted(current: int, defect: Defect, target: float) -> int:
    """Шкала против дефекта. Высота падает примерно как квадрат шкалы — шаг
    через корень; ширина слова — как сама шкала, шаг линейный, и не меньше
    одного процента, иначе петля топталась бы на месте."""
    if defect.kind == BROKEN_WORD:
        step = int(current * WORD_TARGET / max(defect.ratio, 1e-6))
        return min(step, current - 1)
    return _step(current, defect.ratio, target)


def _with_scales(plan: DeckPlan, wanted: dict[tuple[int, str], int]) -> DeckPlan:
    if not wanted:
        return plan
    slides: list[PlannedSlide] = []
    for slide in plan.slides:
        fills: list[Fill] = []
        for fill in slide.fills:
            scale = wanted.get((slide.index, fill.slot_id))
            fills.append(dataclasses.replace(fill, font_scale=scale) if scale else fill)
        slides.append(dataclasses.replace(slide, fills=tuple(fills)))
    return dataclasses.replace(plan, slides=tuple(slides))
