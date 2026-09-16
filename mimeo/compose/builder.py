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
    slide_from_layout,
)
from .package import CT_SLIDE, RT_SLIDE, PackageWriter
from .substitute import replace_picture, set_font_scale, set_items, set_text

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
                problem = replace_picture(writer, part, shape, fill.ref or "", n)
                if problem:
                    warnings.append(f"слайд {planned.index}: {problem}")
                ok = problem is None
            elif fill.kind in ("chart", "table"):
                warnings.append(
                    f"слайд {planned.index}: данные {fill.kind} не подменяются, "
                    f"осталось содержимое донора"
                )
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
        for slot in pattern.slots:
            if slot.id in intended:
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
