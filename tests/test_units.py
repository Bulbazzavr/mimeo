"""Единицы. DOM-GEOM §1, DOM-TEXT §4."""

from __future__ import annotations

import pytest

from mimeo.oxml.units import (
    EMU_PER_INCH,
    GRID_QUANT_EMU,
    angle_to_deg,
    aspect_ratio,
    emu_to_inch,
    emu_to_pt,
    pct_to_ratio,
    pt_to_emu,
    quantize,
    spc_to_pt,
    sz_to_pt,
)


def test_inch_and_point():
    assert emu_to_inch(EMU_PER_INCH) == 1.0
    assert emu_to_pt(EMU_PER_INCH) == pytest.approx(72.0)
    assert pt_to_emu(18) == 228600


def test_sz_is_hundredths_of_a_point():
    assert sz_to_pt("1800") == 18.0
    assert sz_to_pt(4400) == 44.0
    assert sz_to_pt(None) is None


def test_spc_can_be_negative():
    assert spc_to_pt("-25") == -0.25
    assert spc_to_pt(None) == 0.0


def test_pct_is_thousandths_of_a_percent():
    assert pct_to_ratio("60000") == pytest.approx(0.6)
    assert pct_to_ratio(None) is None


def test_rot_is_sixtythousandths_of_a_degree():
    assert angle_to_deg("5400000") == pytest.approx(90.0)
    assert angle_to_deg(None) == 0.0


def test_quantize_snaps_to_grid():
    assert quantize(838200) == 841248
    assert quantize(0) == 0
    assert quantize(GRID_QUANT_EMU * 3) == GRID_QUANT_EMU * 3


def test_aspect_ratio_of_standard_canvases():
    assert aspect_ratio(12192000, 6858000) == "16:9"
    assert aspect_ratio(9144000, 6858000) == "4:3"
    assert aspect_ratio(9144000, 5143500) == "16:9"


def test_aspect_ratio_of_odd_canvas_falls_back_to_nearest_known():
    assert aspect_ratio(7559675, 10691813) in {"210:297", "3:2", "1:1", "9:16"}
