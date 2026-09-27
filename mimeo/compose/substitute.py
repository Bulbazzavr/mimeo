"""Подстановка содержимого в клонированную фигуру. Шаги Ш5 и Ш6 `PLAN-3.0`.

Принцип: **текст наш, оформление их**. Первый прогон первого абзаца — носитель
оформления донора; меняется только его текст, а свойства `a:rPr` остаются как
были. Для списка абзац-носитель клонируется по числу пунктов, поэтому маркеры,
отступы и интерлиньяж сохраняются автоматически.

Поля (`a:fld` — номер слайда, дата) не трогаются: они вычисляемые.
"""

from __future__ import annotations

import copy
import os
from xml.etree import ElementTree as ET

from ..oxml.ns import qn
from .package import RT_IMAGE, PackageWriter

#: Типы содержимого для картинок, которые мы кладём в пакет сами.
_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".tiff": "image/tiff",
}


def _body(shape: ET.Element) -> ET.Element | None:
    return shape.find(qn("p:txBody"))


def _style_paragraph(body: ET.Element) -> ET.Element | None:
    """Абзац-носитель оформления: первый, в котором есть прогон с текстом."""
    paragraphs = body.findall(qn("a:p"))
    if not paragraphs:
        return None
    for para in paragraphs:
        if para.find(qn("a:r")) is not None:
            return para
    return paragraphs[0]


def _ensure_run(para: ET.Element) -> ET.Element:
    """Прогон в абзаце. Если его нет, создаётся с оформлением `a:endParaRPr`.

    Порядок детей `a:p` задан схемой DrawingML: `a:pPr?`, затем прогоны
    (`a:r`, `a:br`, `a:fld`), и только потом `a:endParaRPr`. Дописывание в конец
    ставило новый прогон **за** `a:endParaRPr`, и PowerPoint переставал его
    показывать: текст лежал в файле, а слайд выходил пустым. XML при этом
    остаётся корректным, поэтому ни `python-pptx`, ни наш разбор дефекта не
    видели (`DOM-TEXT §1`, `WORKLOG/2026-09-12-endpararpr.md`).

    Пустой абзац макета состоит ровно из `a:endParaRPr` — поэтому страдали
    именно слайды, синтезированные из макетов (`ADR-0006`).
    """
    run = para.find(qn("a:r"))
    if run is not None:
        return run

    run = para.makeelement(qn("a:r"), {})
    end_props = para.find(qn("a:endParaRPr"))
    if end_props is not None:
        props = copy.deepcopy(end_props)
        props.tag = qn("a:rPr")
        run.append(props)
        para.insert(list(para).index(end_props), run)
    else:
        para.append(run)
    ET.SubElement(run, qn("a:t")).text = ""
    return run


def _set_paragraph_text(para: ET.Element, text: str) -> None:
    run = _ensure_run(para)
    target = run.find(qn("a:t"))
    if target is None:
        target = ET.SubElement(run, qn("a:t"))
    target.text = text
    # Лишние прогоны донора убираем: оформление берём у первого, остальное
    # относилось к чужому тексту.
    for extra in para.findall(qn("a:r"))[1:]:
        para.remove(extra)
    for br in para.findall(qn("a:br")):
        para.remove(br)


def set_text(shape: ET.Element, text: str) -> bool:
    body = _body(shape)
    if body is None:
        return False
    para = _style_paragraph(body)
    if para is None:
        return False
    _set_paragraph_text(para, text)
    for extra in body.findall(qn("a:p")):
        if extra is not para:
            body.remove(extra)
    return True


def set_font_scale(shape: ET.Element, percent: int) -> bool:
    """Ужимает текст фигуры до `percent` процентов кегля.

    Пишет `a:normAutofit fontScale` в `a:bodyPr` — тот самый механизм, которым
    PowerPoint ужимает текст сам. Значение хранится в тысячных долях процента:
    62% это `62000`.

    **Это меняет замысел шаблона, и намеренно.** Если у фигуры стоял
    `a:spAutoFit` («бокс растёт под текст»), мы его отменяем: бокс остаётся
    таким, каким его задумал дизайнер, а ужимается текст. Иначе вёрстка донора
    поехала бы ради нашего содержимого (`PLAN-4.0`, шаг 4).

    Решение о шкале принимает план (`Fill.font_scale`), здесь только запись.
    """
    body = _body(shape)
    if body is None:
        return False
    props = body.find(qn("a:bodyPr"))
    if props is None:
        props = body.makeelement(qn("a:bodyPr"), {})
        body.insert(0, props)

    for tag in ("a:normAutofit", "a:spAutoFit", "a:noAutofit"):
        existing = props.find(qn(tag))
        if existing is not None:
            props.remove(existing)

    value = max(10, min(100, int(percent)))
    props.append(props.makeelement(qn("a:normAutofit"), {"fontScale": str(value * 1000)}))
    return True


def allow_wrap(shape: ET.Element, longest: int, chars_per_line: int | None) -> bool:
    """Включает перенос строк у фигуры с `wrap="none"`, если наш текст не
    встаёт в её строку (`longest` знаков против `chars_per_line`).

    Дизайнер выключает перенос у надписи под короткое слово — «BUSINESS»,
    «05». Наш заголовок длиннее, и без переноса PowerPoint тянет его одной
    строкой за край слайда, а ремонт кеглем ширину не лечит (`DOM-TEXT §15`;
    найдено 27 сентября на незнакомом шаблоне из `samples/`, проверка
    программы целиком). С переносом текст остаётся в ширине места, а высоту
    ужимает проверка вёрстки. Текст, встающий в строку, не трогаем: у него
    перенос ничего не меняет. Возвращает, включён ли перенос."""
    body = _body(shape)
    props = body.find(qn("a:bodyPr")) if body is not None else None
    if props is None or props.get("wrap") != "none" or not chars_per_line:
        return False
    if longest <= chars_per_line:
        return False
    props.set("wrap", "square")
    return True


def set_items(shape: ET.Element, items: tuple[str, ...]) -> bool:
    """Список: абзац-носитель клонируется по числу пунктов."""
    body = _body(shape)
    if body is None:
        return False
    para = _style_paragraph(body)
    if para is None:
        return False
    template = copy.deepcopy(para)
    for existing in body.findall(qn("a:p")):
        body.remove(existing)
    for item in items or ("",):
        clone = copy.deepcopy(template)
        _set_paragraph_text(clone, item)
        body.append(clone)
    return True


def replace_picture(
    writer: PackageWriter, slide_part: str, shape: ET.Element, image_path: str, index: int
) -> str | None:
    """Подменяет картинку в `p:pic`, а у рамки под фото (`p:sp`, `Z-55`) —
    заливку фигуры. Возвращает текст предупреждения или None.

    Это единственное место, где копии связей донора недостаточно: нужна новая
    часть в `/ppt/media/`, её тип содержимого и новая связь слайда.

    **`index` обязан быть сквозным по колоде, а не номером слайда** (`Z-28a`,
    `PLAN-7.10`, шаг 4). Пока он был номером слайда, две картинки одного
    слайда писались в одну часть: вторая затирала первую, обе связи указывали
    на неё, и на растре одна и та же картинка стояла дважды. Предупреждений
    при этом не было ни одного, а `substituted` считал обе подстановки
    успешными — отчёт говорил «сделано», зритель видел не то
    (`WORKLOG/2026-09-21-z28a-baseline.md`, дефект A).
    """
    blip = shape.find(f"{qn('p:blipFill')}/{qn('a:blip')}")
    frame = blip is None and shape.tag == qn("p:sp") and shape.find(qn("p:spPr")) is not None
    if blip is None and not frame:
        return f"в фигуре нет картинки, подмена {os.path.basename(image_path)} пропущена"
    if not os.path.isfile(image_path):
        return f"файл {image_path} не найден, оставлена картинка донора"

    extension = os.path.splitext(image_path)[1].lower()
    content_type = _IMAGE_TYPES.get(extension)
    if content_type is None:
        return f"неизвестный тип картинки {extension}, оставлена картинка донора"
    if frame:
        blip = _fill_with_picture(shape.find(qn("p:spPr")))

    part = f"/ppt/media/mimeo{index}{extension}"
    with open(image_path, "rb") as fh:
        writer.write(part, fh.read())
    writer.content_types.ensure_default(extension.lstrip("."), content_type)

    rels = writer.rels(slide_part)
    rid = rels.add(RT_IMAGE, writer.relative(slide_part, part))
    writer.put_rels(slide_part, rels)
    blip.set(qn("r:embed"), rid)
    return None


#: Заливки фигуры в `p:spPr` — ровно одна из них (CT_ShapeProperties, группа
#: EG_FillProperties) стоит после геометрии.
_FILLS = ("a:noFill", "a:solidFill", "a:gradFill", "a:blipFill", "a:pattFill", "a:grpFill")


def _fill_with_picture(sp_pr: ET.Element) -> ET.Element:
    """Рамка под фото (`Z-55`): картинка встаёт заливкой самой фигуры.

    Так остаются геометрия, скругление, обводка и тень, какими их задал
    дизайнер, — это та же фигура донора, только залитая картинкой. Картинку
    генератор рисует в пропорции рамки (`plan/images.py`), поэтому растяжение
    `a:stretch` её не искажает. Возвращает `a:blip`, которому осталось
    назначить связь."""
    old = [c for c in sp_pr if c.tag in {qn(t) for t in _FILLS}]
    at = list(sp_pr).index(old[0]) if old else None
    for child in old:
        sp_pr.remove(child)
    if at is None:
        after = [i for i, c in enumerate(sp_pr)
                 if c.tag in (qn("a:xfrm"), qn("a:custGeom"), qn("a:prstGeom"))]
        at = after[-1] + 1 if after else 0
    fill = ET.Element(qn("a:blipFill"), {"rotWithShape": "1"})
    blip = ET.SubElement(fill, qn("a:blip"))
    ET.SubElement(ET.SubElement(fill, qn("a:stretch")), qn("a:fillRect"))
    sp_pr.insert(at, fill)
    return blip
