"""Целостность документации.

Заведено после того, как аудит нашёл документ, обещавший несуществующее:
`WORKLOG` отсылал к «скрипту в журнале», а скрипта не было. Такой дефект хуже
отсутствия документа — на него полагаются.

Проверяется механически проверяемое: ссылки ведут в существующие файлы, каждый
документ найден с карты, каждое решение перечислено, упомянутые команды
запускаемы.
"""

from __future__ import annotations

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")

#: Ссылка вида [текст](путь). Внешние и якорные не проверяем.
_LINK = re.compile(r"\[[^\]]*\]\((?!https?:|mailto:|#)([^)#]+)(?:#[^)]*)?\)")


def _markdown_files() -> list[str]:
    found = [
        os.path.join(ROOT, name)
        for name in ("README.md", "CLAUDE.md", "THIRD-PARTY.md")
        if os.path.exists(os.path.join(ROOT, name))
    ]
    for directory in ("docs", "WORKLOG"):
        for base, _, files in os.walk(os.path.join(ROOT, directory)):
            found += [os.path.join(base, f) for f in files if f.endswith(".md")]
    return sorted(found)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


@pytest.mark.parametrize("path", _markdown_files(), ids=_rel)
def test_every_link_resolves(path: str) -> None:
    """Ссылка в никуда — это документ, который врёт."""
    base = os.path.dirname(path)
    broken = [
        target
        for target in _LINK.findall(_read(path))
        if not os.path.exists(os.path.normpath(os.path.join(base, target)))
    ]
    assert not broken, f"{_rel(path)}: битые ссылки {broken}"


def test_every_document_is_on_the_map() -> None:
    """Документ, которого нет в INDEX, никто не найдёт."""
    index = _read(os.path.join(DOCS, "INDEX.md"))
    missing = []
    for base, _, files in os.walk(DOCS):
        for name in files:
            if not name.endswith(".md"):
                continue
            relative = os.path.relpath(os.path.join(base, name), DOCS).replace(os.sep, "/")
            if relative in ("INDEX.md",) or relative.startswith("journal/"):
                continue  # журнал перечислен как каталог, по датам
            if relative not in index:
                missing.append(relative)
    assert not missing, f"нет в docs/INDEX.md: {missing}"


def test_every_decision_is_listed() -> None:
    adr_dir = os.path.join(DOCS, "architecture", "adr")
    index = _read(os.path.join(DOCS, "INDEX.md"))
    missing = [f for f in sorted(os.listdir(adr_dir)) if f.endswith(".md") and f not in index]
    assert not missing, f"ADR не перечислены в INDEX: {missing}"


def test_referenced_tools_exist() -> None:
    """Если документ обещает «пересоздать скриптом» — скрипт должен быть."""
    pattern = re.compile(r"tools/([A-Za-z0-9_]+\.py)")
    missing = set()
    for path in _markdown_files():
        for name in pattern.findall(_read(path)):
            if not os.path.exists(os.path.join(ROOT, "tools", name)):
                missing.add(name)
    assert not missing, f"упомянуты, но отсутствуют: {sorted(missing)}"


def test_demo_content_is_committed() -> None:
    """Без своего демо-контента проект не воспроизводится с нуля: шаблоны чужие
    и не коммитятся, а показать работу на чём-то надо."""
    assert os.path.exists(os.path.join(ROOT, "examples", "content-demo.md"))
    rules = [
        line.strip()
        for line in _read(os.path.join(ROOT, ".gitignore")).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any(rule.startswith("examples") for rule in rules), rules


def test_tz_derived_cache_can_never_be_committed() -> None:
    """Кэш ответов модели по данным ТЗ не должен попадать в репозиторий.

    Обещано `PLAN-2.6` (логическая проверка 1, находка 1): ответ модели о
    выданном шаблоне — производная конфиденциальных данных (п. 7.3.5 Положения),
    и один общий кэш означал бы тихую утечку через коммит.

    Правило проверяется действием, а не доверием: `tz/*` исключён целиком, и
    любой путь под ним — тоже.
    """
    rules = [
        line.strip()
        for line in _read(os.path.join(ROOT, ".gitignore")).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert "tz/*" in rules, (
        "в .gitignore нет строки `tz/*` — кэш по данным ТЗ может утечь в коммит"
    )
    # Исключение ровно одно и оно наше собственное: заметка о том, что это за каталог.
    negations = [r for r in rules if r.startswith("!tz/")]
    assert negations == ["!tz/README.md"], negations


def test_state_names_the_next_stage() -> None:
    """`STATE` — точка входа новой сессии; он обязан отвечать «что дальше»."""
    state = _read(os.path.join(DOCS, "STATE.md"))
    assert "## Что следующее" in state
    assert "VERIFY" in state


# --- ссылочная целостность внутри документации ---------------------------
#
# Три проверки ниже заведены после ручного аудита, который нашёл: пропуск
# раздела в `DOM-TEXT` (нумерация шла §7 → §9) и ссылку на несуществующий
# `DOM-TEXT §8`. Разовым скриптом такое ловится один раз, тестом — всегда.


def _all_sources() -> list[str]:
    sources = _markdown_files()
    for directory in ("mimeo", "tools", "tests"):
        for base, _, files in os.walk(os.path.join(ROOT, directory)):
            if "__pycache__" in base:
                continue
            sources += [os.path.join(base, f) for f in files if f.endswith(".py")]
    return sources


def _domain_sections() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for name in os.listdir(os.path.join(DOCS, "domain")):
        if not name.endswith(".md"):
            continue
        text = _read(os.path.join(DOCS, "domain", name))
        doc_id = re.search(r"^id:\s*(\S+)", text, re.M).group(1)
        out[doc_id] = set(re.findall(r"^##\s*§(\d+)", text, re.M))
    return out


def test_domain_sections_are_numbered_without_gaps() -> None:
    """Пропуск в нумерации заставляет читателя искать удалённый раздел."""
    gaps = {}
    for doc_id, numbers in _domain_sections().items():
        present = set(map(int, numbers))
        missing = sorted(set(range(1, max(present) + 1)) - present)
        if missing:
            gaps[doc_id] = missing
    assert not gaps, f"пропущенные разделы: {gaps}"


def test_section_references_resolve() -> None:
    sections = _domain_sections()
    broken = [
        (_rel(path), doc, num)
        for path in _all_sources()
        for doc, num in re.findall(r"\b(DOM-[A-Z]+)\s*§(\d+)", _read(path))
        if num not in sections.get(doc, set())
    ]
    assert not broken, f"ссылки на несуществующие разделы: {broken}"


def test_decision_and_question_references_resolve() -> None:
    adrs = {
        re.match(r"ADR-\d+", f).group(0)
        for f in os.listdir(os.path.join(DOCS, "architecture", "adr"))
        if f.startswith("ADR-")
    }
    questions = set(
        re.findall(r"^\|\s*(OQ-\d+)", _read(os.path.join(DOCS, "OPEN-QUESTIONS.md")), re.M)
    )
    broken = []
    for path in _all_sources():
        text = _read(path)
        broken += [(_rel(path), a) for a in set(re.findall(r"\bADR-\d{4}\b", text)) if a not in adrs]
        broken += [(_rel(path), q) for q in set(re.findall(r"\bOQ-\d{2}\b", text)) if q not in questions]
    assert not broken, f"ссылки в никуда: {broken}"


def test_engine_has_no_third_party_imports() -> None:
    """«Обязательных зависимостей нет» — утверждение, которое легко разрушить
    одним импортом. `jsonschema` в `cli.py` допущен сознательно: он внутри
    функции и только для флага --validate."""
    import ast

    allowed = {("mimeo/cli.py", "jsonschema")}
    stdlib = set(__import__("sys").stdlib_module_names)
    outside = set()
    for base, _, files in os.walk(os.path.join(ROOT, "mimeo")):
        if "__pycache__" in base:
            continue
        for name in (f for f in files if f.endswith(".py")):
            path = os.path.join(base, name)
            for node in ast.walk(ast.parse(_read(path))):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    modules = [node.module or ""]
                for module in modules:
                    root = module.split(".")[0]
                    if root and root not in stdlib and root != "mimeo":
                        entry = (_rel(path), root)
                        if entry not in allowed:
                            outside.add(entry)
    assert not outside, f"сторонние импорты в движке: {sorted(outside)}"


def test_state_and_backlog_agree_on_the_next_task() -> None:
    """Точки входа обязаны называть ОДНУ следующую задачу.

    19 сентября они разошлись: таблица фаз в `BACKLOG` вела на `Z-37`, а список
    в `STATE` — на `Z-41`. Новая сессия следует инструкции «начни с таблицы фаз»
    и взяла бы не ту задачу, пропустив единственный дефект, который портит
    сдаточный артефакт.

    Это второй случай расхождения сводки с карточкой за три дня, и первый
    нашёлся только чтением. Поэтому проверка автоматическая: сводка устаревает
    раньше всего остального, а замечают её последней.
    """
    backlog = _read(os.path.join(DOCS, "BACKLOG.md"))
    state = _read(os.path.join(DOCS, "STATE.md"))

    rows = [ln for ln in backlog.splitlines() if ln.startswith("| 16–29 сентября")]
    assert len(rows) == 1, "строка активной фазы в таблице фаз не одна"
    queue = re.findall(r"`(Z-\d+)`", rows[0].split("**Первым идёт")[0])
    done = set(re.findall(r"~~`(Z-\d+)`", rows[0]))
    backlog_next = next(z for z in queue if z not in done)

    block = state.split("**Ближайшая работа")[1].split("`Z-29` — единственная")[0]
    state_next = None
    for item in re.split(r"\n(?=\d+\. )", block):
        head = item.strip().split(chr(10))[0] if item.strip() else ""
        if not head or not head[0].isdigit():
            continue
        if "~~" in head:                       # пункт целиком про сделанное
            continue
        found = re.findall(r"`(Z-\d+)`", item)
        if found:
            state_next = found[0]
            break

    assert backlog_next == state_next, (
        f"точки входа расходятся: BACKLOG ведёт на {backlog_next}, "
        f"STATE на {state_next}. Новая сессия возьмёт не ту задачу."
    )


def test_backlog_is_not_frozen_to_the_day_it_was_written() -> None:
    """Бэклог читают в неизвестный день.

    Первая редакция содержала «09.09 — сегодня» и заголовок «Сейчас, до
    15 сентября». Сессия, стартующая позже, либо приняла бы дату написания за
    сегодняшнюю, либо не знала бы, как пересобрать порядок.
    """
    text = _read(os.path.join(DOCS, "BACKLOG.md"))
    assert "узнай сегодняшнюю дату" in text, "бэклог не велит свериться с датой"
    assert re.search(r"^\|\s*Если сегодня\s*\|", text, re.M), "нет таблицы выбора фазы"
    frozen = re.findall(r"^##+\s*(Сейчас|Сегодня)\b.*$", text, re.M)
    assert not frozen, f"заголовки, привязанные к дню написания: {frozen}"


def test_backlog_marks_tasks_the_agent_must_not_do_alone() -> None:
    """Часть задач исполняет пользователь. Молча «сделать» их — ошибка."""
    text = _read(os.path.join(DOCS, "BACKLOG.md"))
    assert "делает пользователь" in text
    assert "нужно согласие пользователя" in text


def test_entry_points_send_the_reader_to_the_backlog() -> None:
    """Новая сессия не должна искать список задач по каталогам."""
    for path in (
        os.path.join(ROOT, "CLAUDE.md"),
        os.path.join(DOCS, "STATE.md"),
        os.path.join(DOCS, "INDEX.md"),
    ):
        assert "BACKLOG" in _read(path), f"{_rel(path)} не отсылает к бэклогу"


def test_run_instructions_name_the_working_directory() -> None:
    """Стартовый каталог сессии на уровень выше корня репозитория.

    Без явного указания первая же команда из документации падает с
    `No module named mimeo`.
    """
    state = _read(os.path.join(DOCS, "STATE.md"))
    assert "cd mimeo" in state or "из каталога `mimeo/`" in state


def test_completed_plans_are_marked_completed_on_the_map() -> None:
    """План со `status: done` не должен выглядеть на карте как незаконченный:
    карту читают первой, и сессия может продолжить уже сделанное."""
    import re as _re

    index = _read(os.path.join(DOCS, "INDEX.md"))
    for name in sorted(os.listdir(os.path.join(DOCS, "plans"))):
        if not name.endswith(".md"):
            continue
        status = _re.search(r"^status:\s*(\S+)", _read(os.path.join(DOCS, "plans", name)), _re.M)
        if status and status.group(1) == "done":
            row = next((ln for ln in index.splitlines() if name in ln), "")
            assert "Выполнено" in row, f"{name}: в INDEX не помечен выполненным"
