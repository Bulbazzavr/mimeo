"""Граница веб-слоя (`Z-29`, `PLAN-8.0`, `SPEC-WEB` раздел 2).

**Веб видит движок только через командную строку.** Правило записано в
спецификации, но записанное правило держится до первого неудобства — поэтому
оно здесь проверяется, а не обещается.

Зачем граница вообще: продукт целиком на стандартной библиотеке, обязательных
зависимостей ноль (`ADR-0001`, `ADR-0011`). Веб-слой не имеет права затащить
зависимости в движок. Побочная польза, названная в `SPEC-WEB`: так веб заодно
проверяет, что продукт пригоден для встраивания — если интерфейса неудобно
построить, плох CLI.
"""

from __future__ import annotations

import ast
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")


def _python_files() -> list[str]:
    found = []
    for base, _dirs, names in os.walk(WEB):
        found += [os.path.join(base, n) for n in names if n.endswith(".py")]
    return sorted(found)


def test_the_web_layer_exists() -> None:
    assert os.path.isfile(os.path.join(WEB, "serve.py")), "сервер не на месте"
    assert _python_files(), "в web/ нет ни одного модуля — тест ниже был бы пустым"


@pytest.mark.parametrize("path", _python_files())
def test_web_never_imports_the_engine(path: str) -> None:
    """`import mimeo` в веб-слое запрещён.

    Проверяется разбором дерева, а не поиском подстроки: `grep` по `mimeo`
    находит слово в комментариях и в именах путей, а тест, который кричит на
    комментарии, перестают читать — этим уже была плоха первая версия
    `doc_check` (`CLAUDE.md`).
    """
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if _is_engine(a.name)]
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` даёт module=None: это относительный импорт
            # внутри самого веб-слоя, и он разрешён.
            if node.module and _is_engine(node.module):
                offenders.append(node.module)

    assert not offenders, (
        f"{os.path.relpath(path, ROOT)} импортирует движок: {offenders}. "
        "Веб обязан звать его командной строкой (SPEC-WEB, раздел 2)"
    )


def _is_engine(name: str) -> bool:
    return name == "mimeo" or name.startswith("mimeo.")


def test_web_brings_no_dependencies() -> None:
    """Локальный интерфейс живёт на стандартной библиотеке.

    `SPEC-WEB` разрешает веб-слою свои зависимости, и для будущего публичного
    стенда это остаётся в силе. Решение 22 сентября (`PLAN-8.0`) — здесь их не
    заводить: этот интерфейс запускает эксперт из сданного репозитория, и
    каждая зависимость — шаг, на котором чужой сетап ломается.

    Если решение однажды пересмотрят, этот тест — то место, где надо будет
    сказать об этом вслух, а не тихо добавить `pip install`.
    """
    assert not os.path.exists(os.path.join(WEB, "requirements.txt"))
    assert not os.path.exists(os.path.join(WEB, "package.json"))

    stdlib_only = set()
    for path in _python_files():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                stdlib_only |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                stdlib_only.add(node.module.split(".")[0])

    import sys
    outside = sorted(m for m in stdlib_only if m not in sys.stdlib_module_names)
    assert not outside, f"веб тянет не из стандартной библиотеки: {outside}"


def test_the_page_is_self_contained() -> None:
    """Ни одной внешней загрузки: ни шрифтов, ни иконок, ни CDN.

    Эксперт может запустить это без сети, и на демонстрации сеть подводит
    ровно тогда, когда она нужнее всего.
    """
    static = os.path.join(WEB, "static")
    for name in sorted(os.listdir(static)):
        with open(os.path.join(static, name), encoding="utf-8") as fh:
            body = fh.read()
        for mark in ("http://", "https://", "//cdn", "//fonts"):
            # `127.0.0.1` в тексте страницы — это адрес самого сервера, а не
            # внешняя загрузка.
            assert mark not in body.replace("http://127.0.0.1", ""), (
                f"{name}: внешняя ссылка «{mark}» — страница перестанет работать без сети"
            )


def test_the_server_listens_on_loopback_only() -> None:
    """Локальный значит локальный: ни аутентификации, ни защиты здесь нет."""
    with open(os.path.join(WEB, "serve.py"), encoding="utf-8") as fh:
        body = fh.read()
    assert '"127.0.0.1"' in body
    assert '"0.0.0.0"' not in body, "слушать все интерфейсы этому серверу нельзя"
