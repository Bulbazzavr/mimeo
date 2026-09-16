"""Промпт-контракт. Шаг Ш5 плана `PLAN-2.0`, обоснование в `ADR-0010`.

Здесь описано **что уходит в модель и что ожидается назад**, а не как именно
сформулированы инструкции. Формулировки поменяются под конкретную модель за
полчаса; конструкция — нет.

Живого вызова здесь нет намеренно: неизвестно, какая модель стоит за выданным
API (`OQ-02`, `OQ-03`, `OQ-11`). Клиент подставляется снаружи, любой объект с
методом, принимающим `Request` и возвращающим строку.

Модель решает ровно две вещи: **какой паттерн из пригодных взять** и **какой
текст написать в слоты**. Пригодность считает код (`matching.py`), геометрию
модель не видит и видеть не должна (`ADR-0002`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

from ..model import DesignSystem
from .content import ContentSection
from .matching import Match

#: Сколько пригодных паттернов показываем модели. Больше — только шум: они
#: отсортированы по пригодности, и хвост заведомо хуже.
MAX_CANDIDATES = 6


class Mode(str, Enum):
    """Как просить структурированный ответ.

    Порядок — от лучшего к запасному. Режим определяется один раз пробным
    запросом и дальше не меняется. Валидатор одинаков во всех трёх: доверять
    режиму нельзя ни в одном из них. Разведка по поддержке — в
    `WORKLOG/2026-09-09-stage-plan.md`.
    """

    JSON_SCHEMA = "json_schema"   # response_format с JSON Schema
    TOOL_CALL = "tool_call"       # вызов инструмента с той же схемой
    FREE_TEXT = "free_text"       # обычный текст, JSON вынимаем сами


#: Схема ответа модели на один слайд. Намеренно уже, чем `deck-plan`: модель не
#: назначает индексы, не проставляет origin и не решает, переполнен ли слот.
RESPONSE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pattern_id", "fills"],
    "properties": {
        "pattern_id": {
            "type": "string",
            "description": "Идентификатор одного из предложенных паттернов. Придумывать нельзя.",
        },
        "fills": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["slot_id", "kind"],
                "properties": {
                    "slot_id": {"type": "string"},
                    "kind": {"type": "string", "enum": ["text", "list", "number"]},
                    "text": {"type": ["string", "null"]},
                    "items": {"type": ["array", "null"], "items": {"type": "string"}},
                },
            },
        },
        "reason": {
            "type": "string",
            "description": "Одно предложение: почему выбрана эта раскладка.",
        },
    },
}

_SYSTEM = (
    "Ты раскладываешь готовый контент по слайдам презентации. "
    "Дизайн уже задан шаблоном, менять его нельзя.\n\n"
    "Правила, нарушение любого делает ответ негодным:\n"
    "1. Выбери pattern_id строго из предложенных. Не придумывай новых.\n"
    "2. Заполняй только перечисленные слоты этого паттерна, по slot_id.\n"
    "3. Соблюдай max_chars для каждого слота. target_chars — ориентир, к нему стоит стремиться.\n"
    "4. Не выдумывай фактов, которых нет во входном тексте. Сокращать и "
    "переформулировать можно, добавлять новое нельзя.\n"
    "5. Отвечай только JSON по схеме, без пояснений вокруг."
)


@dataclass(frozen=True)
class Request:
    """Всё, что нужно, чтобы отправить запрос любым из трёх способов."""

    mode: Mode
    system: str
    user: str
    schema: dict
    section_id: str
    candidates: tuple[str, ...]

    def as_messages(self) -> list[dict]:
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user},
        ]


def slot_brief(match: Match, patterns_by_id: dict) -> list[dict]:
    """Компактное описание слотов паттерна: только то, что нужно модели."""
    pattern = patterns_by_id[match.pattern_id]
    out = []
    for slot in pattern.slots:
        if slot.content_type not in ("text", "list", "number"):
            continue
        entry: dict = {"slot_id": slot.id, "role": slot.role, "kind": slot.content_type}
        if slot.capacity:
            entry["max_chars"] = slot.capacity.max_chars
            if slot.capacity.target_chars:
                entry["target_chars"] = slot.capacity.target_chars
            if slot.capacity.max_items:
                entry["max_items"] = slot.capacity.max_items
        out.append(entry)
    return out


def catalogue(matches: list[Match], patterns_by_id: dict) -> list[dict]:
    """Каталог пригодных раскладок для модели. Геометрии здесь нет."""
    return [
        {
            "pattern_id": m.pattern_id,
            "kind": m.kind,
            "slots": slot_brief(m, patterns_by_id),
        }
        for m in matches[:MAX_CANDIDATES]
    ]


def _section_payload(section: ContentSection) -> dict:
    blocks = []
    for block in section.blocks:
        entry: dict = {"kind": block.kind}
        if block.kind == "list":
            entry["items"] = list(block.items)
        elif block.kind == "metric":
            entry["value"] = block.value
            entry["label"] = block.label
        else:
            entry["text"] = block.text
        blocks.append(entry)
    return {"heading": section.heading, "blocks": blocks}


def build_request(
    section: ContentSection,
    matches: list[Match],
    patterns_by_id: dict,
    design_system: DesignSystem | None = None,
    mode: Mode = Mode.JSON_SCHEMA,
) -> Request:
    """Собирает запрос на один слайд."""
    payload = {
        "content": _section_payload(section),
        "patterns": catalogue(matches, patterns_by_id),
    }
    if design_system is not None:
        payload["deck_language_hint"] = {
            "canvas": design_system.slide.aspect,
            "typography": [
                {"role": t.role, "size_norm": t.size_norm} for t in design_system.type_scale[:6]
            ],
        }
    user = json.dumps(payload, ensure_ascii=False, indent=2)
    if mode is Mode.FREE_TEXT:
        user += "\n\nОтветь одним JSON-объектом по схеме:\n" + json.dumps(
            RESPONSE_SCHEMA, ensure_ascii=False
        )
    return Request(
        mode=mode,
        system=_SYSTEM,
        user=user,
        schema=RESPONSE_SCHEMA,
        section_id=section.id,
        candidates=tuple(m.pattern_id for m in matches[:MAX_CANDIDATES]),
    )
