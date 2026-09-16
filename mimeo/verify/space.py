"""Сколько места у текста на самом деле. Шаг 3 плана `PLAN-4.2` (`ADR-0015`).

Бокс — это замысел дизайнера, а не предел. PowerPoint нигде не обрезает текст по
боксу: не влезший просто рисуется дальше. Значит «текст выше бокса» — лишь
признак того, что текст **вылез**, а брак это или нет, решает, **есть ли куда
вылезать**.

## Откуда правила

Замерено на корпусе из одиннадцати шаблонов
(`WORKLOG/2026-09-13-autofit.md`), сверено с растром.

**Помеха — любая фигура, не только текстовая.** Текст есть у 143 фигур корпуса
из 2209; помехой чаще оказывается иконка или плашка.

**Подложка границей не является** — но отдельного правила не требует.
Фигура, содержащая наш бокс, начинается выше его низа и кончается ниже верха, и
её отсеивает та же проверка расстояния, что и всех остальных. План закладывал
сюда особое условие; мутационный прогон показал, что оно ни на что не влияет, а
замер по корпусу это подтвердил. Убрано: мёртвое правило дороже отсутствующего.

**Невидимые фигуры пропускаются.** Клон несёт мусор донора: скрытые фигуры и
заготовки за холстом. Скрытая фигура не мешает зрителю, а место занимала бы.

**Край слайда — такая же граница, как соседняя фигура.**

**Направление задаёт вертикальный якорь.** Верхний — текст растёт вниз, нижний —
вверх, остальные (центр, по ширине, распределён) — в обе стороны.

## Свойство, на которое опирается безопасность правки

Доступное место **всегда не меньше** полезной высоты бокса: свободное место
неотрицательно. Значит новая мерка может дефекты только снимать, но не
добавлять. Ложных срабатываний от неё не прибавится по построению.
"""

from __future__ import annotations

from .metrics import Box, Measurement, ShapeMetric

#: msoAnchor: текст прижат к верху — растёт вниз.
ANCHOR_TOP = 1
#: Текст прижат к низу — растёт вверх.
ANCHOR_BOTTOM = 3

#: Допуск на округление координат в пунктах. Зонд округляет до сотых.
SLACK = 0.5


def _box_of(metric: ShapeMetric) -> Box:
    return Box(
        slide=metric.slide, shape_id=metric.shape_id,
        x=metric.left, y=metric.top, w=metric.width, h=metric.height,
    )


def _blockers(box: Box, boxes: tuple[Box, ...]) -> list[Box]:
    """Фигуры, способные помешать этому тексту.

    Отсеиваются: он сам, невидимые и всё, что не пересекается с ним по
    горизонтали — текст растёт вертикально, вбок не заглядывать.

    **Подложка отдельного правила не требует.** Фигура, содержащая наш бокс,
    начинается выше его нижнего края и кончается ниже верхнего, а такие отсеет
    проверка расстояния в `free_below`/`free_above`. План закладывал здесь
    отдельное условие; мутационный прогон показал, что оно мёртвое, а замер по
    корпусу подтвердил: 16 дефектов с ним и 16 без, списки совпали.
    """
    out = []
    for other in boxes:
        if other.slide != box.slide or other.shape_id == box.shape_id:
            continue
        if not other.visible:
            continue
        if other.right <= box.x + SLACK or other.x >= box.right - SLACK:
            continue
        out.append(other)
    return out


def free_below(box: Box, blockers: list[Box], page_height: float) -> float:
    """Пустое место под фигурой до ближайшей помехи или низа слайда."""
    gap = page_height - box.bottom
    for other in blockers:
        # Фигура, начинающаяся выше нижнего края нашей, стоит за текстом,
        # а не под ним: границей она не является.
        distance = other.y - box.bottom
        if distance >= -SLACK:
            gap = min(gap, max(0.0, distance))
    return max(0.0, gap)


def free_above(box: Box, blockers: list[Box]) -> float:
    """Пустое место над фигурой до ближайшей помехи или верха слайда."""
    gap = box.y
    for other in blockers:
        distance = box.y - other.bottom
        if distance >= -SLACK:
            gap = min(gap, max(0.0, distance))
    return max(0.0, gap)


def available_height(metric: ShapeMetric, measurement: Measurement) -> float:
    """Высота, на которую текст этой фигуры может рассчитывать.

    Без боксов или без габаритов слайда возвращается полезная высота бокса —
    ровно прежнее поведение. Это сознательный откат, а не тихое снятие
    дефектов: не зная окружения, мы не вправе объявлять место свободным
    (`PLAN-4.2`, первая логическая проверка, дыра 4).
    """
    if not measurement.boxes or measurement.page is None:
        return metric.usable_height

    box = _box_of(metric)
    blockers = _blockers(box, measurement.boxes)
    page_height = measurement.page[1]

    if metric.anchor == ANCHOR_TOP:
        extra = free_below(box, blockers, page_height)
    elif metric.anchor == ANCHOR_BOTTOM:
        extra = free_above(box, blockers)
    else:
        # Центр, «по ширине», «распределён»: текст расходится в обе стороны.
        extra = free_above(box, blockers) + free_below(box, blockers, page_height)

    return metric.usable_height + max(0.0, extra)


def height_ratio(metric: ShapeMetric, measurement: Measurement) -> float:
    """Во сколько раз текст выше доступного места. <= 1 — зритель брака не видит."""
    return metric.text_height / max(available_height(metric, measurement), 1.0)
