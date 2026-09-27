"""Сборка колоды по плану. Шаг Ш6 плана `PLAN-3.0`.

Порядок важен: доноры вычитываются **до** удаления исходных слайдов, иначе
новый `/ppt/slides/slide1.xml` затрёт того, кого собирался копировать.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model import DeckPlan, PatternLibrary
from ..opc.package import Package
from ..oxml.ns import qn
from .clone import (
    clear_text,
    find_shape,
    iter_shapes,
    place_clone,
    read_donor,
    remove_shape,
    slide_from_layout,
)
from .icon import replace_with_icon
from .package import CT_SLIDE, RT_SLIDE, PackageWriter
from .substitute import replace_picture, set_font_scale, set_items, set_text
from .visual import place as place_visual
from .visual import slide_height

#: Идентификаторы слайдов в `p:sldIdLst` должны быть не меньше 256.
_FIRST_SLIDE_ID = 256


@dataclass(frozen=True)
class BuildReport:
    """Что получилось. Разделение на подставленное и унаследованное — не
    статистика, а суть подхода: всё унаследованное мы даже не разбирали."""

    output: str
    slides: int
    substituted: int
    inherited_shapes: int
    cleared_slots: int
    warnings: tuple[str, ...]
    #: Сколько слотов получили ужатый кегль по решению стадии VERIFY.
    scaled_slots: int = 0
    #: Индексы слайдов плана в том порядке, в каком они попали в файл.
    #: Слайд, который не собрался, сюда не попадает — и без этого списка
    #: соответствие «слайд файла → слайд плана» пришлось бы угадывать по
    #: номеру, а оно разъезжается при первом же пропуске (`PLAN-4.0`,
    #: первая логическая проверка шагов 2–3).
    slides_written: tuple[int, ...] = ()

    def to_json(self) -> dict:
        return {
            "output": self.output,
            "slides": self.slides,
            "substituted": self.substituted,
            "inherited_shapes": self.inherited_shapes,
            "cleared_slots": self.cleared_slots,
            "scaled_slots": self.scaled_slots,
            "slides_written": list(self.slides_written),
            "warnings": list(self.warnings),
        }


def _rebuild_presentation(writer: PackageWriter, slide_parts: list[str]) -> None:
    pres_part = "/ppt/presentation.xml"
    root = writer.xml(pres_part)
    rels = writer.rels(pres_part)
    rels.drop_type(RT_SLIDE)

    id_lst = root.find(qn("p:sldIdLst"))
    if id_lst is None:
        id_lst = root.makeelement(qn("p:sldIdLst"), {})
        # p:sldIdLst обязан идти после p:sldMasterIdLst и до p:sldSz.
        master_lst = root.find(qn("p:sldMasterIdLst"))
        position = list(root).index(master_lst) + 1 if master_lst is not None else 0
        root.insert(position, id_lst)
    for child in list(id_lst):
        id_lst.remove(child)

    for n, part in enumerate(slide_parts):
        rid = rels.add(RT_SLIDE, writer.relative(pres_part, part))
        entry = id_lst.makeelement(qn("p:sldId"), {"id": str(_FIRST_SLIDE_ID + n)})
        entry.set(qn("r:id"), rid)
        id_lst.append(entry)

    writer.put_rels(pres_part, rels)
    writer.put_xml(pres_part, root)


def build(
    template_path: str,
    plan: DeckPlan,
    library: PatternLibrary,
    output_path: str,
) -> BuildReport:
    warnings: list[str] = []
    patterns = {p.id: p for p in library.patterns}

    with Package(template_path) as source:
        if source.sha256 != plan.source.patterns_sha256:
            warnings.append(
                "План построен по другому разбору этого шаблона: хеши не совпали. "
                "Идентификаторы паттернов и слотов могут означать не то."
            )
        writer = PackageWriter(source)

    # 1. Доноры — в память, до всякого удаления.
    donor_parts = {
        patterns[s.pattern_id].donor_part
        for s in plan.slides
        if s.pattern_id in patterns and patterns[s.pattern_id].source == "slides"
    }
    donors = {part: read_donor(writer, part) for part in donor_parts if writer.has(part)}

    # 2. Исходные слайды и заметки уходят: колода будет из запланированных.
    for part in writer.names("/ppt/slides/", ".xml") + writer.names("/ppt/notesSlides/", ".xml"):
        writer.remove(part)

    # 3. Сборка.
    substituted = 0
    inherited = 0
    cleared = 0
    scaled = 0
    #: Сквозной номер картинки по всей колоде. Именно сквозной: имя части
    #: раньше бралось от номера слайда, и две картинки одного слайда делили
    #: одну часть (`Z-28a`, `PLAN-7.10`, шаг 4).
    pictures = 0
    #: Сквозной номер своей диаграммы — имя её части и книги (`Z-32`).
    charts = 0
    slide_cy = slide_height(writer)
    slide_parts: list[str] = []
    written: list[int] = []

    for n, planned in enumerate(plan.slides, 1):
        pattern = patterns.get(planned.pattern_id)
        if pattern is None:
            warnings.append(f"слайд {planned.index}: паттерна {planned.pattern_id} нет в библиотеке")
            continue

        part = f"/ppt/slides/slide{n}.xml"
        if pattern.source == "slides":
            payload = donors.get(pattern.donor_part)
            if payload is None:
                warnings.append(
                    f"слайд {planned.index}: донор {pattern.donor_part} отсутствует в шаблоне"
                )
                continue
            tree = place_clone(writer, payload, part)
        else:
            if not writer.has(pattern.donor_part):
                warnings.append(f"слайд {planned.index}: макета {pattern.donor_part} нет")
                continue
            tree = slide_from_layout(writer, pattern.donor_part, part)

        slots = {s.id: s for s in pattern.slots}
        touched: set[str] = set()

        for fill in planned.fills:
            slot = slots.get(fill.slot_id)
            if slot is None:
                warnings.append(f"слайд {planned.index}: слота {fill.slot_id} нет в паттерне")
                continue
            shape = find_shape(tree, slot.shape_id)
            if shape is None:
                warnings.append(
                    f"слайд {planned.index}: фигура {slot.shape_id} не найдена в доноре"
                )
                continue

            if fill.kind == "list":
                ok = set_items(shape, fill.items or ())
            elif fill.kind == "image":
                pictures += 1
                problem = replace_picture(writer, part, shape, fill.ref or "", pictures)
                if problem:
                    warnings.append(f"слайд {planned.index}: {problem}")
                ok = problem is None
            elif fill.kind in ("chart", "table") and fill.data is not None:
                # Своя таблица или диаграмма (`Z-32`, `ADR-0026`) — на место
                # фигуры-хозяина, в её координаты.
                charts += fill.kind == "chart"
                rect = (slot.rect.x, slot.rect.y, slot.rect.cx, slot.rect.cy)
                texts = [(s.rect.x, s.rect.y, s.rect.cx, s.rect.cy) for f in planned.fills
                         if f.kind in ("text", "list", "number") and (s := slots.get(f.slot_id))]
                problem = place_visual(writer, part, tree, shape, rect, fill.data, charts, slide_cy,
                                       others=texts)
                if problem:
                    warnings.append(f"слайд {planned.index}: {problem}")
                ok = True
            elif fill.kind in ("chart", "table"):
                # Данных нет — подмены тоже (`Z-12`): фигуру уберёт проход ниже.
                ok = False
            else:
                ok = set_text(shape, fill.text or "")

            if ok:
                substituted += 1
                touched.add(slot.shape_id)
                # Шкалу кегля назначила стадия VERIFY; решение лежит в плане,
                # здесь только применение (`ADR-0005`, `PLAN-4.0` шаг 4).
                if fill.font_scale is not None and set_font_scale(shape, fill.font_scale):
                    scaled += 1

        # Слоты, которым не досталось содержимого, несут текст донора: рыбу,
        # примеры дизайнера, иногда чужие контакты (`PLAN-3.1`). Убираем.
        #
        # Обход идёт по `pattern.slots` в их порядке, а не по множеству —
        # иначе порядок правок, а с ним и XML на выходе, стал бы случайным.
        # Слот, который план собирался заполнить, не трогаем даже при неудачной
        # подстановке: там содержимое донора оставлено сознательно, и об этом
        # уже есть предупреждение.
        intended = {f.slot_id for f in planned.fills}
        # Место, куда встала своя таблица или диаграмма (`Z-32`): фигуры донора
        # там уже нет, убирать нечего.
        visual = {f.slot_id for f in planned.fills
                  if f.kind in ("table", "chart") and f.data is not None}
        # Таблица и диаграмма донора — чужие числа (`Z-62`): данных таблиц и
        # диаграмм движок не подменяет (`Z-12`), поэтому фигура уходит со
        # слайда целиком, заполнял её план или нет. Ранг такие раскладки
        # обходит (`_PENALTY_DONOR_DATA`); здесь — страховка, и она слышна.
        for slot in pattern.slots:
            if slot.content_type not in ("table", "chart") or slot.id in visual:
                continue
            shape = find_shape(tree, slot.shape_id)
            if shape is not None and remove_shape(tree, shape):
                warnings.append(
                    f"слайд {planned.index}: {'таблица' if slot.content_type == 'table' else 'диаграмма'}"
                    f" донора убрана — чужие числа на слайде хуже пустого места (Z-62)"
                )
        # Картинка донора, которую зрение модели признало содержимым —
        # диаграмма, снимок экрана, фото (`plan/donor.py`, `Z-62`).
        for shape_id in getattr(planned, "dropped_pictures", ()):
            shape = find_shape(tree, shape_id)
            if shape is not None and remove_shape(tree, shape):
                warnings.append(
                    f"слайд {planned.index}: картинка донора с чужим содержимым убрана (Z-62)"
                )
        # Значок донора — пиктограмма по смыслу пункта (`plan/icons.py`, `Z-32`).
        for icon in getattr(planned, "icons", ()):
            shape = find_shape(tree, icon.shape_id)
            if shape is None or shape.tag != qn("p:pic"):
                warnings.append(f"слайд {planned.index}: значка {icon.shape_id} нет — "
                                f"пиктограмма «{icon.name}» не встала")
                continue
            problem = replace_with_icon(tree, shape, icon.name, icon.color)
            if problem:
                warnings.append(f"слайд {planned.index}: {problem}")
        for slot in pattern.slots:
            if slot.id in intended or slot.content_type in ("table", "chart"):
                continue
            shape = find_shape(tree, slot.shape_id)
            if shape is None:
                warnings.append(
                    f"слайд {planned.index}: пустой слот {slot.id} — "
                    f"фигуры {slot.shape_id} нет в доноре, текст мог остаться"
                )
                continue
            if clear_text(shape):
                cleared += 1

        inherited += sum(1 for s in iter_shapes(tree) if s is not None) - len(touched)
        writer.put_xml(part, tree, CT_SLIDE)
        slide_parts.append(part)
        written.append(planned.index)

    if not slide_parts:
        warnings.append("Не собрано ни одного слайда: в плане нет применимых паттернов.")

    _rebuild_presentation(writer, slide_parts)
    writer.save(output_path)

    return BuildReport(
        output=output_path,
        slides=len(slide_parts),
        substituted=substituted,
        inherited_shapes=inherited,
        cleared_slots=cleared,
        scaled_slots=scaled,
        slides_written=tuple(written),
        warnings=tuple(warnings),
    )
