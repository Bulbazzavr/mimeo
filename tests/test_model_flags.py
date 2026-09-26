"""Флаги модели `--llm` и `--text` (`PLAN-9.0`, Ш2; `ADR-0021`, `ADR-0023`).

Что Ш2 обещает сам: флаги есть там, где строится план; недопустимое значение
не проходит ни из командной строки, ни из конфига прогона; сборка говорит
словами, каким путём шла. Сам путь модели (Ш3) — `test_model_path.py`.
"""

from __future__ import annotations

import argparse
import json

import pytest

from mimeo import cli
from mimeo.plan import client, outline
from mimeo.plan.content import ContentDoc


def _parse(*argv):
    return cli.build_parser().parse_args(list(argv))


@pytest.mark.parametrize("command", ["plan", "build"])
def test_flags_exist_where_the_plan_is_built(command):
    args = _parse(command, "t.pptx", "c.md", "--llm", "on", "--text", "improve")
    assert (args.llm, args.text) == ("on", "improve")


def test_defaults_are_left_to_config_and_user():
    """Без флага — `None`: доступ возьмётся из config/model.json, режим выберет пользователь."""
    args = _parse("build", "t.pptx", "c.md")
    assert args.llm is None and args.text is None


@pytest.mark.parametrize("flag, value", [("--llm", "maybe"), ("--text", "rewrite")])
def test_unknown_value_is_refused(flag, value):
    with pytest.raises(SystemExit):
        _parse("build", "t.pptx", "c.md", flag, value)


def test_run_config_value_is_checked(tmp_path):
    """`choices` на конфиг прогона не действуют — проверка своя (замер Ш2)."""
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"llm": "maybe"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="maybe"):
        cli.main(["build", "t.pptx", "c.md", "--config", str(run), "-q"])


def test_command_line_beats_run_config(tmp_path):
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"llm": "off", "text": "keep"}), encoding="utf-8")
    argv = ["build", "t.pptx", "c.md", "--config", str(run), "--llm", "on"]
    args = _parse(*argv)
    cli.apply_run_config(args, argv)
    assert (args.llm, args.text) == ("on", "keep")


def _doc(origin: str) -> ContentDoc:
    return ContentDoc(name="c.md", origin=origin)


def test_marked_up_input_needs_no_model():
    """Размеченный markdown — вызова нет, и о флаге `--text` сказано, а не смолчано."""
    got = outline.run("c.md", _doc(""), access="on", text_mode="improve")
    assert got.status == "markup"
    assert "не нужна" in got.line and "--text improve не применяется" in got.line


def test_model_off_is_said_in_words():
    got = outline.run("c.md", _doc("сплошной текст"), access="off")
    assert got.status == "off"
    assert "выключена (задано при запуске)" in got.line and "не применяется" not in got.line


def test_cache_miss_is_said_in_words(tmp_path):
    """Промах кэша — путь без модели, и сборка говорит это, а не молчит."""
    text = tmp_path / "c.md"
    text.write_text("Сплошной текст про движок.", encoding="utf-8")
    config = client.ClientConfig(access=client.Access.CACHE, cache_root=str(tmp_path / "cache"))
    got = outline.run(str(text), _doc("сплошной текст"), config=config)
    assert got.status == "no_answer"
    assert "cache (умолчание config/model.json)" in got.line and "без модели" in got.line
    assert "текст improve (умолчание)" in got.line, "умолчание режима названо, а не подразумевается"


def test_default_access_comes_from_config(monkeypatch, tmp_path):
    """Флага нет — доступ из config/model.json, а не второе умолчание в коде."""
    monkeypatch.setattr(cli, "load_model_config", lambda: client.ClientConfig(access=client.Access.OFF))
    args = argparse.Namespace(content="c.md", llm=None, text=None, out=str(tmp_path))
    outcome, record = cli.model_path(args, _doc("сплошной текст"), None)
    assert "выключена (умолчание config/model.json)" in outcome.line
    assert json.load(open(record, encoding="utf-8"))["status"] == "off"
