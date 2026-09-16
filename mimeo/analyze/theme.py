"""Разбор темы: цветовая схема, схема шрифтов. DOM-COLOR §2, DOM-TEXT §3."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from xml.etree import ElementTree as ET

from ..opc.package import Package
from ..oxml.ns import qn
from .color import THEME_ROLES, ColorContext, resolve_color


@dataclass(frozen=True)
class Theme:
    part: str
    name: str
    scheme: Mapping[str, str]          # роль темы -> "#RRGGBB"
    major_latin: str | None
    minor_latin: str | None
    major_scripts: Mapping[str, str]   # код письменности -> гарнитура
    minor_scripts: Mapping[str, str]

    def typeface(self, token: str | None) -> str | None:
        """Разрешает токены `+mj-lt` / `+mn-lt`. DOM-TEXT §3."""
        if not token:
            return None
        if token in ("+mj-lt", "+mj-ea", "+mj-cs"):
            return self.major_latin
        if token in ("+mn-lt", "+mn-ea", "+mn-cs"):
            return self.minor_latin
        return token

    def script_face(self, token: str | None, script: str = "Cyrl") -> str | None:
        """Гарнитура для конкретной письменности, если тема её подменяет."""
        if token in ("+mj-lt", "+mj-ea", "+mj-cs"):
            return self.major_scripts.get(script)
        if token in ("+mn-lt", "+mn-ea", "+mn-cs"):
            return self.minor_scripts.get(script)
        return None


def _font_block(block: ET.Element | None) -> tuple[str | None, dict[str, str]]:
    if block is None:
        return None, {}
    latin_el = block.find(qn("a:latin"))
    latin = latin_el.get("typeface") if latin_el is not None else None
    scripts = {}
    for f in block.findall(qn("a:font")):
        s, tf = f.get("script"), f.get("typeface")
        if s and tf:
            scripts[s] = tf
    return (latin or None), scripts


def load_theme(pkg: Package, part: str) -> Theme:
    root = pkg.xml(part)
    name = root.get("name") or ""
    elements = root.find(qn("a:themeElements"))

    scheme: dict[str, str] = {}
    if elements is not None:
        clr_scheme = elements.find(qn("a:clrScheme"))
        if clr_scheme is not None:
            # В схеме темы стоят только srgbClr и sysClr — schemeClr там запрещён,
            # поэтому пустого контекста достаточно. DOM-COLOR §2.
            bare = ColorContext()
            for role in THEME_ROLES:
                holder = clr_scheme.find(qn(f"a:{role}"))
                if holder is None:
                    continue
                resolved = resolve_color(next(iter(holder), None), bare)
                if resolved is not None:
                    scheme[role] = resolved.hex

    major_latin = minor_latin = None
    major_scripts: dict[str, str] = {}
    minor_scripts: dict[str, str] = {}
    if elements is not None:
        font_scheme = elements.find(qn("a:fontScheme"))
        if font_scheme is not None:
            major_latin, major_scripts = _font_block(font_scheme.find(qn("a:majorFont")))
            minor_latin, minor_scripts = _font_block(font_scheme.find(qn("a:minorFont")))

    return Theme(
        part=part,
        name=name,
        scheme=MappingProxyType(scheme),
        major_latin=major_latin,
        minor_latin=minor_latin,
        major_scripts=MappingProxyType(major_scripts),
        minor_scripts=MappingProxyType(minor_scripts),
    )
