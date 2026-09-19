"""Запуск конфиг-файлом: приоритет, опечатки, понятные отказы.

Требование ТЗ, раздел 6, финальная сдача: «воспроизводимый сетап и запуск
конфиг файлом». Задача `Z-30`, план `PLAN-7.0` Ш10.

Главная из проверок — про приоритет, и она заведена не «на всякий случай».
Наивная реализация сравнивает значение с умолчанием и считает совпадение
признаком «пользователь смолчал». Тогда `--variants 1` — а единица и есть
умолчание — выглядел бы как молчание, и конфиг перебил бы явную просьбу
пользователя. Ровно этот случай стоит первым.
"""

from __future__ import annotations

import json

import pytest

from mimeo.cli import apply_run_config, build_parser


def _write(tmp_path, payload: dict) -> str:
    path = tmp_path / "run.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _run(tmp_path, payload: dict, argv: list[str]):
    path = _write(tmp_path, payload)
    argv = [*argv, "--config", path]
    args = build_parser().parse_args(argv)
    notes = apply_run_config(args, argv)
    return args, notes


def test_config_supplies_everything_including_positionals(tmp_path) -> None:
    """Смысл требования: прогон задаётся одним файлом, а не набором руками."""
    args, _ = _run(
        tmp_path,
        {"template": "t.pptx", "content": "c.md", "out": "o", "variants": 3},
        ["build"],
    )
    assert (args.template, args.content, args.out, args.variants) == ("t.pptx", "c.md", "o", 3)


def test_command_line_wins_even_when_it_repeats_the_default(tmp_path) -> None:
    """`--variants 1` совпадает с умолчанием и всё равно обязан перебить файл."""
    args, notes = _run(
        tmp_path,
        {"template": "t.pptx", "content": "c.md", "variants": 3},
        ["build", "--variants", "1"],
    )
    assert args.variants == 1, "конфиг перебил явный аргумент командной строки"
    assert any("перекрыто командной строкой" in line and "variants" in line for line in notes)


def test_command_line_wins_for_positionals_too(tmp_path) -> None:
    args, _ = _run(
        tmp_path,
        {"template": "from-config.pptx", "content": "c.md"},
        ["build", "explicit.pptx"],
    )
    assert args.template == "explicit.pptx"
    assert args.content == "c.md"


def test_unknown_key_is_named_not_swallowed(tmp_path) -> None:
    """Опечатка в ключе иначе оставит прогон на чужих значениях молча."""
    _, notes = _run(tmp_path, {"template": "t.pptx", "varaints": 3}, ["build"])
    assert any("не знает ключей" in line and "varaints" in line for line in notes)


def test_comment_keys_are_not_mistaken_for_settings(tmp_path) -> None:
    """`_`-ключи — пояснения рядом со значением, как в остальных конфигах."""
    _, notes = _run(
        tmp_path, {"_": "пояснение", "version": "1.0", "template": "t.pptx"}, ["build"]
    )
    assert not any("не знает ключей" in line for line in notes)


def test_version_and_hash_of_the_run_config_are_shown(tmp_path) -> None:
    """Прогон обязан быть опознаваем: чем он задан и каким этот файл был."""
    _, notes = _run(tmp_path, {"version": "2.1", "template": "t.pptx"}, ["build"])
    assert "версия 2.1" in notes[0] and "sha256" in notes[0]


def test_missing_config_file_is_refused_loudly(tmp_path) -> None:
    argv = ["build", "--config", str(tmp_path / "нет-такого.json")]
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit):
        apply_run_config(args, argv)


def test_broken_json_is_refused_with_a_readable_reason(tmp_path) -> None:
    path = tmp_path / "run.json"
    path.write_text('{"template": "C:\\bad\\path.pptx"}', encoding="utf-8")
    argv = ["build", "--config", str(path)]
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        apply_run_config(args, argv)
    assert "не JSON" in str(exc.value)


def test_without_config_nothing_changes(tmp_path) -> None:
    """Прогон без флага обязан вести себя ровно как раньше: девять сдаточных
    колод собираются старыми командами."""
    argv = ["build", "t.pptx", "c.md"]
    args = build_parser().parse_args(argv)
    assert apply_run_config(args, argv) == []
    assert (args.template, args.content, args.variants) == ("t.pptx", "c.md", 1)


def test_committed_example_runs_the_committed_content() -> None:
    """Пример в репозитории должен быть рабочим, а не декоративным: он —
    единственное, что покажет эксперту, как выглядит запуск конфиг-файлом."""
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "config", "run.example.json")
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    assert os.path.exists(os.path.join(root, payload["content"])), payload["content"]

    argv = ["build", "--config", path]
    args = build_parser().parse_args(argv)
    notes = apply_run_config(args, argv)
    assert not any("не знает ключей" in line for line in notes), notes
