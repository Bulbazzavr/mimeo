"""Флаги модели `--llm` и `--text` (`PLAN-9.0`, Ш2; `ADR-0021`, `ADR-0023`).

Сборка модель ещё не зовёт (Ш3), поэтому проверяется то, что Ш2 обещает сам:
флаги есть там, где строится план; недопустимое значение не проходит ни из
командной строки, ни из конфига прогона; сборка говорит словами, каким путём шла.
"""

from __future__ import annotations

import argparse
import json

import pytest

from mimeo import cli
from mimeo.plan import client


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


def test_marked_up_input_needs_no_model():
    """Размеченный markdown — вызова нет, и о флаге `--text` сказано, а не смолчано."""
    line = cli.model_line(False, "on", True, "improve")
    assert "не нужна" in line and "--text improve не применяется" in line


def test_model_off_is_said_in_words():
    line = cli.model_line(True, "off", True, None)
    assert "выключена (задано при запуске)" in line and "не применяется" not in line


@pytest.mark.parametrize("access", ["cache", "on"])
def test_model_not_wired_yet_is_said_in_words(access):
    """До Ш3 модель колоду не строит — сборка говорит это, а не молчит."""
    line = cli.model_line(True, access, False, None)
    assert f"{access} (умолчание config/model.json)" in line and "без модели" in line


def test_default_access_comes_from_config(monkeypatch):
    """Флага нет — доступ из config/model.json, а не второе умолчание в коде."""
    monkeypatch.setattr(cli, "load_model_config", lambda: client.ClientConfig(access=client.Access.OFF))
    doc = argparse.Namespace(origin="сплошной текст")
    line = cli._model_line(argparse.Namespace(llm=None, text=None), doc)
    assert "выключена (умолчание config/model.json)" in line
