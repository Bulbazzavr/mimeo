"""Оценка вместимости текстового блока. DOM-TEXT §5, §6, §9.

Отвечает на вопрос «сколько символов влезет в этот прямоугольник при этом
кегле». Этим числом стадия PLAN ограничивает языковую модель (`ADR-0002`).

Точность здесь намеренно грубая — почему именно так, см. `ADR-0008`. Коротко:
метрик шрифта у нас нет и не будет, а промах в 15% гасится стадией VERIFY,
которая ловит переполнение по растру и чинит его (`ADR-0005`).

Модуль не знает ни про паттерны, ни про слайды: на вход прямоугольник и
типографика, на выход числа.
"""

from __future__ import annotations

from ..model import Capacity
from ..oxml.units import EMU_PER_POINT

#: Средняя ширина знака в долях кегля. Для гуманистических гротесков смешанного
#: набора это 0.50-0.55 em и для латиницы, и для кириллицы. Прописные шире.
_EM_MIXED = 0.52
_EM_ALLCAPS = 0.66
_EM_BOLD_FACTOR = 1.03

#: Интерлиньяж по умолчанию, когда `a:lnSpc` не задан.
_DEFAULT_LINE_SPACING = 1.2

#: Скидка на то, что строки рвутся по словам и правый край недоиспользуется.
_RAGGED_EDGE = 0.92

#: Во сколько раз дизайнерский замысел мягче геометрического предела. Донор с
#: заголовком в 20 знаков не означает лимит в 20: это означает «примерно так».
_TARGET_SLACK = 1.6


def average_glyph_emu(size_pt: float, caps: str | None = None, bold: bool = False) -> float:
    """Средняя ширина знака в EMU."""
    em = _EM_ALLCAPS if caps == "all" else _EM_MIXED
    if bold:
        em *= _EM_BOLD_FACTOR
    return size_pt * EMU_PER_POINT * em


def estimate(
    *,
    cx_emu: int,
    cy_emu: int,
    size_pt: float,
    insets: tuple[int, int, int, int] = (91440, 45720, 91440, 45720),
    line_spacing_pct: float | None = None,
    caps: str | None = None,
    bold: bool = False,
    wrap: str | None = None,
    donor_chars: int | None = None,
    donor_items: int | None = None,
) -> Capacity:
    """Вместимость прямоугольника. Все размеры в EMU, кегль в пунктах."""
    left, top, right, bottom = insets
    usable_w = max(1, cx_emu - left - right)
    usable_h = max(1, cy_emu - top - bottom)

    glyph = max(1.0, average_glyph_emu(size_pt, caps, bold))
    chars_per_line = max(1, int(usable_w / glyph))

    spacing = (line_spacing_pct / 100.0) if line_spacing_pct else _DEFAULT_LINE_SPACING
    line_h = max(1.0, size_pt * EMU_PER_POINT * spacing)
    max_lines = max(1, int(usable_h / line_h))

    if wrap == "none":
        # Перенос выключен: сколько бы ни было места по высоте, строка одна.
        max_lines = 1

    geometric = max(1, int(chars_per_line * max_lines * _RAGGED_EDGE))

    # Донор — настоящий слайд, сделанный дизайнером: текст в нём заведомо
    # помещается. Значит оценка не имеет права быть меньше того, что там уже
    # написано. Это и есть вся калибровка, которая нам нужна — ADR-0008.
    max_chars = max(geometric, donor_chars or 0, 1)

    target = None
    if donor_chars:
        target = max(1, min(max_chars, int(donor_chars * _TARGET_SLACK)))

    max_items = None
    if donor_items and donor_items > 1:
        lines_per_item = max(1, max_lines // donor_items)
        max_items = max(1, min(donor_items + 2, max_lines // max(1, lines_per_item)))

    basis = (
        f"{usable_w // 12700}x{usable_h // 12700} pt полезной площади при {size_pt:g} pt, "
        f"{chars_per_line} знаков в строке, {max_lines} строк, оценка {geometric}"
        + (f", в доноре {donor_chars}" if donor_chars else "")
    )
    return Capacity(
        max_chars=max_chars,
        max_lines=max_lines,
        chars_per_line=chars_per_line,
        target_chars=target,
        max_items=max_items,
        donor_chars=donor_chars,
        basis=basis,
    )
