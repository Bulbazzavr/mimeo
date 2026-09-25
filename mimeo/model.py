"""Модель артефакта `design-system.json`.

Форма строго соответствует `contracts/design-system.schema.json` (ARCH-CONTRACTS).
Сериализация явная, а не через рефлексию: так расхождение со схемой видно в коде,
а не всплывает в тесте.

Вывод обязан быть детерминированным — ADR-0003.
"""

from __future__ import annotations

from dataclasses import dataclass

#: 1.2 — `deck-plan.source.configs`, паспорт конфигов прогона (`Z-33`,
#: `ADR-0022`). Поле обязательное, и потому число поднято у всех четырёх
#: артефактов: конвенция «артефакты одного прогона несут одно число»
#: (`ARCH-CONTRACTS`, история версий).
#:
#: **`slots[].typeface_kind` (`Z-43`) номер не поднял** — поле необязательное,
#: старый артефакт проходит новую схему. Так же поступили с `exclusive`
#: (`Z-44`), `fills[].font_scale` и `slots[].picture_kind` (`Z-28a`):
#: правило 3 там же.
SCHEMA_VERSION = "1.2"


@dataclass(frozen=True)
class ConfigStamp:
    """Паспорт одного конфига: чем разложили эту колоду (`Z-33`, `ADR-0022`).

    Живёт здесь, а не в `mimeo/config.py`, потому что это **форма артефакта**:
    `to_dict` уезжает в `deck-plan.json`, `source.configs`. Чтение файлов —
    работа `mimeo/config.py`, и оно импортирует отсюда, а не наоборот: `model`
    остаётся листом (`ARCH`, таблица границ).

    Отсутствие файла — не ошибка, а состояние: движок работает на встроенных
    значениях, и это должно быть видно, а не угадываться по молчанию.
    """

    name: str
    version: str = ""      #: объявлена в файле; пусто — не объявлена или файла нет
    sha256: str = ""       #: от байтов файла; пусто — файла нет
    loaded: bool = False
    source: str = ""       #: путь, по которому искали

    def to_dict(self) -> dict:
        """То, что уходит в артефакт. `loaded` и `source` не уходят: путь у
        каждой машины свой, а артефакт обязан быть одинаковым."""
        return {"name": self.name, "version": self.version, "sha256": self.sha256}


@dataclass(frozen=True)
class SourceInfo:
    filename: str
    sha256: str
    masters: int
    layouts: int
    slides: int

    def to_json(self) -> dict:
        return {
            "filename": self.filename,
            "sha256": self.sha256,
            "masters": self.masters,
            "layouts": self.layouts,
            "slides": self.slides,
        }


@dataclass(frozen=True)
class SlideSize:
    cx_emu: int
    cy_emu: int
    aspect: str
    type: str | None

    def to_json(self) -> dict:
        return {
            "cx_emu": self.cx_emu,
            "cy_emu": self.cy_emu,
            "aspect": self.aspect,
            "type": self.type,
        }


@dataclass(frozen=True)
class ThemeColor:
    role: str
    hex: str
    master: str

    def to_json(self) -> dict:
        return {"role": self.role, "hex": self.hex, "master": self.master}


@dataclass(frozen=True)
class ObservedColor:
    hex: str
    alpha: float
    count: int
    theme_role: str | None
    contexts: tuple[str, ...]

    def to_json(self) -> dict:
        return {
            "hex": self.hex,
            "alpha": round(self.alpha, 4),
            "count": self.count,
            "theme_role": self.theme_role,
            "contexts": list(self.contexts),
        }


@dataclass(frozen=True)
class TypeRole:
    id: str
    role: str
    latin: str | None
    cyrl: str | None
    size_pt: float
    size_norm: float
    bold: bool
    italic: bool
    caps: str | None
    spacing_pt: float
    line_spacing_pct: float | None
    color_hex: str | None
    align: str | None
    count: int
    examples: tuple[str, ...]

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "latin": self.latin,
            "cyrl": self.cyrl,
            "size_pt": self.size_pt,
            "size_norm": self.size_norm,
            "bold": self.bold,
            "italic": self.italic,
            "caps": self.caps,
            "spacing_pt": self.spacing_pt,
            "line_spacing_pct": self.line_spacing_pct,
            "color_hex": self.color_hex,
            "align": self.align,
            "count": self.count,
            "examples": list(self.examples),
        }


@dataclass(frozen=True)
class Grid:
    margin_left_emu: int
    margin_right_emu: int
    margin_top_emu: int
    margin_bottom_emu: int
    columns: int | None
    gutter_emu: int | None
    baseline_emu: int | None
    samples: int

    def to_json(self) -> dict:
        return {
            "margin_left_emu": self.margin_left_emu,
            "margin_right_emu": self.margin_right_emu,
            "margin_top_emu": self.margin_top_emu,
            "margin_bottom_emu": self.margin_bottom_emu,
            "columns": self.columns,
            "gutter_emu": self.gutter_emu,
            "baseline_emu": self.baseline_emu,
            "samples": self.samples,
        }


@dataclass(frozen=True)
class ShapeToken:
    geom: str
    fill_kind: str | None
    fill_hex: str | None
    line_hex: str | None
    line_w_emu: int | None
    corner_radius_pct: float | None
    has_shadow: bool
    count: int

    def to_json(self) -> dict:
        return {
            "geom": self.geom,
            "fill_kind": self.fill_kind,
            "fill_hex": self.fill_hex,
            "line_hex": self.line_hex,
            "line_w_emu": self.line_w_emu,
            "corner_radius_pct": self.corner_radius_pct,
            "has_shadow": self.has_shadow,
            "count": self.count,
        }


@dataclass(frozen=True)
class Unhandled:
    kind: str
    detail: str | None
    count: int

    def to_json(self) -> dict:
        return {"kind": self.kind, "detail": self.detail, "count": self.count}


@dataclass(frozen=True)
class Evidence:
    slides_analyzed: int
    shapes_total: int
    shapes_used: int
    shapes_positioned: int
    runs_total: int
    unhandled: tuple[Unhandled, ...] = ()
    notes: tuple[str, ...] = ()

    def to_json(self) -> dict:
        return {
            "slides_analyzed": self.slides_analyzed,
            "shapes_total": self.shapes_total,
            "shapes_used": self.shapes_used,
            "shapes_positioned": self.shapes_positioned,
            "runs_total": self.runs_total,
            "unhandled": [u.to_json() for u in self.unhandled],
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class DesignSystem:
    source: SourceInfo
    slide: SlideSize
    theme_palette: tuple[ThemeColor, ...]
    observed_palette: tuple[ObservedColor, ...]
    core_palette: tuple[str, ...]
    type_scale: tuple[TypeRole, ...]
    grid: Grid
    shapes: tuple[ShapeToken, ...]
    evidence: Evidence
    version: str = SCHEMA_VERSION

    def to_json(self) -> dict:
        return {
            "version": self.version,
            "source": self.source.to_json(),
            "slide": self.slide.to_json(),
            "palette": {
                "theme": [c.to_json() for c in self.theme_palette],
                "observed": [c.to_json() for c in self.observed_palette],
                "core": list(self.core_palette),
            },
            "type_scale": [t.to_json() for t in self.type_scale],
            "grid": self.grid.to_json(),
            "shapes": [s.to_json() for s in self.shapes],
            "evidence": self.evidence.to_json(),
        }


# --- библиотека паттернов ----------------------------------------------
#
# Форма соответствует contracts/pattern-library.schema.json. См. ADR-0004.


@dataclass(frozen=True)
class Capacity:
    """Сколько текста помещается в слот. Этим стадия PLAN ограничивает LLM."""

    max_chars: int
    max_lines: int
    chars_per_line: int
    target_chars: int | None
    max_items: int | None
    donor_chars: int | None
    basis: str

    def to_json(self) -> dict:
        return {
            "max_chars": self.max_chars,
            "max_lines": self.max_lines,
            "chars_per_line": self.chars_per_line,
            "target_chars": self.target_chars,
            "max_items": self.max_items,
            "donor_chars": self.donor_chars,
            "basis": self.basis,
        }


@dataclass(frozen=True)
class Slot:
    id: str
    role: str
    content_type: str
    rect: object          # analyze.deck.Rect — структурно x/y/cx/cy
    type_role: str | None
    capacity: Capacity | None
    required: bool
    shape_id: str = ""    # p:cNvPr/@id фигуры донора — якорь для стадии COMPOSE
    #: Годится ли гарнитура слота под обычный текст доклада (`Z-43`,
    #: `PLAN-7.3`): `prose`, `mono` — набрано как код, `icon` — пиктограммный
    #: шрифт. `None` отдельно от них и означает «судить не по чему»: у фигуры
    #: нет ни одного прогона, как у слотов запасного пути `_from_layouts`
    #: (`ADR-0006`). Измеритель обязан отличать «проверено и чисто» от
    #: «проверить не смог».
    #:
    #: Ранг штрафует `mono` и `icon` (`matching.py`): проза, набранная
    #: моноширинным шрифтом жёлтым по чёрному, соблюдает правила шаблона и
    #: выглядит фрагментом кода. Найдено растром, `DOM-TEXT §11`, `§12`.
    typeface_kind: str | None = None
    #: Вид слота под картинку: `illustration` — место под иллюстрацию,
    #: `icon` — пиктограмма или логотип мельче порога, `backdrop` — подложка
    #: карточки, под которой лежит её же текст, `background` — фон во весь
    #: слайд. `None` у слотов, которые не под картинку, — и это не «неизвестно»,
    #: а «вопрос не задавался» (`Z-28a`, `PLAN-7.10`, `analyze/picture.py`).
    #:
    #: Нужен потому, что `content_type == "image"` одинаков у всех четырёх, а
    #: класть нашу картинку можно только в первый: замер дал 688 слотов под
    #: картинку по корпусу и **135 годных**.
    picture_kind: str | None = None
    #: Полоса слота, свободная от непрозрачных фигур **поверх** него (`Z-48`,
    #: `PLAN-7.8`). Равна `rect`, когда сверху ничего нет или судить не по чему:
    #: «проверить не смог» — не «заслонено». По ней считается ёмкость, и по ней
    #: же VERIFY судит о заслонении, иначе приёмку не пройти по построению.
    visible: object | None = None

    @property
    def occluded(self) -> bool:
        """Лежит ли поверх слота непрозрачное, отнимая у него ширину."""
        return self.visible is not None and self.visible.cx < self.rect.cx

    def to_json(self) -> dict:
        out = {
            "id": self.id,
            "shape_id": self.shape_id,
            "role": self.role,
            "content_type": self.content_type,
            "rect_emu": {
                "x": self.rect.x, "y": self.rect.y,
                "cx": self.rect.cx, "cy": self.rect.cy,
            },
            "type_role": self.type_role,
            "typeface_kind": self.typeface_kind,
            "picture_kind": self.picture_kind,
            "capacity": self.capacity.to_json() if self.capacity else None,
            "required": self.required,
        }
        if self.occluded:
            out["visible_rect_emu"] = {
                "x": self.visible.x, "y": self.visible.y,
                "cx": self.visible.cx, "cy": self.visible.cy,
            }
        return out


@dataclass(frozen=True)
class OpaqueRegion:
    """Непрозрачный кусок фигуры донора, лежащей поверх текста (`Z-48`).

    Нужен стадии VERIFY. От PowerPoint её детектор получает только габаритный
    бокс, а у декоративного PNG непрозрачна бывает четверть площади: рамка
    карточки VK Tech непрозрачна на 1%, а шестиугольник — на 85% в правой
    трети. Без этого детектор объявлял заслонённым текст, который читается.
    """

    shape_id: str
    rect: object          # analyze.deck.Rect — структурно x/y/cx/cy

    def to_json(self) -> dict:
        return {
            "shape_id": self.shape_id,
            "rect_emu": {
                "x": self.rect.x, "y": self.rect.y,
                "cx": self.rect.cx, "cy": self.rect.cy,
            },
        }


@dataclass(frozen=True)
class Pattern:
    id: str
    kind: str
    donor_part: str
    donor_index: int
    slots: tuple[Slot, ...]
    members: tuple[int, ...]
    cohesion: float | None
    donor_reason: str
    source: str
    #: Донор несёт части, которые **нельзя разделить между двумя клонами**:
    #: диаграмму, внедрённый объект, VML-рисунок. Такой донор допустимо
    #: использовать в колоде **только один раз** — иначе PowerPoint отказывается
    #: открывать файл целиком (`Z-44`, `DOM-PKG §9`).
    #:
    #: Замер 19 сентября: `chart` ×2 и ×3, `oleObject` ×2 и ×3 — файл не
    #: открывается; картинки ×3 — открывается
    #: (`WORKLOG/2026-09-19-z38-baseline.md`).
    exclusive: bool = False
    #: Части донора, которые мы **не знаем как разделять**: всё, кроме картинок,
    #: макета и заметок. Список исключительных типов получен от встреченных
    #: файлов и **неполон по построению**, поэтому повтор донора с незнакомой
    #: частью не запрещается, а сопровождается предупреждением.
    unknown_parts: tuple[str, ...] = ()
    #: Непрозрачные куски фигур донора, накрывающих его текстовые слоты
    #: (`Z-48`). Пусто, когда таких нет или судить не по чему.
    opaque: tuple[OpaqueRegion, ...] = ()

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "exclusive": self.exclusive,
            "donor": {
                "part": self.donor_part,
                "index": self.donor_index if self.donor_index >= 0 else None,
            },
            "slots": [s.to_json() for s in self.slots],
            "opaque": [o.to_json() for o in self.opaque],
            "evidence": {
                "members": list(self.members),
                "cohesion": self.cohesion,
                "donor_reason": self.donor_reason,
            },
        }


@dataclass(frozen=True)
class PatternSource:
    filename: str
    sha256: str

    def to_json(self) -> dict:
        return {"filename": self.filename, "sha256": self.sha256}


@dataclass(frozen=True)
class PatternLibrary:
    source: PatternSource
    patterns: tuple[Pattern, ...]
    notes: tuple[str, ...] = ()
    version: str = SCHEMA_VERSION

    def to_json(self) -> dict:
        return {
            "version": self.version,
            "source": self.source.to_json(),
            "patterns": [p.to_json() for p in self.patterns],
            "notes": list(self.notes),
        }


# --- план колоды -------------------------------------------------------
#
# Форма соответствует contracts/deck-plan.schema.json. См. ADR-0002, ADR-0009.


@dataclass(frozen=True)
class Fill:
    """Что подставить в один слот."""

    slot_id: str
    kind: str
    text: str | None = None
    items: tuple[str, ...] | None = None
    ref: str | None = None
    over_capacity: bool = False
    #: Шкала кегля в процентах, которую стадия VERIFY сочла нужной. `None` —
    #: не трогали. Решение живёт здесь, а не в файле: сборка обязана оставаться
    #: чистой функцией от шаблона и плана (`ADR-0005`, `PLAN-4.0`).
    font_scale: int | None = None

    def to_json(self) -> dict:
        return {
            "slot_id": self.slot_id,
            "kind": self.kind,
            "text": self.text,
            "items": list(self.items) if self.items is not None else None,
            "ref": self.ref,
            "font_scale": self.font_scale,
            "over_capacity": self.over_capacity,
        }


@dataclass(frozen=True)
class PlannedSlide:
    index: int
    pattern_id: str
    fills: tuple[Fill, ...]
    reason: str
    origin_section: str | None = None
    origin_part: int | None = None
    origin_of: int | None = None
    notes: str | None = None
    #: Сюжет иллюстрации — идея модели для генератора картинок (`PLAN-9.0`, Ш6;
    #: `ADR-0023`, п. 1). Отбирает её `plan_deck`; сборка поле не читает, рисует
    #: генератор отдельным планом (`Z-28`). `None` — идеи у слайда нет, и тогда
    #: ключа нет и в JSON: план пути без модели не меняется ни ключом.
    image_idea: str | None = None

    def to_json(self) -> dict:
        origin = None
        if self.origin_section is not None:
            origin = {
                "section": self.origin_section,
                "part": self.origin_part,
                "of": self.origin_of,
            }
        out = {
            "index": self.index,
            "pattern_id": self.pattern_id,
            "fills": [f.to_json() for f in self.fills],
            "reason": self.reason,
            "origin": origin,
            "notes": self.notes,
        }
        if self.image_idea:
            out["image_idea"] = self.image_idea
        return out


@dataclass(frozen=True)
class PlanSource:
    """В чём собирался этот план: что разложили, из чего и **чем**.

    Третье добавлено 19 сентября (`Z-33`, `ADR-0022`). Двух `sha256` шаблона
    мало: они опознают вход, но не настройки, а тот же вход при другом
    `config/prose.json` даёт другую колоду. Без `configs` «воспроизводимый
    прогон» из критерия 2 ТЗ проверить по артефакту нельзя.
    """

    content: str
    design_system_sha256: str
    patterns_sha256: str
    configs: tuple[ConfigStamp, ...] = ()

    def to_json(self) -> dict:
        return {
            "content": self.content,
            "design_system_sha256": self.design_system_sha256,
            "patterns_sha256": self.patterns_sha256,
            "configs": [c.to_dict() for c in self.configs],
        }


@dataclass(frozen=True)
class DeckPlan:
    source: PlanSource
    slides: tuple[PlannedSlide, ...]
    planner: str = "deterministic"
    unplaced: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    version: str = SCHEMA_VERSION

    def to_json(self) -> dict:
        return {
            "version": self.version,
            "source": self.source.to_json(),
            "slides": [s.to_json() for s in self.slides],
            "diagnostics": {
                "planner": self.planner,
                "unplaced": list(self.unplaced),
                "warnings": list(self.warnings),
            },
        }
