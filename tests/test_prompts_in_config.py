"""Промпты лежат только в `config/`, копий в коде нет (`Z-72`).

ТЗ, раздел 4: «Промпты\\конфиги скиллов и агентов в репозитории лежат
отдельными файлами, т.е. не зашиты в код»; организаторы 17 сентября назвали
это минимумом. До 27 сентября пять промптов жили ещё и запасными копиями в
коде — без файла движок молча работал на копии. Теперь без файла — понятная
ошибка (`config.MissingConfig`), и `cli.main` её печатает.
"""

from __future__ import annotations

import glob
import json
import os

import pytest

from mimeo import cli
from mimeo.audit import vision
from mimeo.config import MissingConfig, config_root
from mimeo.plan import donor, icons, images, outline, prompt

LOADERS = {
    "outline.json": outline.load_config,
    "outline.json/retry": outline.retry_config,
    "prompt.json": prompt.load_config,
    "audit.json/donor_pictures": donor.load_config,
    "audit.json/slides": vision.load_config,
    "generator.json": images.load_config,
    "icons.json": icons.load_config,
}


@pytest.mark.parametrize("name", sorted(LOADERS))
def test_missing_file_is_an_error_not_a_copy(name, tmp_path):
    with pytest.raises(MissingConfig) as err:
        LOADERS[name](str(tmp_path / "нет.json"))
    assert "config/" in str(err.value) and "git checkout" in str(err.value)


@pytest.mark.parametrize("name", sorted(LOADERS))
def test_file_without_prompt_is_an_error(name, tmp_path):
    """Файл есть, промпта в нём нет — тоже ошибка, а не пустой промпт."""
    path = tmp_path / "пусто.json"
    path.write_text(json.dumps({"version": "1.0", "donor_pictures": {}, "slides": {}}), encoding="utf-8")
    if name == "generator.json":
        gen = images.load_config(str(path))
        with pytest.raises(MissingConfig):
            images.scenes(["идея"], gen, model_config=None)
        return
    with pytest.raises(MissingConfig):
        LOADERS[name](str(path))


def _prompt_texts() -> list[str]:
    """Промпты и реплики из конфигов — все строки длиннее 60 знаков в
    полях, которые уходят модели."""
    out = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if not key.startswith("_") and not key.endswith("__"):
                    walk(value)
        elif isinstance(node, list):
            if all(isinstance(x, str) for x in node):
                out.append("".join(node))
            else:
                for x in node:
                    walk(x)
        elif isinstance(node, str) and len(node) > 60:
            out.append(node)

    for name in ("outline.json", "prompt.json", "audit.json", "generator.json", "icons.json"):
        with open(os.path.join(config_root(), name), encoding="utf-8") as fh:
            walk(json.load(fh))
    return out


def test_no_prompt_is_copied_into_the_engine():
    """Ни один промпт конфига не повторён в коде движка даже началом фразы."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(cli.__file__)))
    code = "".join(open(p, encoding="utf-8").read()
                   for p in glob.glob(os.path.join(root, "mimeo", "**", "*.py"), recursive=True))
    texts = _prompt_texts()
    assert len(texts) >= 5
    copied = [t[:50] for t in texts if t[:50] in code]
    assert not copied, f"промпт в коде: {copied}"


def test_cli_says_what_is_missing(monkeypatch, capsys):
    def boom(args):
        raise MissingConfig("config/outline.json: файла нет")

    parser = cli.build_parser()
    args = parser.parse_args(["analyze", "t.pptx"])
    monkeypatch.setattr(cli, "build_parser", lambda: _Fixed(parser, args, boom))
    assert cli.main(["analyze", "t.pptx"]) == 1
    assert "config/outline.json" in capsys.readouterr().err


class _Fixed:
    def __init__(self, parser, args, func):
        self.parser, self.args, self.func = parser, args, func

    def parse_args(self, argv=None):
        self.args.func = self.func
        return self.args
