"""Клонирование донора. Шаги Ш3 и Ш4 плана `PLAN-3.0`, обоснование в `ADR-0012`.

Клон слайда — это копия двух записей ZIP: самой части и её файла связей. Цели в
связях заданы относительно каталога части, а новый слайд ложится в тот же
каталог `/ppt/slides/`, поэтому копия связей остаётся корректной без единой
правки: макет, медиа и всё остальное разрешаются в те же самые части.

Паттерны, выведенные из макетов (`ADR-0006`), приводятся к тому же виду:
из макета синтезируется минимальный слайд, и дальше работает общий путь. Один
механизм вместо двух.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from ..oxml.ns import qn
from .package import (
    CT_SLIDE,
    RT_LAYOUT,
    RT_NOTES,
    PackageWriter,
    Relationships,
    register_prefixes,
)

#: Колонтитульные плейсхолдеры в синтезированный слайд не переносятся: они
#: пусты по смыслу и только мешают. DOM-GEOM §7.
_CHROME = {"sldNum", "ftr", "dt", "hdr"}

_SHAPE_TAGS = (qn("p:sp"), qn("p:pic"), qn("p:graphicFrame"), qn("p:cxnSp"))

_EMPTY_SLIDE = (
    '<p:sld xmlns:a="{a}" xmlns:r="{r}" xmlns:p="{p}">'
    "<p:cSld><p:spTree>"
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    "<p:grpSpPr><a:xfrm>"
    '<a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
    '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/>'
    "</a:xfrm></p:grpSpPr>"
    "</p:spTree></p:cSld>"
    "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>"
    "</p:sld>"
)


def iter_shapes(root: ET.Element) -> Iterator[ET.Element]:
    """Все фигуры дерева, включая вложенные в группы."""
    for el in root.iter():
        if el.tag in _SHAPE_TAGS:
            yield el


def shape_id_of(shape: ET.Element) -> str | None:
    for holder in ("p:nvSpPr", "p:nvPicPr", "p:nvGraphicFramePr", "p:nvCxnSpPr"):
        nv = shape.find(f"{qn(holder)}/{qn('p:cNvPr')}")
        if nv is not None:
            return nv.get("id")
    return None


def find_shape(root: ET.Element, shape_id: str) -> ET.Element | None:
    """Фигура по якорю из слота (`p:cNvPr/@id`)."""
    if not shape_id:
        return None
    for shape in iter_shapes(root):
        if shape_id_of(shape) == shape_id:
            return shape
    return None


def remove_shape(root: ET.Element, shape: ET.Element) -> bool:
    """Убирает фигуру из дерева слайда, где бы она ни лежала — и в группе.
    Связь на часть (диаграмму) остаётся неиспользованной: это допустимо в
    OOXML, а удалять саму часть значит трогать то, что может делить колода."""
    for parent in root.iter():
        for child in list(parent):
            if child is shape:
                parent.remove(child)
                return True
    return False


@dataclass(frozen=True)
class DonorPayload:
    """Донор, вычитанный в память.

    Читать доноров надо **до** удаления исходных слайдов: новый
    `/ppt/slides/slide1.xml` иначе затрёт того, кого собирался копировать.
    """

    part: str
    raw: bytes
    rels: Relationships


def read_donor(writer: PackageWriter, donor_part: str) -> DonorPayload:
    rels = writer.rels(donor_part)
    # Заметки ссылаются обратно на свой слайд, которого в выходном пакете не
    # будет. Связь в никуда делает пакет невалидным.
    rels.drop_type(RT_NOTES)
    return DonorPayload(part=donor_part, raw=writer.read(donor_part), rels=rels)


def place_clone(writer: PackageWriter, payload: DonorPayload, new_part: str) -> ET.Element:
    """Кладёт копию донора под новым именем и возвращает дерево для правки."""
    register_prefixes(payload.raw)
    writer.put_rels(new_part, Relationships(list(payload.rels.items)))
    writer.content_types.overrides[new_part] = CT_SLIDE
    return ET.fromstring(payload.raw)


def clone_slide(writer: PackageWriter, donor_part: str, new_part: str) -> ET.Element:
    """Прямое клонирование, когда исходные слайды ещё на месте."""
    return place_clone(writer, read_donor(writer, donor_part), new_part)


def clear_text(shape: ET.Element) -> bool:
    """Убирает текст фигуры, сохраняя оформление первого абзаца.

    Прогоны **удаляются**, а не обнуляются. Обнуление годилось для подсказки
    макета, но не для слота, оставшегося без содержимого: пустой абзац в
    маркированном списке рисует маркер, и вместо чужой строки на слайде остаётся
    сиротливая точка (`PLAN-3.1`, шаг 2).

    Поля (`a:fld` — номер слайда, дата) не трогаются: их значение подставляет
    PowerPoint, и оно не чужое.

    Возвращает True, если что-то действительно убрано.
    """
    body = shape.find(qn("p:txBody"))
    if body is None:
        return False
    paras = body.findall(qn("a:p"))
    if not paras:
        return False

    changed = False
    for para in paras[1:]:
        body.remove(para)
        changed = True
    for child in list(paras[0]):
        if child.tag in (qn("a:r"), qn("a:br")):
            paras[0].remove(child)
            changed = True
    return changed


def _clear_text(shape: ET.Element) -> None:
    """Совместимость: прежнее имя, используется синтезом слайда из макета."""
    clear_text(shape)


def slide_from_layout(writer: PackageWriter, layout_part: str, new_part: str) -> ET.Element:
    """Синтезирует слайд по макету — для паттернов из `ADR-0006`.

    Дальше он обрабатывается как обычный клон: те же подстановки, тот же путь.
    """
    layout_raw = writer.read(layout_part)
    register_prefixes(layout_raw)
    layout = ET.fromstring(layout_raw)

    from ..oxml.ns import NS

    slide = ET.fromstring(_EMPTY_SLIDE.format(a=NS["a"], r=NS["r"], p=NS["p"]))
    tree = slide.find(f"{qn('p:cSld')}/{qn('p:spTree')}")

    source_tree = layout.find(f"{qn('p:cSld')}/{qn('p:spTree')}")
    if source_tree is not None and tree is not None:
        for shape in source_tree.findall(qn("p:sp")):
            ph = shape.find(f"{qn('p:nvSpPr')}/{qn('p:nvPr')}/{qn('p:ph')}")
            if ph is None or (ph.get("type") or "body") in _CHROME:
                continue
            clone = copy.deepcopy(shape)
            _clear_text(clone)
            tree.append(clone)

    rels = Relationships()
    rels.add(RT_LAYOUT, writer.relative(new_part, layout_part))
    writer.put_rels(new_part, rels)
    writer.content_types.overrides[new_part] = CT_SLIDE
    return slide
