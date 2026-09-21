"""Что лежит поверх слота и где начинается непрозрачное. `PLAN-7.8` (`Z-48`).

`Z-47` научил отчёт **называть** заслонённый текст, но чинить его нечем: текст в
боксе переносится, и меньший кегль не уводит строку из-под картинки — меньше
кегль, меньше строк, а не у́же строка. Единственное место, где это лечится, —
ёмкость слота: если PLAN знает, что справа от третьей четверти бокса лежит
непрозрачная картинка, он туда столько текста и не положит.

## Порядок отрисовки берётся даром

Фигуры в `p:spTree` лежат **в порядке рисования**, и `analyze.shapes._walk`
сохраняет его, разворачивая группы на месте. Значит «поверх нас» — это просто
«позже в списке». PowerPoint для этого не нужен, в отличие от детектора VERIFY.

Без порядка отрисовки перекрытие не значит ничего: карточка накрывает свой же
текст на 100%, и это норма (`Z-47`, заход 2).

## Непрозрачность картинки считается по пикселям

Замер опроверг допущение «`p:pic` — значит непрозрачно»: 30 ложных сужений на
одном слайде выданного VK Tech, где рамка карточки непрозрачна на 1%
(`WORKLOG/2026-09-21-z48-baseline.md`, замер 4). Пиксели разбирает
`analyze/raster.py`; здесь только геометрия.

## Режется ширина, а не высота

Высоту чинит петля VERIFY: не влез по высоте — ужали кегль, стало меньше строк.
Ширину она не чинит **вовсе**, и это ровно тот разрыв, ради которого задача
заведена.
"""

from __future__ import annotations

from .deck import Rect
from .raster import OPAQUE, opacity_grid

#: Какую долю высоты слота помеха должна пересечь, чтобы резать ширину. Помеха,
#: задевшая бокс уголком, границей строки не является — то же рассуждение, что
#: у `_BACKING_COVERAGE` в `verify/space.py`.
CROSS_HEIGHT = 0.5

#: Заливки, скрывающие то, что под ними. `none` и `None` — не скрывают.
_OPAQUE_FILLS = {"solid", "gradient", "pattern", "picture"}

#: Сколько клеток у сетки непрозрачности. 32 колонки на картинку шириной в
#: половину слайда — это около 0.15 дюйма на клетку, вдвое меньше высоты
#: строки самого мелкого текста корпуса.
_GRID = 32


def _fill_is_opaque(shape) -> bool:
    if shape.fill_kind not in _OPAQUE_FILLS:
        return False
    return not (shape.fill is not None and shape.fill.alpha < OPAQUE)


def _opaque_runs(
    grid: tuple[tuple[float, ...], ...], y0: float, y1: float
) -> list[tuple[float, float]]:
    """Непрозрачные колонки картинки в полосе строк `[y0, y1]`, долями ширины.

    **Колонки, а не общий размах.** Размах сводил рамку карточки к сплошной
    плашке: у рамки непрозрачны оба края, между ними пусто, а «от левого края
    до правого» — это вся ширина. Так и вышло в первой редакции — 69 сужений на
    одном VK Tech вместо 34 даже у грубого правила.
    """
    rows, cols = len(grid), len(grid[0])
    first = max(0, min(rows - 1, int(y0 * rows)))
    last = max(first + 1, min(rows, int(y1 * rows + 0.999)))
    dense = [
        any(grid[r][c] >= OPAQUE for r in range(first, last))
        for c in range(cols)
    ]
    runs: list[tuple[float, float]] = []
    start = None
    for c, on in enumerate(dense):
        if on and start is None:
            start = c
        elif not on and start is not None:
            runs.append((start / cols, c / cols))
            start = None
    if start is not None:
        runs.append((start / cols, 1.0))
    return runs


def occluding_rects(shape, above, image_bytes, unhandled=None) -> list[tuple[str, Rect]]:
    """Непрозрачные области фигур, нарисованных поверх `shape`, с их `shape_id`.

    Идентификатор нужен стадии VERIFY: её детектор видит от PowerPoint только
    габаритный бокс фигуры и про прозрачные части картинки знать не может. Без
    этого детектор объявил бы заслонённым даже текст самого дизайнера
    (`WORKLOG/2026-09-21-z48-baseline.md`, замер 6).

    `above` — фигуры, идущие в `p:spTree` позже неё. `image_bytes` — как достать
    байты картинки по имени части; возвращает `None`, если части нет.

    Чужой текст помехой не считается: это соседство, и разбирается оно
    переполнением (`Z-47`).
    """
    box = shape.rect
    out: list[tuple[str, Rect]] = []
    if box is None or box.cx <= 0 or box.cy <= 0:
        return out
    for other in above:
        rect = other.rect
        if rect is None or other.hidden or other.has_text:
            continue
        if rect.cx <= 0 or rect.cy <= 0:
            continue
        if rect.right <= box.x or rect.x >= box.right:
            continue
        if rect.bottom <= box.y or rect.y >= box.bottom:
            continue

        if other.kind != "pic" and other.fill_kind != "picture":
            if _fill_is_opaque(other):
                out.append((other.shape_id, rect))
            continue

        data = image_bytes(other.image_part) if other.image_part else None
        grid = opacity_grid(data, _GRID, _GRID) if data else None
        if grid is None:
            # Судить не по чему: формат не разобран или части нет. Сужать слот
            # по такому ответу нельзя — это не «прозрачно» и не «заслоняет».
            if unhandled is not None:
                unhandled.append(
                    ("image_opacity",
                     f"непрозрачность картинки {other.image_part or '?'} не разобрана: "
                     "слот не сужен")
                )
            continue
        top = max(0.0, (box.y - rect.y) / rect.cy)
        bottom = min(1.0, (box.bottom - rect.y) / rect.cy)
        for a, b in _opaque_runs(grid, top, bottom):
            out.append((other.shape_id, Rect(
                    x=rect.x + round(a * rect.cx),
                    y=rect.y,
                    cx=max(1, round((b - a) * rect.cx)),
                    cy=rect.cy,
            )))
    return out


def visible_rect(box: Rect, blockers: list[tuple[str, Rect]]) -> Rect:
    """Полоса бокса, свободная от непрозрачного сверху. Режется только ширина.

    Помеха посередине оставляет две полосы, и «ширина слота» перестаёт быть
    одним числом. Берётся широкая из двух: оценка ёмкости и так грубая
    (`ADR-0008`), а недооценка безопаснее переоценки (`PLAN-7.8`, дыра 2).
    """
    left, right = box.x, box.right
    for _, other in blockers:
        crossed = min(other.bottom, box.bottom) - max(other.y, box.y)
        if crossed < box.cy * CROSS_HEIGHT:
            continue                       # задела край, а не пересекла бокс
        if other.x <= left and other.right >= right:
            return Rect(x=box.x, y=box.y, cx=0, cy=box.cy)
        if other.x <= left:
            left = max(left, other.right)
        elif other.right >= right:
            right = min(right, other.x)
        elif (other.x - left) >= (right - other.right):
            right = min(right, other.x)
        else:
            left = max(left, other.right)
    return Rect(x=left, y=box.y, cx=max(0, right - left), cy=box.cy)
