"""Отчёт стадии VERIFY: в строки для человека и в JSON для машины.

Шаг 7 плана `PLAN-4.0`. Форматирование живёт здесь, а не в `cli.py`, потому что
печатают отчёт двое — команда `build --verify` и `tools/build_verified.py`, — и
разойтись в формулировках им нельзя.

## Чего здесь нельзя делать

**Печатать число дефектов, когда замер не состоялся.** «Переполнено 0» у файла,
который не открылся, — та же ложь, что зелёная галочка на непроверенном (`Z-20`).
При `status != "ok"` вместо чисел печатается причина.

**Молчать о том, что петля сдалась.** «Раунды кончились» и «упёрлись в предел» —
разные исходы, и пользователю они говорятся разными словами.
"""

from __future__ import annotations

import json
import os

from .loop import (
    STOP_FITS,
    STOP_FLOOR,
    STOP_NOT_MEASURED,
    STOP_ROUNDS,
    STOP_UNAVAILABLE,
    VerifyReport,
)

#: Причина остановки человеческими словами. Ключи — `STOP_*` из `loop`.
WHY = {
    STOP_FITS: "всё влезло",
    STOP_FLOOR: "упёрлись в предел читаемости — дальше ужимать значит сделать нечитаемым",
    STOP_ROUNDS: "раунды кончились, а дефекты остались",
    STOP_NOT_MEASURED: "замер не состоялся",
    STOP_UNAVAILABLE: "мерить нечем",
}

#: Сколько неустранённых дефектов перечислять поимённо, прежде чем свернуть.
SHOW_UNRESOLVED = 5


def plural(n: int, one: str, few: str, many: str) -> str:
    """«1 переполнение», «2 переполнения», «5 переполнений».

    Сводку читает человек, и «1 переполнений» в демонстрации бросается в глаза.
    """
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def describe(report: VerifyReport, show: int = SHOW_UNRESOLVED) -> list[str]:
    """Отчёт строками для консоли. Первая строка выровнена как остальные в `build`."""
    if report.status == "unavailable":
        return [f"проверка    пропущена: {report.note or WHY[STOP_UNAVAILABLE]}"]

    if report.status != "ok":
        # Числа не печатаем принципиально: их нет, а ноль соврал бы.
        return [
            f"проверка    НЕ УДАЛАСЬ: {report.note or WHY[STOP_NOT_MEASURED]}",
            "            вёрстка осталась непроверенной — это не то же самое, "
            "что «дефектов нет»",
        ]

    rounds = len(report.rounds)
    lines = [
        f"проверка    раундов {rounds}, переполнено {report.before} → {report.after}"
        f" за {report.seconds:.1f} с"
    ]
    lines.append(f"            встала: {WHY.get(report.stopped, report.stopped)}")

    # Заслонение вычитается: оно «наше», но не чинится и к донору отношения
    # не имеет. Без этого вычитания оно уехало бы в донорские и соврало
    # (`Z-47`, найдено при работе, а не тестом).
    donor = max((r.defects - r.ours - r.occluded for r in report.rounds), default=0)
    if donor:
        word = plural(donor, "переполнение", "переполнения", "переполнений")
        lines.append(
            f"            плюс {donor} {word} в фигурах донора — "
            "мы в них ничего не подставляли, чинить нечем"
        )

    if report.occluded:
        n = len(report.occluded)
        word = plural(n, "надпись", "надписи", "надписей")
        lines.append(
            f"            закрыто фигурой поверх: {n} {word} — "
            "ужатие кегля тут не поможет, текст переносится по ширине бокса"
        )
        for defect in report.occluded[:show]:
            lines.append(f"            закрыто: {defect.describe()}")

    for defect in report.unresolved[:show]:
        lines.append(f"            осталось: {defect.describe()}")
    if len(report.unresolved) > show:
        lines.append(f"            …и ещё {len(report.unresolved) - show}")
    return lines


def write_json(report: VerifyReport, path: str) -> str:
    """Отчёт на диск. Ключи отсортированы: файл должен быть сравним построчно."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report.to_json(), fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return path
