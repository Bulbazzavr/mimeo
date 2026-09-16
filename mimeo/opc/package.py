"""Чтение OPC-пакета: части, связи, граф. DOM-PKG.

Только стандартная библиотека — ADR-0001.
"""

from __future__ import annotations

import hashlib
import posixpath
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from ..oxml.ns import RT, qn

_ROOT_RELS = "/_rels/.rels"


class PackageError(ValueError):
    """Пакет не удалось прочитать как OPC. Отдельный тип, чтобы CLI не ловил
    заодно любой ValueError из стандартной библиотеки."""


@dataclass(frozen=True)
class Relationship:
    id: str
    type: str
    target: str
    external: bool
    source: str

    @property
    def target_part(self) -> str | None:
        """Имя части, если связь внутренняя."""
        if self.external:
            return None
        return _resolve(self.source, self.target)


def _norm(partname: str) -> str:
    """Нормализуем к виду с ведущим слэшем и прямыми разделителями. DOM-PKG §7."""
    p = partname.replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    return posixpath.normpath(p)


def _resolve(source_part: str, target: str) -> str:
    """Цель связи относительна каталогу части, а не корню. DOM-PKG §3."""
    t = target.replace("\\", "/")
    if t.startswith("/"):
        return posixpath.normpath(t)
    base = posixpath.dirname(_norm(source_part))
    return posixpath.normpath(posixpath.join(base, t))


def _rels_part_for(partname: str) -> str:
    if partname in ("/", ""):
        return _ROOT_RELS
    d, _, name = _norm(partname).rpartition("/")
    return f"{d}/_rels/{name}.rels"


class Package:
    """Пакет, открытый на чтение. Части и XML кэшируются."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._zip = zipfile.ZipFile(path)
        self._names = {
            _norm(i.filename)
            for i in self._zip.infolist()
            if not i.filename.endswith("/")  # каталоги как записи — DOM-PKG §7
        }
        self._xml_cache: dict[str, ET.Element] = {}
        self._rels_cache: dict[str, dict[str, Relationship]] = {}
        self._sha256: str | None = None

    # -- жизненный цикл -------------------------------------------------

    def close(self) -> None:
        self._zip.close()

    def __enter__(self) -> "Package":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- части ----------------------------------------------------------

    def has_part(self, partname: str) -> bool:
        return _norm(partname) in self._names

    def read(self, partname: str) -> bytes:
        return self._zip.read(_norm(partname).lstrip("/"))

    def xml(self, partname: str) -> ET.Element:
        key = _norm(partname)
        cached = self._xml_cache.get(key)
        if cached is None:
            cached = ET.fromstring(self.read(key))
            self._xml_cache[key] = cached
        return cached

    @property
    def part_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._names))

    @property
    def sha256(self) -> str:
        if self._sha256 is None:
            h = hashlib.sha256()
            with open(self.path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            self._sha256 = h.hexdigest()
        return self._sha256

    # -- связи ----------------------------------------------------------

    def rels(self, partname: str) -> dict[str, Relationship]:
        source = _norm(partname) if partname not in ("/", "") else "/"
        cached = self._rels_cache.get(source)
        if cached is not None:
            return cached

        rels_part = _rels_part_for(source)
        out: dict[str, Relationship] = {}
        if self.has_part(rels_part):
            root = self.xml(rels_part)
            for el in root.findall(qn("rel:Relationship")):
                rid = el.get("Id") or ""
                out[rid] = Relationship(
                    id=rid,
                    type=el.get("Type") or "",
                    target=el.get("Target") or "",
                    external=(el.get("TargetMode") == "External"),
                    source=source,
                )
        self._rels_cache[source] = out
        return out

    def related(self, partname: str, reltype: str) -> list[Relationship]:
        return [r for r in self.rels(partname).values() if r.type == reltype]

    def related_one(self, partname: str, reltype: str) -> Relationship | None:
        found = self.related(partname, reltype)
        return found[0] if found else None

    def part_for_rid(self, source_part: str, rid: str) -> str | None:
        rel = self.rels(source_part).get(rid)
        return rel.target_part if rel else None

    # -- точка входа ----------------------------------------------------

    @property
    def main_part(self) -> str:
        """Главная часть ищется по типу связи, не по пути. DOM-PKG §2."""
        rel = self.related_one("/", RT.OFFICE_DOCUMENT)
        if rel is None or rel.target_part is None:
            raise PackageError(f"{self.path}: не найдена главная часть пакета")
        return rel.target_part


def open_package(path: str) -> Package:
    return Package(path)
