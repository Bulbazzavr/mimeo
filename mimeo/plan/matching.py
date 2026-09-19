"""Подбор паттернов под кусок контента. Шаг Ш3 плана `PLAN-2.0`.

Разделение труда с моделью — главное решение этого модуля (`ADR-0009`):

* **пригодность считает код.** Хватает ли слотов, влезает ли текст, есть ли
  материал под слот изображения — это арифметика, и модели тут делать нечего;
* **выбор из пригодных делает модель.** Что «сравнение выручки по годам»
  просится в две колонки, а не в список, — это смысл, и его код не понимает.

Детерминированный планировщик (`deterministic.py`) берёт первый по рангу
пригодный. Модель, когда появится, выберет из тех же пригодных — но список ей
готовит этот модуль.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model import Fill, Pattern, Slot
from .content import ContentBlock, ContentSection

#: Роли слотов по назначению.
_TITLE_ROLES = ("title",)
_SUBTITLE_ROLES = ("subtitle",)
_TEXT_ROLES = ("body", "bullet_list", "caption", "label", "quote", "attribution")
_METRIC_VALUE_ROLES = ("metric_value",)
_METRIC_LABEL_ROLES = ("metric_label", "caption", "label")

#: Насколько поднимает ранг совпадение вида паттерна с ожидаемым.
_BONUS_BEST_KIND = 0.40
_BONUS_ANY_KIND = 0.18

#: Штраф за незакрытый обязательный слот и за слот, заполненный «на донышке».
_PENALTY_EMPTY_SLOT = 0.15
_PENALTY_SLACK = 0.10

#: Ниже этой доли от ориентира слот считается заполненным «на донышке».
_SLACK_RATIO = 0.25

#: Переполнение слота допускается и штрафуется, а не запрещается.
#:
#: Правило «влезает точно либо паттерн непригоден» на реальных шаблонах
#: отвергало всё: слоты подогнаны под собственные короткие тексты дизайнера, и
#: в обложечный заголовок шаблона на холсте 26.7 дюйма при кегле 138 pt влезает
#: девять знаков. Замер: 4 раздела демо-контента из 5 не размещались вовсе.
#:
#: Гарантировать, что ничего не торчит, — работа стадии VERIFY (`ADR-0005`):
#: она ужимает кегль по растру. Дублировать её здесь значит делать систему
#: хрупкой ради чужой ответственности. Здесь мы стремимся уложиться, а не
#: обязаны: переполнение до порога штрафуется и помечается, сверх порога —
#: слот действительно не годится.
_OVERFLOW_HARD = 2.5
_PENALTY_OVERFLOW = 0.35

#: Штраф за повтор раскладки. Живёт здесь, а не в `deterministic`, чтобы все
#: три настраиваемые величины ранга лежали в одном месте и в одном объекте.
_REPEAT_PENALTY = 0.25


@dataclass(frozen=True)
class Tuning:
    """Три величины ранга, которые `ADR-0020` перебирает политиками.

    **Умолчания равны тому, что стоит в продукте**, поэтому вызов без `tuning`
    ведёт себя ровно как раньше. Это не вежливость к старому коду: девять
    сдаточных колод собираются той же командой, и молчаливая смена умолчания
    испортила бы их незаметно.

    Что сюда **не входит и не должно**: пороги весов (`quality.py`). Весы — это
    линейка, и если каждый вариант мерить своей, сравнение вариантов теряет
    смысл. Линейка одна на всех, политика меняет только ранг.
    """

    repeat: float = _REPEAT_PENALTY
    slack: float = _SLACK_RATIO
    over: float = _PENALTY_OVERFLOW


#: Настройка «как в продукте». Отдельным именем, чтобы в коде было видно, что
#: умолчание выбрано, а не забыто.
DEFAULT_TUNING = Tuning()


@dataclass(frozen=True)
class Match:
    pattern_id: str
    kind: str
    fills: tuple[Fill, ...]
    score: float
    reason: str
    leftover: tuple[str, ...]

    @property
    def fits(self) -> bool:
        return not self.leftover


# --- ожидаемый вид раскладки -------------------------------------------


def preferred_kinds(section: ContentSection) -> tuple[str, ...]:
    """Какие раскладки просит этот кусок контента. Первая — самая подходящая."""
    kinds = section.kinds()
    lists = [b for b in section.blocks if b.kind == "list"]

    if "metric" in kinds:
        return ("metric", "cards", "text")
    if "table" in kinds:
        return ("table", "text")
    if "quote" in kinds:
        return ("quote", "text")
    if "image" in kinds and ("paragraph" in kinds or "list" in kinds):
        return ("image_text", "image_full", "text")
    if "image" in kinds:
        return ("image_full", "image_text")
    if lists:
        items = lists[0].items
        short = items and max(len(i) for i in items) <= 80
        if 2 <= len(items) <= 5 and short:
            return ("cards", "bullets", "two_column", "text")
        return ("bullets", "text", "cards")
    paragraphs = [b for b in section.blocks if b.kind == "paragraph"]
    if len(paragraphs) == 2:
        a, b = (len(p.text) for p in paragraphs)
        if max(a, b) <= min(a, b) * 1.6:
            return ("two_column", "text", "bullets")
    if not section.blocks:
        return ("section", "cover", "closing", "text")
    return ("text", "bullets", "two_column")


# --- назначение --------------------------------------------------------


def _by_role(slots: tuple[Slot, ...], roles: tuple[str, ...]) -> list[Slot]:
    return [s for s in slots if s.role in roles]


def _limit(slot: Slot) -> int | None:
    return slot.capacity.max_chars if slot.capacity else None


def _target(slot: Slot) -> int | None:
    return slot.capacity.target_chars if slot.capacity else None


def _overflow(slot: Slot, text: str) -> float:
    """На сколько текст превышает ёмкость слота. 0 — влезает."""
    cap = _limit(slot)
    if not cap:
        return 0.0
    return max(0.0, len(text) / cap - 1.0)


def _fits(slot: Slot, text: str) -> bool:
    """Годится ли слот вообще. Умеренное переполнение — годится."""
    return _overflow(slot, text) <= _OVERFLOW_HARD - 1.0


def _max_items(slot: Slot) -> int | None:
    return slot.capacity.max_items if slot.capacity else None


def _pick(slots: list[Slot], text: str) -> Slot | None:
    """Первый по порядку слот, в который текст действительно влезает.

    Раньше брался просто `slots[0]`, и раскладка объявлялась непригодной, если
    её первый текстовый слот мал. Замер (`PLAN-2.1`, шаг 1): у раскладки `p03`
    десять текстовых слотов — ёмкости 5, 5, 65, 5, 5, 65, 5, 5, 5, 65, — где
    пятёрки это номерные бейджи. Абзац в 142 знака влезал в любой из слотов на
    65, но проверялся только бейдж, и весь паттерн отсеивался. Так пул
    пригодных раскладок сужался до двух-трёх, а из узкого пула шли повторы.

    Порядок обхода сохраняется: берётся первый подходящий, а не самый крупный.
    Иначе текст уезжал бы в случайное место раскладки и ломал чтение.
    """
    for slot in slots:
        if _fits(slot, text):
            return slot
    return None


def match(
    section: ContentSection, pattern: Pattern, tuning: Tuning = DEFAULT_TUNING
) -> Match | None:
    """Пробует уложить раздел в паттерн. None — если не годится в принципе."""
    used: set[str] = set()
    fills: list[Fill] = []
    leftover: list[str] = []
    slack: list[float] = []
    overflows: list[float] = []

    def take(slot: Slot, text_for_fit: str = "", **kwargs) -> None:
        used.add(slot.id)
        over = _overflow(slot, text_for_fit) if text_for_fit else 0.0
        overflows.append(over)
        fills.append(Fill(slot_id=slot.id, over_capacity=over > 0, **kwargs))

    def note_slack(slot: Slot, length: int) -> None:
        target = _target(slot)
        if target:
            slack.append(min(1.0, length / target))

    free = lambda roles: [s for s in _by_role(pattern.slots, roles) if s.id not in used]  # noqa: E731

    # 1. Заголовок раздела.
    if section.heading:
        head_slots = (
            free(_TITLE_ROLES) or free(_SUBTITLE_ROLES)
            or [s for s in free(("body", "label", "caption")) if s.content_type == "text"]
        )
        head_slot = _pick(head_slots, section.heading)
        if head_slot is not None:
            take(head_slot, text_for_fit=section.heading, kind="text", text=section.heading)
            note_slack(head_slot, len(section.heading))
        else:
            leftover.append(f"{section.id}:heading")

    # 2. Метрики: число в свой слот, подпись — в соседний.
    #
    #    Если плашки под число в шаблоне нет вовсе, метрика становится обычным
    #    текстом. Замер показал, зачем: слот `metric_value` был всего у трёх
    #    паттернов из четырнадцати, самых плотных, и требование «только туда»
    #    загоняло планировщик в раскладку на двадцать слотов ради двух блоков
    #    контента. Терять содержимое из-за отсутствия плашки нельзя.
    for block in (b for b in section.blocks if b.kind == "metric"):
        value_slots = free(_METRIC_VALUE_ROLES)
        if value_slots and _fits(value_slots[0], block.value or ""):
            take(value_slots[0], text_for_fit=block.value or "", kind="number", text=block.value)
            note_slack(value_slots[0], len(block.value or ""))
            if block.label:
                label_slots = free(_METRIC_LABEL_ROLES)
                if label_slots and _fits(label_slots[0], block.label):
                    take(label_slots[0], text_for_fit=block.label, kind="text", text=block.label)
            continue

        merged = block.value or ""
        if block.label:
            merged = f"{merged} \u2014 {block.label}"
        text_slots = [s for s in free(_TEXT_ROLES) if s.content_type == "text"]
        slot = _pick(text_slots, merged)
        if slot is not None:
            take(slot, text_for_fit=merged, kind="text", text=merged)
            note_slack(slot, len(merged))
        else:
            leftover.append(block.id)

    # 3. Изображения, таблицы, диаграммы — только если слот под них есть.
    for block in (b for b in section.blocks if b.kind in ("image", "table")):
        role = "image" if block.kind == "image" else "table"
        slots = free((role,))
        if not slots:
            leftover.append(block.id)
            continue
        take(slots[0], kind=role, ref=block.ref, text=block.text or None)

    # 4. Списки. Либо один слот-список целиком, либо по слоту на пункт.
    for block in (b for b in section.blocks if b.kind == "list"):
        bullet_slots = [s for s in free(_TEXT_ROLES) if s.content_type == "list"]
        whole = next(
            (
                s for s in bullet_slots
                if len(block.items) <= (_max_items(s) or len(block.items))
                and all(_fits(s, i) for i in block.items)
            ),
            None,
        )
        if whole is not None:
            take(whole, text_for_fit=max(block.items, key=len),
                 kind="list", items=tuple(block.items))
            note_slack(whole, block.length)
            continue

        # По слоту на пункт. Слоты перебираются по порядку, но негодные по
        # ёмкости пропускаются (`PLAN-2.1`, шаг 1): раньше пункт жёстко ложился
        # в слот с тем же номером, и раскладка с номерными бейджами впереди
        # отсеивалась целиком.
        text_slots = [s for s in free(_TEXT_ROLES) if s.content_type == "text"]
        chosen: list[Slot] = []
        rest = list(text_slots)
        for item in block.items:
            slot = _pick(rest, item)
            if slot is None:
                chosen = []
                break
            chosen.append(slot)
            rest = [s for s in rest if s.id != slot.id]
        if chosen:
            for slot, item in zip(chosen, block.items):
                take(slot, text_for_fit=item, kind="text", text=item)
                note_slack(slot, len(item))
        else:
            leftover.append(block.id)

    # 5. Абзацы и цитаты — в оставшиеся текстовые слоты по порядку. Если
    #    текстовых не осталось, годится и слот-список: абзац станет одним
    #    пунктом. На реальных шаблонах слоты-списки самые вместительные.
    for block in (b for b in section.blocks if b.kind in ("paragraph", "quote")):
        text_slots = [s for s in free(_TEXT_ROLES) if s.content_type == "text"]
        list_slots = [s for s in free(_TEXT_ROLES) if s.content_type == "list"]
        as_text = _pick(text_slots, block.text)
        as_list = _pick(list_slots, block.text)
        if as_text is not None:
            take(as_text, text_for_fit=block.text, kind="text", text=block.text)
            note_slack(as_text, len(block.text))
        elif as_list is not None:
            take(as_list, text_for_fit=block.text, kind="list", items=(block.text,))
            note_slack(as_list, len(block.text))
        else:
            leftover.append(block.id)

    if not fills:
        return None

    # --- ранг ---
    total_units = sum(b.units for b in section.blocks) + (1 if section.heading else 0)
    placed_units = sum(len(f.items or ()) if f.kind == "list" else 1 for f in fills)
    coverage = placed_units / total_units if total_units else 1.0

    wanted = preferred_kinds(section)
    if wanted and pattern.kind == wanted[0]:
        affinity = _BONUS_BEST_KIND
    elif pattern.kind in wanted:
        affinity = _BONUS_ANY_KIND
    else:
        affinity = 0.0

    empty_required = sum(
        1 for s in pattern.slots if s.required and s.id not in used and s.content_type != "image"
    )
    thin = sum(1 for ratio in slack if ratio < tuning.slack)
    over = sum(overflows) / len(overflows) if overflows else 0.0

    score = (
        coverage
        + affinity
        - _PENALTY_EMPTY_SLOT * empty_required
        - _PENALTY_SLACK * (thin / max(1, len(slack)))
        - tuning.over * over
    )

    return Match(
        pattern_id=pattern.id,
        kind=pattern.kind,
        fills=tuple(fills),
        score=round(score, 4),
        reason=_reason(section, pattern, placed_units, empty_required, wanted, over),
        leftover=tuple(leftover),
    )


def _reason(
    section: ContentSection,
    pattern: Pattern,
    placed: int,
    empty: int,
    wanted: tuple[str, ...],
    over: float = 0.0,
) -> str:
    """Человекочитаемое обоснование. Это показывают эксперту, а не пишут в лог."""
    shape = []
    lists = [b for b in section.blocks if b.kind == "list"]
    if lists:
        shape.append(f"{len(lists[0].items)} равноправных пункта")
    metrics = [b for b in section.blocks if b.kind == "metric"]
    if metrics:
        shape.append(f"{len(metrics)} числовых показателя")
    paragraphs = [b for b in section.blocks if b.kind == "paragraph"]
    if paragraphs:
        shape.append(f"{len(paragraphs)} абзаца текста")
    if not shape:
        shape.append("только заголовок")

    head = ", ".join(shape)
    fit = "вид совпал" if wanted and pattern.kind == wanted[0] else f"вид «{pattern.kind}»"
    tail = f", {empty} слот(ов) осталось пустыми" if empty else ""
    if over > 0.02:
        tail += f", текст плотнее ёмкости на {over:.0%} — ужмётся на проверке вёрстки"
    return f"{head} — {fit}, заполнено {placed} мест{tail}"


def rank(
    section: ContentSection,
    patterns: tuple[Pattern, ...],
    tuning: Tuning = DEFAULT_TUNING,
) -> list[Match]:
    """Пригодные паттерны, от лучшего к худшему. Порядок детерминирован."""
    found = []
    for pattern in patterns:
        m = match(section, pattern, tuning)
        if m is not None and m.fits:
            found.append(m)
    return sorted(found, key=lambda m: (-m.score, m.pattern_id))
