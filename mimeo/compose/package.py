"""Запись OPC-пакета. Шаг Ш2 плана `PLAN-3.0`, обоснование в `ADR-0011`.

Пакет читается целиком в память, правится по частям и пишется заново. Файлы
презентаций — единицы мегабайт, экономить тут не на чем, зато операции
получаются простыми: удалить часть, добавить часть, заменить часть.

Два неочевидных требования, оба найдены логической проверкой плана, а не
отладкой:

* **фиксированная метка времени** у каждой записи ZIP. Без неё один и тот же
  план даёт разные файлы, и воспроизводимость сборки (`ADR-0003`) — фикция;
* **сохранение префиксов пространств имён** при сериализации. Реальные слайды
  содержат `mc:Ignorable="p14 a14"`, где префиксы указаны **текстом**. Если
  сериализатор переименует `p14` в `ns3`, файл станет невалидным.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from ..opc.package import Package
from ..oxml.ns import NS

def attr(value: str) -> str:
    """Значение XML-атрибута.

    `.rels` и `[Content_Types].xml` мы собираем строками, а не сериализатором,
    чтобы не потерять префиксы пространств имён. Плата за это — экранировать
    приходится самим. Найдено на шаблоне VK Education: в гиперссылке на Figma
    стоит `&amp;`, разбор превращает её в `&`, и запись без экранирования даёт
    **битый пакет** (`WORKLOG/2026-09-15-tz-templates.md`). На одиннадцати наших
    образцах такой ссылки не было.
    """
    return escape(value, {'"': "&quot;"})


#: Метка времени всех записей. Любая фиксированная подойдёт; важно, что она не
#: зависит от момента запуска.
FIXED_TIME = (2026, 1, 1, 0, 0, 0)

XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

_XMLNS = re.compile(rb'xmlns:([A-Za-z0-9_.-]+)="([^"]+)"')

_CT_NS = NS["ct"]
_REL_NS = NS["rel"]

#: Типы содержимого, которые мы создаём сами.
CT_SLIDE = (
    "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
)
RT_SLIDE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
RT_NOTES = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"
)
RT_LAYOUT = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"
)
RT_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


#: Префикс -> URI по всему, что встретилось при чтении пакета. `ElementTree`
#: хранит обратное отображение и наружу его не отдаёт, а нам нужно именно прямое:
#: по префиксу из `mc:Ignorable` восстановить объявление. См. `DOM-PKG §8`.
_SEEN_PREFIXES: dict[str, str] = {}

#: Атрибуты Markup Compatibility, чьи **значения** ссылаются на префиксы текстом.
#: Именно они теряются при пересериализации, см. `_restore_mce_prefixes`.
_MCE_ATTRS = ("Ignorable", "PreserveAttributes", "PreserveElements",
              "ProcessContent", "Requires")
_MCE_VALUES = re.compile(
    r'\b(?:' + "|".join(_MCE_ATTRS) + r')="([^"]*)"'
)
_ROOT_TAG = re.compile(r"<[A-Za-z_][\w.-]*(?::[A-Za-z_][\w.-]*)?(\s[^>]*)?>")


def register_prefixes(raw: bytes) -> None:
    """Регистрирует все префиксы, объявленные в документе.

    Иначе `ElementTree` выдумает свои (`ns0`, `ns1`), а атрибуты вроде
    `mc:Ignorable="p14 a14"` ссылаются на префиксы текстом и перестанут
    соответствовать объявлениям.
    """
    for prefix, uri in _XMLNS.findall(raw):
        name, url = prefix.decode(), uri.decode()
        _SEEN_PREFIXES.setdefault(name, url)
        try:
            ET.register_namespace(name, url)
        except ValueError:
            pass


def _restore_mce_prefixes(text: str) -> str:
    """Возвращает объявления префиксов, на которые ссылаются только значения.

    `ElementTree` объявляет лишь те пространства имён, что встретились в
    **именах** элементов и атрибутов. Markup Compatibility ссылается на префиксы
    иначе — текстом внутри значения: `mc:Ignorable="mv"`,
    `mc:Choice Requires="v"`. Если такой префикс больше нигде не встречается,
    объявление при пересериализации исчезает, а ссылка остаётся — и PowerPoint
    отвечает на файл `E_FAIL`, не открывая его вовсе.

    Найдено 12 сентября: два собранных файла из одиннадцати не открывались
    (`WORKLOG/2026-09-12-mce-prefixes.md`). Ни `python-pptx`, ни наш разбор, ни
    проверка целостности пакета этого не видят — XML остаётся синтаксически
    корректным. См. `DOM-PKG §8`.
    """
    match = _ROOT_TAG.search(text)
    if match is None:
        return text
    tag = match.group(0)

    wanted: set[str] = set()
    for value in _MCE_VALUES.findall(text):
        for token in value.split():
            prefix = token.split(":", 1)[0].strip()
            if prefix and prefix != "*":
                wanted.add(prefix)
    if not wanted:
        return text

    declared = {p.decode() for p, _ in _XMLNS.findall(tag.encode("utf-8"))}
    missing = sorted(p for p in wanted - declared if p in _SEEN_PREFIXES)
    if not missing:
        return text

    insert = "".join(f' xmlns:{p}="{_SEEN_PREFIXES[p]}"' for p in missing)
    # Корень может быть самозакрывающимся: вставлять надо перед `/>`, иначе
    # получится `<p:sld/ xmlns:v="...">` — синтаксический мусор.
    close = "/>" if tag.endswith("/>") else ">"
    patched = tag[: -len(close)].rstrip() + insert + close
    return text[: match.start()] + patched + text[match.end():]


def serialize(root: ET.Element) -> bytes:
    text = _restore_mce_prefixes(ET.tostring(root, encoding="unicode"))
    return XML_DECL.encode("utf-8") + text.encode("utf-8")


# --- связи --------------------------------------------------------------


@dataclass
class Rel:
    id: str
    type: str
    target: str
    external: bool = False


class Relationships:
    """Файл `.rels` одной части."""

    def __init__(self, rels: list[Rel] | None = None) -> None:
        self.items: list[Rel] = list(rels or [])

    @classmethod
    def parse(cls, raw: bytes) -> "Relationships":
        root = ET.fromstring(raw)
        out = []
        for el in root.findall(f"{{{_REL_NS}}}Relationship"):
            out.append(
                Rel(
                    id=el.get("Id") or "",
                    type=el.get("Type") or "",
                    target=el.get("Target") or "",
                    external=el.get("TargetMode") == "External",
                )
            )
        return cls(out)

    def next_id(self) -> str:
        used = {int(r.id[3:]) for r in self.items if r.id.startswith("rId") and r.id[3:].isdigit()}
        n = 1
        while n in used:
            n += 1
        return f"rId{n}"

    def add(self, type_: str, target: str, external: bool = False) -> str:
        rid = self.next_id()
        self.items.append(Rel(id=rid, type=type_, target=target, external=external))
        return rid

    def drop_type(self, type_: str) -> int:
        before = len(self.items)
        self.items = [r for r in self.items if r.type != type_]
        return before - len(self.items)

    def first_of(self, type_: str) -> Rel | None:
        return next((r for r in self.items if r.type == type_), None)

    def to_bytes(self) -> bytes:
        parts = [XML_DECL, f'<Relationships xmlns="{_REL_NS}">']
        for r in self.items:
            mode = ' TargetMode="External"' if r.external else ""
            parts.append(
                f'<Relationship Id="{attr(r.id)}" Type="{attr(r.type)}" '
                f'Target="{attr(r.target)}"{mode}/>'
            )
        parts.append("</Relationships>")
        return "".join(parts).encode("utf-8")


# --- типы содержимого ---------------------------------------------------


class ContentTypes:
    def __init__(self, defaults: dict[str, str], overrides: dict[str, str]) -> None:
        self.defaults = defaults
        self.overrides = overrides

    @classmethod
    def parse(cls, raw: bytes) -> "ContentTypes":
        root = ET.fromstring(raw)
        defaults, overrides = {}, {}
        for el in root.findall(f"{{{_CT_NS}}}Default"):
            defaults[(el.get("Extension") or "").lower()] = el.get("ContentType") or ""
        for el in root.findall(f"{{{_CT_NS}}}Override"):
            overrides[el.get("PartName") or ""] = el.get("ContentType") or ""
        return cls(defaults, overrides)

    def ensure_default(self, extension: str, content_type: str) -> None:
        self.defaults.setdefault(extension.lower(), content_type)

    def to_bytes(self) -> bytes:
        parts = [XML_DECL, f'<Types xmlns="{_CT_NS}">']
        for ext, ct in sorted(self.defaults.items()):
            parts.append(f'<Default Extension="{attr(ext)}" ContentType="{attr(ct)}"/>')
        for name, ct in sorted(self.overrides.items()):
            parts.append(f'<Override PartName="{attr(name)}" ContentType="{attr(ct)}"/>')
        parts.append("</Types>")
        return "".join(parts).encode("utf-8")


# --- пакет --------------------------------------------------------------


def rels_name(partname: str) -> str:
    directory, _, name = partname.rpartition("/")
    return f"{directory}/_rels/{name}.rels"


class PackageWriter:
    """Пакет, открытый на правку. Части адресуются именами с ведущим слэшем."""

    def __init__(self, source: Package) -> None:
        self.parts: dict[str, bytes] = {}
        for name in source.part_names:
            self.parts[name] = source.read(name)
        self.content_types = ContentTypes.parse(self.parts["/[Content_Types].xml"])

    # -- части --

    def has(self, partname: str) -> bool:
        return partname in self.parts

    def read(self, partname: str) -> bytes:
        return self.parts[partname]

    def write(self, partname: str, data: bytes, content_type: str | None = None) -> None:
        self.parts[partname] = data
        if content_type:
            self.content_types.overrides[partname] = content_type

    def remove(self, partname: str) -> None:
        """Удаляет часть вместе с её связями и записью о типе содержимого."""
        self.parts.pop(partname, None)
        self.parts.pop(rels_name(partname), None)
        self.content_types.overrides.pop(partname, None)

    def names(self, prefix: str = "", suffix: str = "") -> list[str]:
        return sorted(
            n for n in self.parts
            if n.startswith(prefix) and n.endswith(suffix) and "/_rels/" not in n
        )

    # -- связи --

    def rels(self, partname: str) -> Relationships:
        raw = self.parts.get(rels_name(partname))
        return Relationships.parse(raw) if raw else Relationships()

    def put_rels(self, partname: str, rels: Relationships) -> None:
        self.parts[rels_name(partname)] = rels.to_bytes()

    def resolve(self, source_part: str, target: str) -> str:
        base = posixpath.dirname(source_part)
        return posixpath.normpath(posixpath.join(base, target))

    def relative(self, source_part: str, target_part: str) -> str:
        return posixpath.relpath(target_part, posixpath.dirname(source_part))

    # -- XML --

    def xml(self, partname: str) -> ET.Element:
        raw = self.parts[partname]
        register_prefixes(raw)
        return ET.fromstring(raw)

    def put_xml(self, partname: str, root: ET.Element, content_type: str | None = None) -> None:
        self.write(partname, serialize(root), content_type)

    # -- сохранение --

    def save(self, path: str) -> None:
        self.parts["/[Content_Types].xml"] = self.content_types.to_bytes()
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in sorted(self.parts):
                info = zipfile.ZipInfo(name.lstrip("/"), date_time=FIXED_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                zf.writestr(info, self.parts[name])
