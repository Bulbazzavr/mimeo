"""Разбор, проверка и ремонт ответа модели. Шаг Ш6 плана `PLAN-2.0`.

Ответу модели не доверяем ни в одном из трёх режимов (`ADR-0010`). Даже когда
API обещает соблюсти JSON Schema, проверяются и структура, и смысл: существует
ли такой паттерн, есть ли такие слоты, влезает ли текст.

Ремонт чинит то, что чинится механически (лишний слот выкинуть, длинный текст
подрезать по границе слова), и честно сообщает, что именно поправил. Что не
чинится — повод переспросить модель, а если и это не помогло, откатиться на
детерминированный планировщик (`ADR-0009`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..model import Fill, Pattern
from .prompt import Request

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

#: Многоточие при подрезке. Один символ, чтобы не съедать ёмкость.
_ELLIPSIS = "…"


@dataclass(frozen=True)
class Problem:
    code: str
    detail: str
    fatal: bool = False


def extract_json(raw: str) -> dict | None:
    """Достаёт объект JSON из ответа любой аккуратности.

    Модели в режиме свободного текста регулярно оборачивают ответ в тройные
    кавычки или добавляют фразу до и после. Это не повод терять ответ.
    """
    if not raw:
        return None
    text = raw.strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start, depth = text.find("{"), 0
    if start < 0:
        return None
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start:i + 1])
                    return value if isinstance(value, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _trim(text: str, limit: int) -> str:
    """Подрезает по границе слова, чтобы не рвать слово посередине."""
    if len(text) <= limit:
        return text
    cut = text[: max(1, limit - 1)]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:—-") + _ELLIPSIS


def check(payload: dict, request: Request, pattern: Pattern | None) -> list[Problem]:
    """Что не так с ответом. Пустой список — ответ годен как есть."""
    problems: list[Problem] = []

    pattern_id = payload.get("pattern_id")
    if pattern_id not in request.candidates:
        problems.append(
            Problem("unknown_pattern", f"pattern_id={pattern_id!r} не из предложенных", fatal=True)
        )
        return problems
    if pattern is None:
        return [Problem("missing_pattern", f"паттерн {pattern_id} не найден", fatal=True)]

    slots = {s.id: s for s in pattern.slots}
    fills = payload.get("fills")
    if not isinstance(fills, list) or not fills:
        return [Problem("no_fills", "в ответе нет ни одного заполнения", fatal=True)]

    seen: set[str] = set()
    for fill in fills:
        slot_id = fill.get("slot_id") if isinstance(fill, dict) else None
        if slot_id not in slots:
            problems.append(Problem("unknown_slot", f"слота {slot_id!r} нет в паттерне"))
            continue
        if slot_id in seen:
            problems.append(Problem("duplicate_slot", f"слот {slot_id} заполнен дважды"))
            continue
        seen.add(slot_id)

        slot = slots[slot_id]
        kind = fill.get("kind")
        if kind != slot.content_type:
            problems.append(
                Problem("kind_mismatch", f"слот {slot_id} ждёт {slot.content_type}, а не {kind}")
            )
        cap = slot.capacity
        if cap is None:
            continue
        if kind == "list":
            items = fill.get("items") or []
            if cap.max_items and len(items) > cap.max_items:
                problems.append(
                    Problem("too_many_items", f"слот {slot_id}: {len(items)} > {cap.max_items}")
                )
            for item in items:
                if len(str(item)) > cap.max_chars:
                    problems.append(
                        Problem("too_long", f"слот {slot_id}: пункт длиннее {cap.max_chars}")
                    )
        else:
            text = fill.get("text") or ""
            if len(text) > cap.max_chars:
                problems.append(
                    Problem("too_long", f"слот {slot_id}: {len(text)} > {cap.max_chars}")
                )

    missing = [s.id for s in pattern.slots if s.required and s.id not in seen]
    if missing:
        problems.append(Problem("missing_required", f"не заполнены слоты: {', '.join(missing)}"))
    return problems


def repair(payload: dict, request: Request, pattern: Pattern) -> tuple[tuple[Fill, ...], list[str]]:
    """Чинит то, что чинится механически. Возвращает заполнения и список правок."""
    slots = {s.id: s for s in pattern.slots}
    fixes: list[str] = []
    fills: list[Fill] = []
    seen: set[str] = set()

    for raw in payload.get("fills") or []:
        if not isinstance(raw, dict):
            continue
        slot_id = raw.get("slot_id")
        slot = slots.get(slot_id)
        if slot is None:
            fixes.append(f"выброшено заполнение несуществующего слота {slot_id!r}")
            continue
        if slot_id in seen:
            fixes.append(f"выброшен повтор слота {slot_id}")
            continue
        seen.add(slot_id)

        kind = slot.content_type
        cap = slot.capacity
        if kind == "list":
            items = [str(i) for i in (raw.get("items") or []) if str(i).strip()]
            if not items and raw.get("text"):
                items = [str(raw["text"])]
                fixes.append(f"слот {slot_id}: текст обёрнут в список из одного пункта")
            if cap and cap.max_items and len(items) > cap.max_items:
                fixes.append(f"слот {slot_id}: {len(items)} пунктов урезано до {cap.max_items}")
                items = items[: cap.max_items]
            if cap:
                trimmed = [_trim(i, cap.max_chars) for i in items]
                if trimmed != items:
                    fixes.append(f"слот {slot_id}: длинные пункты подрезаны")
                items = trimmed
            fills.append(Fill(slot_id=slot_id, kind="list", items=tuple(items)))
            continue

        text = str(raw.get("text") or "")
        if not text and raw.get("items"):
            text = " ".join(str(i) for i in raw["items"])
            fixes.append(f"слот {slot_id}: список склеен в текст")
        if cap and len(text) > cap.max_chars:
            fixes.append(f"слот {slot_id}: текст подрезан с {len(text)} до {cap.max_chars}")
            text = _trim(text, cap.max_chars)
        fills.append(Fill(slot_id=slot_id, kind=kind, text=text))

    missing = [s.id for s in pattern.slots if s.required and s.id not in seen]
    if missing:
        fixes.append(f"остались незаполненными: {', '.join(missing)}")
    return tuple(fills), fixes


def feedback(problems: list[Problem]) -> str:
    """Текст для переспроса. Не «попробуй ещё раз», а что именно не так."""
    lines = ["Предыдущий ответ не принят. Исправь и повтори:"]
    for p in problems:
        lines.append(f"- {p.detail}")
    lines.append("Верни только JSON по схеме.")
    return "\n".join(lines)
