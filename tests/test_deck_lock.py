"""Замок Ш0: сверка колод без модели с эталоном (`PLAN-9.0`, `tools/deck_lock.py`).

Сами колоды собираются из шаблонов, которых в репозитории нет, поэтому здесь
проверяется то, что можно проверить без них, — **сверка**: те же байты дают 0,
разошедшаяся колода — 1, «проверить не смог» — 2 и никогда не 0. Проверка,
которая не отличает «не смог» от «чисто», хуже отсутствующей (`CLAUDE.md`).
"""

from __future__ import annotations

import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def lock_tool(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "deck_lock", os.path.join(ROOT, "tools", "deck_lock.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "LOCK", str(tmp_path / "deck_lock.json"))
    monkeypatch.setattr(mod, "_code", lambda: "test")
    return mod


def _deck(files: dict[str, str], exit_code: int = 0, template: str = "t" * 64) -> dict:
    return {"template_sha256": template, "content_sha256": "c" * 64, "flags": [],
            "exit": exit_code, "files": files}


GOT = {
    "nine|a.pptx": _deck({"a-1.pptx": "1", "a-2.pptx": "2", "a-3.pptx": "3"}),
    "single|x.md|b.pptx": _deck({"b.pptx": "4"}, exit_code=2),
}


def test_same_bytes_pass(lock_tool):
    assert lock_tool.write("compose", GOT) == 0
    assert lock_tool.check("compose", json.loads(json.dumps(GOT))) == 0


def test_changed_deck_is_named(lock_tool, capsys):
    lock_tool.write("compose", GOT)
    got = json.loads(json.dumps(GOT))
    got["nine|a.pptx"]["files"]["a-2.pptx"] = "X"
    assert lock_tool.check("compose", got) == 1
    assert "a-2.pptx" in capsys.readouterr().out


def test_changed_build_status_is_a_difference(lock_tool):
    """Код сборки хранится вместе с байтами: структурная проблема, которая
    появилась или ушла, — тоже расхождение."""
    lock_tool.write("compose", GOT)
    got = json.loads(json.dumps(GOT))
    got["single|x.md|b.pptx"]["exit"] = 0
    assert lock_tool.check("compose", got) == 1


@pytest.mark.parametrize("spoil", ["missing", "error", "other_template", "extra_deck_only"])
def test_cannot_check_is_never_clean(lock_tool, spoil):
    lock_tool.write("compose", GOT)
    got = json.loads(json.dumps(GOT))
    if spoil == "missing":
        del got["single|x.md|b.pptx"]
    elif spoil == "error":
        got["single|x.md|b.pptx"] = {"error": "сборка не вышла"}
    elif spoil == "other_template":
        got["nine|a.pptx"]["template_sha256"] = "z" * 64
    else:
        got = {}
    assert lock_tool.check("compose", got) == 2


def test_no_lock_means_cannot_check(lock_tool):
    assert lock_tool.check("compose", GOT) == 2


def test_write_refuses_a_broken_build(lock_tool):
    got = dict(GOT, **{"single|y.md|c.pptx": {"error": "сборка не вышла"}})
    assert lock_tool.write("compose", got) == 2
    assert not os.path.exists(lock_tool.LOCK)


def test_layers_are_kept_apart(lock_tool):
    """Слой verify снят на машине с PowerPoint; запись compose его не стирает."""
    lock_tool.write("verify", GOT)
    lock_tool.write("compose", {"nine|a.pptx": GOT["nine|a.pptx"]})
    assert lock_tool.check("verify", json.loads(json.dumps(GOT))) == 0
