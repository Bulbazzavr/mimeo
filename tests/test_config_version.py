"""Замок версий конфигов: он обязан уметь падать.

Задача `Z-33`, решение `ADR-0022`, план `PLAN-7.0` Ш3.

Здесь две разные проверки, и вторая не заменяет первую.

Первая зовёт **сам инструмент подпроцессом** — ровно так, как его запустит
человек. Она отвечает на вопрос «замок в репозитории сейчас сходится».

Вторая проверяет, что сверка **различает случаи**: забытую версию, поднятую
версию с необновлённым замком, отсутствие поля, лишнюю запись. Без неё первая
была бы зелёной и у сверки, которая всегда возвращает «сошлось», — а это ровно
та ошибка, о которой `CLAUDE.md`: проверка, которая не может провалиться,
ничего не стоит.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "config_version.py")


def _tool():
    spec = importlib.util.spec_from_file_location("config_version_tool", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _stamp(name: str, version: str, sha: str):
    from mimeo.config import ConfigStamp

    return ConfigStamp(name=name, version=version, sha256=sha, loaded=True, source=name)


def test_repository_lock_is_current() -> None:
    """Замок в репозитории сходится с конфигами. Падение значит одно из двух:
    конфиг поправили и не подняли версию, либо подняли и не записали замок."""
    done = subprocess.run(
        [sys.executable, TOOL, "--check"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_every_known_config_is_in_the_lock() -> None:
    """Конфиг, которого нет в замке, версионируется только на словах."""
    from mimeo.config import KNOWN

    lock = _tool().read_lock()
    assert set(lock) == set(KNOWN), f"замок: {sorted(lock)}, движок читает: {sorted(KNOWN)}"


def test_forgotten_version_is_caught() -> None:
    """Главный случай: содержимое другое, номер прежний."""
    tool = _tool()
    lock = {"prose.json": {"version": "1.0", "sha256": "a" * 64}}
    problems = tool.compare(lock, (_stamp("prose.json", "1.0", "b" * 64),))
    assert len(problems) == 1 and "version остался 1.0" in problems[0]
    assert tool.forgotten(lock, (_stamp("prose.json", "1.0", "b" * 64),)) == ["prose.json"]


def test_raised_version_asks_for_a_lock_refresh_not_for_a_bump() -> None:
    """Версию подняли — это не забытая версия, а несвежий замок. Разные
    сообщения, потому что чинятся они по-разному."""
    tool = _tool()
    lock = {"prose.json": {"version": "1.0", "sha256": "a" * 64}}
    stamps = (_stamp("prose.json", "1.1", "b" * 64),)
    problems = tool.compare(lock, stamps)
    assert len(problems) == 1 and "--write" in problems[0]
    assert tool.forgotten(lock, stamps) == []


def test_missing_version_field_is_a_problem() -> None:
    tool = _tool()
    problems = tool.compare({}, (_stamp("prose.json", "", "b" * 64),))
    assert len(problems) == 1 and "version" in problems[0]


def test_stale_entry_in_the_lock_is_a_problem() -> None:
    """Конфиг убрали из движка, а в замке он остался: замок описывает не то,
    что работает."""
    tool = _tool()
    lock = {"gone.json": {"version": "1.0", "sha256": "a" * 64}}
    problems = tool.compare(lock, ())
    assert len(problems) == 1 and "gone.json" in problems[0]


def test_agreement_is_silent() -> None:
    tool = _tool()
    lock = {"prose.json": {"version": "1.0", "sha256": "a" * 64}}
    assert tool.compare(lock, (_stamp("prose.json", "1.0", "a" * 64),)) == []


def test_write_produces_a_lock_that_check_accepts(tmp_path) -> None:
    """Запись замка и его сверка — один круг.

    Проверка заведена задним числом: `--write` ломался от опечатки в импорте, а
    все остальные тесты оставались зелёными, потому что звали только `--check`.
    Ветка кода, которую ни один тест не исполняет, ломается молча.
    """
    tool = _tool()
    path = str(tmp_path / "versions.lock.json")
    stamps = (_stamp("prose.json", "1.0", "a" * 64), _stamp("model.json", "2.0", "b" * 64))

    tool.write_lock(stamps, path)
    lock = tool.read_lock(path)

    assert set(lock) == {"prose.json", "model.json"}
    assert lock["model.json"] == {"version": "2.0", "sha256": "b" * 64}
    assert tool.compare(lock, stamps) == []
