"""Стадия ANALYZE: шаблон -> дизайн-система и библиотека паттернов.

См. docs/architecture/00-overview.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..model import DesignSystem, PatternLibrary
from ..opc.package import Package
from .deck import load_deck
from .patterns import build_pattern_library
from .shapes import analyze_slides
from .tokens import build_design_system

__all__ = ["analyze_template", "analyze_package", "Analysis", "DesignSystem", "PatternLibrary"]


@dataclass(frozen=True)
class Analysis:
    """Оба артефакта стадии. Между стадиями они ходят файлами — ADR-0003."""

    design_system: DesignSystem
    patterns: PatternLibrary


def analyze_package(pkg: Package, filename: str | None = None) -> Analysis:
    name = filename or os.path.basename(pkg.path)
    deck = load_deck(pkg)
    slides, unhandled = analyze_slides(deck)
    design_system = build_design_system(deck, slides, unhandled, name)
    patterns = build_pattern_library(deck, slides, design_system, name)
    return Analysis(design_system=design_system, patterns=patterns)


def analyze_template(path: str | os.PathLike[str]) -> Analysis:
    """Разбирает шаблон. Без сети и без обучения — ADR-0002."""
    with Package(os.fspath(path)) as pkg:
        return analyze_package(pkg, os.path.basename(os.fspath(path)))
