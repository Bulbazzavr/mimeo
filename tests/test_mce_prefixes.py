"""Пространства имён Markup Compatibility переживают пересериализацию.

Регрессия 12 сентября: два собранных файла из одиннадцати не открывались в
PowerPoint (`E_FAIL`), потому что `ElementTree` не объявляет префикс, который
встречается только в **значении** атрибута — `mc:Ignorable="mv"`,
`mc:Choice Requires="v"`. XML при этом остаётся синтаксически корректным, и ни
`python-pptx`, ни наш разбор, ни проверка целостности пакета дефекта не видят.

Подробности — `WORKLOG/2026-09-12-mce-prefixes.md`, `DOM-PKG §8`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from mimeo.compose.package import register_prefixes, serialize

MV = "urn:schemas-microsoft-com:mac:vml"
VML = "urn:schemas-microsoft-com:vml"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _declared(raw: bytes) -> dict[str, str]:
    tag = re.search(rb"<p:sld[^>]*>", raw).group(0).decode()
    return dict(re.findall(r'xmlns:([A-Za-z0-9_]+)="([^"]*)"', tag))


def _roundtrip(source: str) -> bytes:
    raw = source.encode("utf-8")
    register_prefixes(raw)
    return serialize(ET.fromstring(raw))


def test_prefix_named_only_in_ignorable_survives():
    """`mc:Ignorable="mv"` — префикс `mv` больше нигде не встречается."""
    out = _roundtrip(
        f'<p:sld xmlns:p="{P}" xmlns:mc="{MC}" xmlns:mv="{MV}"'
        f' mc:Ignorable="mv" mc:PreserveAttributes="mv:*"><p:cSld/></p:sld>'
    )
    assert _declared(out).get("mv") == MV


def test_prefix_named_only_in_requires_survives():
    """`mc:Choice Requires="v"` — так падал второй из двух файлов."""
    out = _roundtrip(
        f'<p:sld xmlns:p="{P}" xmlns:mc="{MC}" xmlns:v="{VML}">'
        f'<mc:AlternateContent><mc:Choice Requires="v"><p:sp/></mc:Choice>'
        f"</mc:AlternateContent></p:sld>"
    )
    assert _declared(out).get("v") == VML


def test_no_duplicate_declaration_when_prefix_is_also_used_in_names():
    """Префикс, встречающийся и в именах, объявляется ровно один раз.

    Иначе вышло бы два одинаковых `xmlns:`, и это уже не корректный XML.
    """
    out = _roundtrip(
        f'<p:sld xmlns:p="{P}" xmlns:mc="{MC}" xmlns:v="{VML}">'
        f'<mc:AlternateContent><mc:Choice Requires="v"><v:shape/></mc:Choice>'
        f"</mc:AlternateContent></p:sld>"
    )
    tag = re.search(rb"<p:sld[^>]*>", out).group(0).decode()
    assert tag.count('xmlns:v=') == 1


def test_document_without_markup_compatibility_is_untouched():
    """Без атрибутов MCE сериализация не должна ничего добавлять."""
    source = f'<p:sld xmlns:p="{P}"><p:cSld/></p:sld>'
    out = _roundtrip(source)
    assert b"xmlns:mv" not in out and b"xmlns:v=" not in out


def test_unknown_prefix_is_not_invented():
    """Если объявления префикса нигде не встречалось, выдумывать URI нельзя."""
    out = _roundtrip(
        f'<p:sld xmlns:p="{P}" xmlns:mc="{MC}" mc:Ignorable="zz"><p:cSld/></p:sld>'
    )
    assert b"xmlns:zz" not in out
