"""Структурный валидатор пакета. Шаг Ш7 плана `PLAN-3.0`.

Заменяет собой то, чего мы не можем сделать: открыть файл в PowerPoint. Ловит
не красоту, а нарушения, из-за которых пакет не откроется вовсе — связь в
несуществующую часть, отсутствующий тип содержимого, битый XML, ссылку на
слайд, которого нет.

Модуль не знает про презентации как таковые: он проверяет пакет, а не смысл.
"""

from __future__ import annotations

import posixpath
import zipfile
from xml.etree import ElementTree as ET

from ..oxml.ns import NS

_REL_NS = NS["rel"]
_CT_NS = NS["ct"]
_P_NS = NS["p"]
_R_NS = NS["r"]

_ROOT_RELS = "_rels/.rels"


def _norm(name: str) -> str:
    return posixpath.normpath("/" + name.replace("\\", "/").lstrip("/"))


def inspect(path: str) -> list[str]:
    """Список проблем. Пустой список — пакет структурно корректен."""
    problems: list[str] = []
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        return [f"{path}: не ZIP-архив"]

    with zf:
        names = {_norm(i.filename) for i in zf.infolist() if not i.filename.endswith("/")}
        read = lambda n: zf.read(n.lstrip("/"))  # noqa: E731

        # 1. Типы содержимого.
        if "/[Content_Types].xml" not in names:
            return ["нет /[Content_Types].xml"]
        try:
            ct_root = ET.fromstring(read("/[Content_Types].xml"))
        except ET.ParseError as exc:
            return [f"[Content_Types].xml не разбирается: {exc}"]

        defaults = {
            (el.get("Extension") or "").lower()
            for el in ct_root.findall(f"{{{_CT_NS}}}Default")
        }
        overrides = {
            el.get("PartName") or "" for el in ct_root.findall(f"{{{_CT_NS}}}Override")
        }
        for name in sorted(names):
            if name == "/[Content_Types].xml" or "/_rels/" in name:
                continue
            extension = posixpath.splitext(name)[1].lstrip(".").lower()
            if name not in overrides and extension not in defaults:
                problems.append(f"у части {name} нет типа содержимого")

        # 2. Связи разрешаются в существующие части.
        slide_parts: set[str] = set()
        for name in sorted(n for n in names if n.endswith(".rels")):
            try:
                root = ET.fromstring(read(name))
            except ET.ParseError as exc:
                problems.append(f"{name} не разбирается: {exc}")
                continue
            directory = posixpath.dirname(posixpath.dirname(name))
            for el in root.findall(f"{{{_REL_NS}}}Relationship"):
                if el.get("TargetMode") == "External":
                    continue
                target = (el.get("Target") or "").replace("\\", "/")
                resolved = (
                    posixpath.normpath(target)
                    if target.startswith("/")
                    else posixpath.normpath(posixpath.join(directory or "/", target))
                )
                if resolved not in names:
                    problems.append(
                        f"{name}: связь {el.get('Id')} ведёт в несуществующую часть {resolved}"
                    )
                elif (el.get("Type") or "").endswith("/slide"):
                    slide_parts.add(resolved)

        # 3. Весь XML разбирается.
        for name in sorted(n for n in names if n.endswith(".xml")):
            try:
                ET.fromstring(read(name))
            except ET.ParseError as exc:
                problems.append(f"{name} не разбирается: {exc}")

        # 4. Список слайдов согласован со связями.
        pres = "/ppt/presentation.xml"
        if pres not in names:
            problems.append("нет /ppt/presentation.xml")
            return problems
        try:
            root = ET.fromstring(read(pres))
        except ET.ParseError:
            return problems

        pres_rels = {}
        rels_part = "/ppt/_rels/presentation.xml.rels"
        if rels_part in names:
            for el in ET.fromstring(read(rels_part)).findall(f"{{{_REL_NS}}}Relationship"):
                pres_rels[el.get("Id")] = el.get("Target") or ""

        id_lst = root.find(f"{{{_P_NS}}}sldIdLst")
        listed = []
        if id_lst is not None:
            for entry in id_lst.findall(f"{{{_P_NS}}}sldId"):
                rid = entry.get(f"{{{_R_NS}}}id")
                if rid not in pres_rels:
                    problems.append(f"p:sldIdLst ссылается на связь {rid}, которой нет")
                    continue
                listed.append(posixpath.normpath(posixpath.join("/ppt", pres_rels[rid])))
        if not listed:
            problems.append("в презентации не осталось ни одного слайда")

        ids = [e.get("id") for e in (id_lst.findall(f"{{{_P_NS}}}sldId") if id_lst is not None else [])]
        if len(set(ids)) != len(ids):
            problems.append("идентификаторы слайдов в p:sldIdLst повторяются")

        for part in listed:
            if part not in names:
                problems.append(f"p:sldIdLst ссылается на отсутствующую часть {part}")

        # 5. У каждого слайда ровно один макет.
        for part in listed:
            rels_name = posixpath.join(
                posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels"
            )
            if rels_name not in names:
                problems.append(f"у слайда {part} нет файла связей")
                continue
            layouts = [
                el for el in ET.fromstring(read(rels_name)).findall(f"{{{_REL_NS}}}Relationship")
                if (el.get("Type") or "").endswith("/slideLayout")
            ]
            if len(layouts) != 1:
                problems.append(f"у слайда {part} связей с макетом: {len(layouts)}, а нужна одна")

    return problems
