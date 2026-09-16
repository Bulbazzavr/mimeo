"""Единицы OOXML. DOM-GEOM §1, DOM-TEXT §4.

Внутри системы всё в EMU целыми числами. Конвертация — только на границе вывода.
"""

from __future__ import annotations

from math import gcd

EMU_PER_INCH = 914400
EMU_PER_POINT = 12700
EMU_PER_CM = 360000
EMU_PER_PX_96DPI = 9525

#: Шаг квантования при выводе сетки: ~0.02 дюйма. DOM-GEOM §6.
GRID_QUANT_EMU = 18288


def emu_to_pt(emu: int) -> float:
    return emu / EMU_PER_POINT


def pt_to_emu(pt: float) -> int:
    return round(pt * EMU_PER_POINT)


def emu_to_inch(emu: int) -> float:
    return emu / EMU_PER_INCH


def emu_to_px(emu: int, dpi: int = 96) -> float:
    return emu / EMU_PER_INCH * dpi


def sz_to_pt(sz: str | int | None) -> float | None:
    """Атрибут `sz` — сотые доли пункта. DOM-TEXT §4."""
    if sz is None:
        return None
    return int(sz) / 100.0


def spc_to_pt(spc: str | int | None) -> float:
    """Атрибут `spc` (трекинг) — сотые доли пункта, может быть отрицательным."""
    if spc is None:
        return 0.0
    return int(spc) / 100.0


def pct_to_ratio(val: str | int | None) -> float | None:
    """Тысячные доли процента: `60000` -> 0.6."""
    if val is None:
        return None
    return int(val) / 100000.0


def angle_to_deg(val: str | int | None) -> float:
    """`rot` — 60000-е доли градуса. DOM-GEOM §2."""
    if val is None:
        return 0.0
    return int(val) / 60000.0


def quantize(value: int, step: int = GRID_QUANT_EMU) -> int:
    """Округление к сетке квантования. Без него мода краёв размазывается."""
    if step <= 0:
        return value
    return int(round(value / step)) * step


def aspect_ratio(cx: int, cy: int) -> str:
    """`12192000 x 6858000` -> `16:9`."""
    if cx <= 0 or cy <= 0:
        return "?"
    d = gcd(cx, cy)
    w, h = cx // d, cy // d
    # Приводим к обозримому виду: длинные дроби сводим к ближайшему из типовых.
    if w > 64 or h > 64:
        known = ((16, 9), (4, 3), (16, 10), (3, 2), (1, 1), (9, 16), (210, 297))
        target = cx / cy
        w, h = min(known, key=lambda p: abs(p[0] / p[1] - target))
    return f"{w}:{h}"
