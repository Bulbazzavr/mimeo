"""Сквозной прогон стадии ANALYZE на синтетическом шаблоне.

Фикстура специально задевает наследование геометрии и текста, косвенность
цветовой карты и трансформации цвета — см. tests/fixtures/build_fixture.py.
"""

from __future__ import annotations

import json
import os

import pytest

from mimeo.analyze import analyze_template
from mimeo.oxml.units import GRID_QUANT_EMU

CONTRACT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "contracts",
    "design-system.schema.json",
)


@pytest.fixture(scope="module")
def design_system(template_path):
    return analyze_template(template_path).design_system


# --- структура ---------------------------------------------------------


def test_package_graph_is_read(design_system):
    src = design_system.source
    assert (src.masters, src.layouts, src.slides) == (1, 1, 2)
    assert len(src.sha256) == 64


def test_canvas_size(design_system):
    assert design_system.slide.cx_emu == 12192000
    assert design_system.slide.cy_emu == 6858000
    assert design_system.slide.aspect == "16:9"
    assert design_system.slide.type == "screen16x9"


# --- палитра -----------------------------------------------------------


def test_theme_palette_carries_all_twelve_roles(design_system):
    roles = {c.role for c in design_system.theme_palette}
    assert "accent1" in roles and "dk1" in roles and "folHlink" in roles
    accent1 = next(c for c in design_system.theme_palette if c.role == "accent1")
    assert accent1.hex == "#4472C4"


def test_sys_clr_in_theme_resolves_via_last_clr(design_system):
    dk1 = next(c for c in design_system.theme_palette if c.role == "dk1")
    assert dk1.hex == "#000000"


def test_observed_palette_is_sorted_by_frequency(design_system):
    counts = [c.count for c in design_system.observed_palette]
    assert counts == sorted(counts, reverse=True)


def test_text_colour_comes_from_master_through_clr_map(design_system):
    """Цвет текста задан как schemeClr tx1 -> clrMap -> dk1 -> sysClr. DOM-COLOR §2."""
    black = next(c for c in design_system.observed_palette if c.hex == "#000000")
    assert "text" in black.contexts
    assert black.count == 7  # 2 заголовка + 3 пункта + число + подпись


def test_lum_transforms_produce_a_new_observed_colour(design_system):
    """Заливка карточки — accent1 + lumMod/lumOff, то есть не сам accent1."""
    fills = [c for c in design_system.observed_palette if "fill" in c.contexts]
    assert fills, "заливка карточки не найдена"
    card = fills[0]
    assert card.theme_role == "accent1"
    assert card.hex != "#4472C4"


# --- типографика -------------------------------------------------------


def test_type_scale_roles(design_system):
    by_role = {t.role: t for t in design_system.type_scale}
    assert set(by_role) >= {"body", "title", "metric"}


def test_body_inherits_size_and_face_from_master_body_style(design_system):
    """У прогонов на слайде нет ни sz, ни latin — всё приходит по цепочке."""
    body = next(t for t in design_system.type_scale if t.role == "body")
    assert body.size_pt == 18.0
    assert body.latin == "Golos Text"      # +mn-lt разрешён через тему
    assert body.cyrl == "Golos Text"       # подмена по script="Cyrl"
    assert body.count == 3


def test_title_uses_major_font_and_title_style_size(design_system):
    title = next(t for t in design_system.type_scale if t.role == "title")
    assert title.size_pt == 44.0
    assert title.latin == "Unbounded"
    assert title.count == 2


def test_large_numeric_run_is_classified_as_metric(design_system):
    metric = next(t for t in design_system.type_scale if t.role == "metric")
    assert metric.size_pt == 80.0
    assert metric.bold is True
    assert metric.align == "ctr"
    assert "84%" in metric.examples


# --- сетка и формы -----------------------------------------------------


def test_grid_margins_recovered_from_inherited_geometry(design_system):
    """Плейсхолдеры слайдов не имеют своего xfrm — DOM-GEOM §5."""
    grid = design_system.grid
    assert grid.samples == 5
    assert abs(grid.margin_left_emu - 838200) <= GRID_QUANT_EMU
    assert abs(grid.margin_right_emu - 838200) <= GRID_QUANT_EMU
    assert abs(grid.margin_top_emu - 457200) <= GRID_QUANT_EMU


def test_shape_vocabulary_keeps_rounded_card(design_system):
    geoms = {s.geom for s in design_system.shapes}
    assert "roundRect" in geoms
    card = next(s for s in design_system.shapes if s.geom == "roundRect")
    assert card.fill_kind == "solid"
    assert card.line_w_emu == 12700
    assert card.corner_radius_pct == pytest.approx(0.08)


def test_decorationless_shapes_are_filtered_out(design_system):
    """Подпись с noFill и без обводки в словарь форм не попадает. DOM-GEOM §7."""
    assert len(design_system.shapes) == 1


# --- артефакт ----------------------------------------------------------


def test_nothing_left_unhandled_on_the_fixture(design_system):
    assert design_system.evidence.unhandled == ()


def test_artifact_matches_contract(design_system):
    jsonschema = pytest.importorskip("jsonschema")
    with open(CONTRACT, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.Draft202012Validator(schema).validate(design_system.to_json())


def test_analyze_is_deterministic(template_path):
    """ADR-0003: один шаблон — байт в байт тот же JSON."""
    first = json.dumps(analyze_template(template_path).design_system.to_json(), ensure_ascii=False, sort_keys=False)
    second = json.dumps(analyze_template(template_path).design_system.to_json(), ensure_ascii=False, sort_keys=False)
    assert first == second
