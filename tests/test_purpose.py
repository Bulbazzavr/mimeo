"""Назначение презентации на входе (`Z-37`): фича, продукт, проект, инициатива.

Назначение добавляет к системному промпту строку каркаса из
`config/outline.json`; без него промпт ровно прежний — от этого зависят ключи
кэша ответов модели и сдаточные колоды.
"""

from __future__ import annotations

import ast
import os

import pytest

from mimeo.plan import outline

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_without_purpose_the_prompt_is_unchanged():
    plain = outline.system_prompt("improve", 10, 15)
    assert outline.system_prompt("improve", 10, 15, purpose=None) == plain
    assert outline.system_prompt("improve", 10, 15, purpose="") == plain
    assert "Назначение презентации" not in plain


@pytest.mark.parametrize("purpose", outline.PURPOSES)
def test_purpose_adds_its_skeleton_and_the_boundary(purpose):
    plain = outline.system_prompt("keep", 10, 15)
    got = outline.system_prompt("keep", 10, 15, purpose=purpose)
    line = outline.purpose_line(purpose)
    assert got == plain + "\n" + line
    assert "→" in line, "каркас — порядок ролей"
    # Граница CTX-NARRATIVE: порядок и группировка, а не новые разделы.
    assert "не добавляй" in line and "не придумывай" in line


def test_unknown_purpose_is_refused():
    with pytest.raises(ValueError):
        outline.system_prompt("keep", 10, 15, purpose="pitch")


def test_request_carries_the_purpose():
    req = outline.request("текст", "improve", (10, 15), purpose="project")
    assert outline.purpose_line("project") in req.system


def test_web_offers_exactly_the_engine_purposes():
    """Веб не импортирует движок — список назначений у него свой, и он обязан
    совпадать с `outline.PURPOSES`."""
    tree = ast.parse(open(os.path.join(ROOT, "web", "serve.py"), encoding="utf-8").read())
    found = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "PURPOSES" for t in node.targets):
            found = ast.literal_eval(node.value)
    assert found == outline.PURPOSES
    page = open(os.path.join(ROOT, "web", "static", "index.html"), encoding="utf-8").read()
    for purpose in outline.PURPOSES:
        assert f'value="{purpose}"' in page
