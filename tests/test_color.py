"""Разрешение цвета. DOM-COLOR."""

from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from mimeo.analyze.color import (
    ColorContext,
    hex_to_rgb,
    linear_to_srgb,
    resolve_color,
    rgb_to_hex,
    srgb_to_linear,
)

A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'

THEME = {
    "dk1": "#000000",
    "lt1": "#FFFFFF",
    "dk2": "#44546A",
    "lt2": "#E7E6E6",
    "accent1": "#4472C4",
}
CLR_MAP = {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2", "accent1": "accent1"}
CTX = ColorContext(scheme=THEME, clr_map=CLR_MAP)


def parse(inner: str) -> ET.Element:
    return ET.fromstring(f"<wrap {A}>{inner}</wrap>")[0]


def close(a: str, b: str, tol: int = 3) -> bool:
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return all(abs(x - y) * 255 <= tol for x, y in zip(ra, rb))


def test_srgb_direct():
    c = resolve_color(parse('<a:srgbClr val="4472C4"/>'), CTX)
    assert c is not None and c.hex == "#4472C4" and c.alpha == 1.0


def test_sys_color_uses_last_clr():
    c = resolve_color(parse('<a:sysClr val="windowText" lastClr="1A1A1A"/>'), CTX)
    assert c is not None and c.hex == "#1A1A1A"


def test_scheme_color_goes_through_clr_map():
    """`tx1` не равен `dk1` напрямую — между ними карта. DOM-COLOR §2."""
    c = resolve_color(parse('<a:schemeClr val="tx1"/>'), CTX)
    assert c is not None and c.hex == "#000000" and c.theme_role == "dk1"


def test_scheme_color_map_override_changes_result():
    swapped = ColorContext(scheme=THEME, clr_map={**CLR_MAP, "tx1": "accent1"})
    c = resolve_color(parse('<a:schemeClr val="tx1"/>'), swapped)
    assert c is not None and c.hex == "#4472C4" and c.theme_role == "accent1"


def test_lum_mod_off_matches_powerpoint_lighter_40():
    """«Accent 1, светлее 40%» = lumMod 60% + lumOff 40%. DOM-COLOR §4.

    PowerPoint показывает #8EA9DB. Наш результат отличается не более чем на
    единицу по каналу — разное округление при переводе в HSL, см. §7.
    """
    c = resolve_color(
        parse(
            '<a:schemeClr val="accent1">'
            '<a:lumMod val="60000"/><a:lumOff val="40000"/>'
            "</a:schemeClr>"
        ),
        CTX,
    )
    assert c is not None
    assert close(c.hex, "#8EA9DB"), c.hex
    assert c.theme_role == "accent1"


def test_lum_mod_alone_darkens():
    c = resolve_color(
        parse('<a:schemeClr val="accent1"><a:lumMod val="75000"/></a:schemeClr>'), CTX
    )
    assert c is not None
    assert sum(hex_to_rgb(c.hex)) < sum(hex_to_rgb("#4472C4"))


def test_shade_and_tint_move_in_opposite_directions():
    base = sum(hex_to_rgb("#4472C4"))
    shaded = resolve_color(parse('<a:srgbClr val="4472C4"><a:shade val="50000"/></a:srgbClr>'), CTX)
    tinted = resolve_color(parse('<a:srgbClr val="4472C4"><a:tint val="50000"/></a:srgbClr>'), CTX)
    assert shaded is not None and tinted is not None
    assert sum(hex_to_rgb(shaded.hex)) < base < sum(hex_to_rgb(tinted.hex))


def test_alpha_is_read_separately_from_colour():
    c = resolve_color(parse('<a:srgbClr val="4472C4"><a:alpha val="40000"/></a:srgbClr>'), CTX)
    assert c is not None and c.hex == "#4472C4" and c.alpha == pytest.approx(0.4)


def test_unknown_scheme_role_is_recorded_not_guessed():
    unhandled: list[tuple[str, str]] = []
    c = resolve_color(parse('<a:schemeClr val="nosuch"/>'), CTX, unhandled)
    assert c is None
    assert ("schemeClr", "nosuch") in unhandled


@pytest.mark.parametrize("value", [0.0, 0.02, 0.2, 0.5, 0.9, 1.0])
def test_gamma_round_trip(value: float):
    assert linear_to_srgb(srgb_to_linear(value)) == pytest.approx(value, abs=1e-9)


def test_hex_round_trip():
    assert rgb_to_hex(*hex_to_rgb("#3F8A2C")) == "#3F8A2C"
