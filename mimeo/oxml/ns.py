"""Пространства имён OOXML и типы связей.

DOM-PKG §3 — про типы связей.
"""

from __future__ import annotations

NS: dict[str, str] = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
}

_RT_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class RT:
    """Типы связей. Идти надо по типу, а не по пути — DOM-PKG §2."""

    OFFICE_DOCUMENT = f"{_RT_BASE}/officeDocument"
    SLIDE = f"{_RT_BASE}/slide"
    SLIDE_MASTER = f"{_RT_BASE}/slideMaster"
    SLIDE_LAYOUT = f"{_RT_BASE}/slideLayout"
    NOTES_SLIDE = f"{_RT_BASE}/notesSlide"
    NOTES_MASTER = f"{_RT_BASE}/notesMaster"
    THEME = f"{_RT_BASE}/theme"
    IMAGE = f"{_RT_BASE}/image"
    CHART = f"{_RT_BASE}/chart"
    FONT = f"{_RT_BASE}/font"


def qn(tag: str) -> str:
    """`a:solidFill` -> `{http://…/main}solidFill` (нотация Кларка)."""
    prefix, _, local = tag.partition(":")
    if not local:
        return tag
    return f"{{{NS[prefix]}}}{local}"


def local_name(tag: str) -> str:
    """`{uri}solidFill` -> `solidFill`."""
    return tag.rpartition("}")[2]
