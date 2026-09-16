"""Стадия VERIFY: измерить вёрстку собранного файла и починить то, что не влезло.

План — `PLAN-4.0`, обоснование петли — `ADR-0005`, выбор движка — `ADR-0013`.

Стадия **необязательна**: без Windows с PowerPoint она выключается, а пайплайн
собирает файл как прежде. Это инструмент качества, а не функция движка.
"""

from __future__ import annotations

from .detect import (
    DONOR_OVERFLOW,
    OVERFLOW_HEIGHT,
    OVERFLOW_WIDTH,
    Defect,
    Inspection,
    find_defects,
)
from .repair import (
    MIN_VISIBLE_PT,
    TARGET_RATIO,
    Repair,
    RepairResult,
    repair_plan,
)
from .loop import (
    DEFAULT_ROUNDS,
    STOP_FITS,
    STOP_FLOOR,
    STOP_NOT_MEASURED,
    STOP_ROUNDS,
    STOP_UNAVAILABLE,
    RoundRecord,
    VerifyOutcome,
    VerifyReport,
    verify_deck,
)
from .metrics import (
    TOLERANCE,
    Measurement,
    MeasurerUnavailable,
    ShapeMetric,
    available,
    measure,
)

__all__ = [
    "DEFAULT_ROUNDS",
    "STOP_FITS",
    "STOP_FLOOR",
    "STOP_NOT_MEASURED",
    "STOP_ROUNDS",
    "STOP_UNAVAILABLE",
    "RoundRecord",
    "VerifyOutcome",
    "VerifyReport",
    "verify_deck",
    "MIN_VISIBLE_PT",
    "Repair",
    "RepairResult",
    "TARGET_RATIO",
    "repair_plan",
    "DONOR_OVERFLOW",
    "Defect",
    "Inspection",
    "OVERFLOW_HEIGHT",
    "OVERFLOW_WIDTH",
    "find_defects",
    "TOLERANCE",
    "Measurement",
    "MeasurerUnavailable",
    "ShapeMetric",
    "available",
    "measure",
]
