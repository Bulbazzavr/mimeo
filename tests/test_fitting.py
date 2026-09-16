"""Оценка вместимости. DOM-TEXT §6, §10, ADR-0008."""

from __future__ import annotations

from mimeo.analyze.fitting import estimate
from mimeo.oxml.units import EMU_PER_INCH


def _box(w_in: float, h_in: float) -> dict:
    return {"cx_emu": int(w_in * EMU_PER_INCH), "cy_emu": int(h_in * EMU_PER_INCH)}


def test_wider_box_holds_more():
    narrow = estimate(**_box(2, 1), size_pt=18)
    wide = estimate(**_box(8, 1), size_pt=18)
    assert wide.chars_per_line > narrow.chars_per_line * 3


def test_larger_type_holds_less():
    small = estimate(**_box(6, 3), size_pt=12)
    large = estimate(**_box(6, 3), size_pt=48)
    assert large.max_chars < small.max_chars / 8


def test_insets_reduce_capacity():
    default = estimate(**_box(4, 2), size_pt=18)
    padded = estimate(**_box(4, 2), size_pt=18, insets=(457200, 457200, 457200, 457200))
    assert padded.chars_per_line < default.chars_per_line


def test_wrap_none_collapses_to_single_line():
    wrapped = estimate(**_box(4, 3), size_pt=18)
    single = estimate(**_box(4, 3), size_pt=18, wrap="none")
    assert wrapped.max_lines > 1
    assert single.max_lines == 1


def test_all_caps_fits_fewer_characters():
    mixed = estimate(**_box(6, 1), size_pt=18)
    caps = estimate(**_box(6, 1), size_pt=18, caps="all")
    assert caps.chars_per_line < mixed.chars_per_line


def test_donor_text_is_a_floor_not_a_ceiling():
    """Донор — настоящий слайд дизайнера: текст в нём заведомо влезает.

    Оценка не имеет права утверждать обратное. ADR-0008.
    """
    tight = estimate(**_box(0.6, 0.6), size_pt=72, donor_chars=5)
    assert tight.max_chars >= 5
    assert tight.donor_chars == 5


def test_target_follows_designer_intent_within_the_hard_limit():
    cap = estimate(**_box(8, 2), size_pt=18, donor_chars=40)
    assert cap.target_chars is not None
    assert 40 <= cap.target_chars <= cap.max_chars


def test_basis_is_human_readable():
    cap = estimate(**_box(6, 2), size_pt=18, donor_chars=30)
    assert "знаков в строке" in cap.basis and "в доноре 30" in cap.basis
