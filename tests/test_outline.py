"""Промпт и схема ответа модели, строящей колоду (`ADR-0023`, `PLAN-9.0`, Ш1)."""

import json
import os
import re

import pytest

from mimeo.plan import outline

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _measured_blocks() -> list[str]:
    """Три блока кода из записи замера: общий текст, тезисы «как есть» и «доработать»."""
    path = os.path.join(ROOT, "WORKLOG", "2026-09-24-z57-prompt.md")
    text = open(path, encoding="utf-8").read()
    return re.findall(r"```\n(.*?)\n```", text, re.S)[:3]


@pytest.mark.parametrize("mode, block", [("keep", 1), ("improve", 2)])
def test_prompt_is_the_measured_one(mode, block):
    """Продукт шлёт ровно тот промпт, которым сняты числа, при рамках замера 10–15."""
    blocks = _measured_blocks()
    expected = blocks[0].replace("{theses}", blocks[block] + "\n")
    assert outline.system_prompt(mode, 10, 15) == expected


def test_builtin_prompt_equals_config(tmp_path):
    common, theses, loaded = outline.load_config()
    assert loaded
    assert (common, theses) == (outline._SYSTEM, outline._THESES)
    fallback = outline.load_config(str(tmp_path / "нет.json"))
    assert fallback == (outline._SYSTEM, outline._THESES, False)


def test_bounds_reach_the_prompt():
    prompt = outline.system_prompt("keep", 3, 7)
    assert "(от 3 до 7;" in prompt and "{" not in prompt


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        outline.system_prompt("as_is", 10, 15)


def test_kinds_are_the_code_vocabulary():
    """Тип слайда — из словаря kind'ов макетов, а не выдуман рядом (`ADR-0023`, п. 2)."""
    schema = json.load(open(os.path.join(ROOT, "contracts", "pattern-library.schema.json"), encoding="utf-8"))
    enums = [node["enum"] for node in _walk(schema) if isinstance(node, dict) and "cover" in node.get("enum", [])]
    assert enums and set(outline.KINDS) <= set(enums[0])
    slide = outline.RESPONSE_SCHEMA["properties"]["slides"]["items"]
    assert slide["properties"]["kind"]["enum"] == list(outline.KINDS)
    assert set(slide["required"]) == set(slide["properties"])


def _walk(node):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)
