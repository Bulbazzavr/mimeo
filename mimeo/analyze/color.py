"""Разрешение цвета DrawingML. DOM-COLOR.

Три слоя: вид элемента (§1), косвенность через карту цветов (§2),
трансформации (§3).
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
from typing import Mapping
from xml.etree import ElementTree as ET

from ..oxml.ns import local_name, qn

#: Роли схемы темы. DOM-COLOR §2.
THEME_ROLES = (
    "dk1", "lt1", "dk2", "lt2",
    "accent1", "accent2", "accent3", "accent4", "accent5", "accent6",
    "hlink", "folHlink",
)

#: Роли карты цветов слайда. Имена отличаются от ролей темы — это не опечатка.
MAP_ROLES = (
    "bg1", "tx1", "bg2", "tx2",
    "accent1", "accent2", "accent3", "accent4", "accent5", "accent6",
    "hlink", "folHlink",
)

_COLOR_TAGS = {
    qn("a:srgbClr"),
    qn("a:schemeClr"),
    qn("a:sysClr"),
    qn("a:prstClr"),
    qn("a:scrgbClr"),
    qn("a:hslClr"),
}

#: Подмножество именованных цветов. Остальные попадают в evidence.unhandled.
_PRESET = {
    "black": "000000", "white": "FFFFFF", "red": "FF0000", "green": "008000",
    "blue": "0000FF", "yellow": "FFFF00", "cyan": "00FFFF", "magenta": "FF00FF",
    "gray": "808080", "grey": "808080", "darkGray": "A9A9A9", "lightGray": "D3D3D3",
    "silver": "C0C0C0", "maroon": "800000", "olive": "808000", "navy": "000080",
    "purple": "800080", "teal": "008080", "lime": "00FF00", "aqua": "00FFFF",
    "fuchsia": "FF00FF", "orange": "FFA500", "darkBlue": "00008B",
    "darkRed": "8B0000", "darkGreen": "006400", "lightBlue": "ADD8E6",
    "dkGray": "A9A9A9", "ltGray": "D3D3D3", "medGray": "A0A0A0",
    "transparent": "FFFFFF",
}

_SYS = {"windowText": "000000", "window": "FFFFFF", "highlight": "0078D7",
        "captionText": "000000", "btnFace": "F0F0F0", "btnText": "000000"}


@dataclass(frozen=True)
class ColorContext:
    """Что нужно, чтобы разрешить `schemeClr`. DOM-COLOR §2."""

    scheme: Mapping[str, str] = field(default_factory=dict)   # роль темы -> "#RRGGBB"
    clr_map: Mapping[str, str] = field(default_factory=dict)  # роль слайда -> роль темы
    ph_hex: str | None = None                                 # значение для val="phClr"


# --- вспомогательное ----------------------------------------------------


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    s = h.lstrip("#")
    return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0)


def rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#{:02X}{:02X}{:02X}".format(
        round(_clamp(r) * 255), round(_clamp(g) * 255), round(_clamp(b) * 255)
    )


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(c: float) -> float:
    c = _clamp(c)
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def _pct(el: ET.Element) -> float | None:
    v = el.get("val")
    return None if v is None else int(v) / 100000.0


# --- разбор базового цвета ---------------------------------------------


def _base_color(
    el: ET.Element, ctx: ColorContext, unhandled: list[tuple[str, str]] | None
) -> tuple[tuple[float, float, float], str | None] | None:
    """Возвращает (rgb, роль темы) либо None, если цвет не распознан."""
    tag = local_name(el.tag)

    if tag == "srgbClr":
        val = el.get("val")
        return (hex_to_rgb(val), None) if val else None

    if tag == "sysClr":
        val = el.get("lastClr") or _SYS.get(el.get("val") or "", None)
        return (hex_to_rgb(val), None) if val else None

    if tag == "prstClr":
        name = el.get("val") or ""
        val = _PRESET.get(name)
        if val is None:
            if unhandled is not None:
                unhandled.append(("prstClr", name))
            return None
        return (hex_to_rgb(val), None)

    if tag == "scrgbClr":
        # Компоненты линейные, в тысячных долях процента. DOM-COLOR §1.
        r = int(el.get("r") or 0) / 100000.0
        g = int(el.get("g") or 0) / 100000.0
        b = int(el.get("b") or 0) / 100000.0
        return ((linear_to_srgb(r), linear_to_srgb(g), linear_to_srgb(b)), None)

    if tag == "hslClr":
        h = int(el.get("hue") or 0) / 60000.0 / 360.0
        s = int(el.get("sat") or 0) / 100000.0
        lum = int(el.get("lum") or 0) / 100000.0
        r, g, b = colorsys.hls_to_rgb(h % 1.0, _clamp(lum), _clamp(s))
        return ((r, g, b), None)

    if tag == "schemeClr":
        val = el.get("val") or ""
        if val == "phClr":
            if ctx.ph_hex:
                return (hex_to_rgb(ctx.ph_hex), None)
            if unhandled is not None:
                unhandled.append(("phClr", "нет внешнего цвета стиля"))
            return None
        # Косвенность: роль слайда -> роль темы -> цвет. DOM-COLOR §2.
        theme_role = ctx.clr_map.get(val, val)
        hexval = ctx.scheme.get(theme_role)
        if hexval is None:
            if unhandled is not None:
                unhandled.append(("schemeClr", val))
            return None
        return (hex_to_rgb(hexval), theme_role)

    return None


# --- трансформации ------------------------------------------------------


def _apply_transforms(
    rgb: tuple[float, float, float], alpha: float, el: ET.Element
) -> tuple[tuple[float, float, float], float]:
    """Применяются в порядке появления в документе. DOM-COLOR §3."""
    r, g, b = rgb
    for child in el:
        tag = local_name(child.tag)
        v = _pct(child)

        if tag == "lumMod" and v is not None:
            h, lum, s = colorsys.rgb_to_hls(r, g, b)
            r, g, b = colorsys.hls_to_rgb(h, _clamp(lum * v), s)
        elif tag == "lumOff" and v is not None:
            h, lum, s = colorsys.rgb_to_hls(r, g, b)
            r, g, b = colorsys.hls_to_rgb(h, _clamp(lum + v), s)
        elif tag == "satMod" and v is not None:
            h, lum, s = colorsys.rgb_to_hls(r, g, b)
            r, g, b = colorsys.hls_to_rgb(h, lum, _clamp(s * v))
        elif tag == "hueMod" and v is not None:
            h, lum, s = colorsys.rgb_to_hls(r, g, b)
            r, g, b = colorsys.hls_to_rgb((h * v) % 1.0, lum, s)
        elif tag == "shade" and v is not None:
            # В линейном RGB, с гамма-коррекцией. Пропуск коррекции даёт
            # заметно более светлый результат. DOM-COLOR §3.
            r, g, b = (linear_to_srgb(srgb_to_linear(c) * v) for c in (r, g, b))
        elif tag == "tint" and v is not None:
            r, g, b = (
                linear_to_srgb(srgb_to_linear(c) * v + (1.0 - v)) for c in (r, g, b)
            )
        elif tag == "alpha" and v is not None:
            alpha = _clamp(v)
        elif tag == "alphaMod" and v is not None:
            alpha = _clamp(alpha * v)
        elif tag == "gamma":
            r, g, b = (srgb_to_linear(c) for c in (r, g, b))
        elif tag == "invGamma":
            r, g, b = (linear_to_srgb(c) for c in (r, g, b))
        elif tag == "inv":
            r, g, b = (1.0 - c for c in (r, g, b))
        elif tag == "gray":
            y = 0.299 * r + 0.587 * g + 0.114 * b
            r = g = b = y
        elif tag == "comp" or tag == "lum" or tag == "sat" or tag == "hue":
            # Абсолютные варианты встречаются крайне редко; фиксируем как есть.
            pass

    return (r, g, b), alpha


# --- публичное ----------------------------------------------------------


@dataclass(frozen=True)
class ResolvedColor:
    hex: str
    alpha: float
    theme_role: str | None


def find_color_child(parent: ET.Element | None) -> ET.Element | None:
    """Первый дочерний элемент-цвет: `a:srgbClr`, `a:schemeClr` и т.д."""
    if parent is None:
        return None
    for child in parent:
        if child.tag in _COLOR_TAGS:
            return child
    return None


def resolve_color(
    el: ET.Element | None,
    ctx: ColorContext,
    unhandled: list[tuple[str, str]] | None = None,
) -> ResolvedColor | None:
    """Полное разрешение цветового элемента до `#RRGGBB` + alpha."""
    if el is None:
        return None
    base = _base_color(el, ctx, unhandled)
    if base is None:
        return None
    rgb, theme_role = base
    rgb, alpha = _apply_transforms(rgb, 1.0, el)
    return ResolvedColor(hex=rgb_to_hex(*rgb), alpha=alpha, theme_role=theme_role)


def resolve_fill_color(
    holder: ET.Element | None,
    ctx: ColorContext,
    unhandled: list[tuple[str, str]] | None = None,
) -> ResolvedColor | None:
    """Разрешает цвет внутри контейнера вроде `a:solidFill`."""
    return resolve_color(find_color_child(holder), ctx, unhandled)
