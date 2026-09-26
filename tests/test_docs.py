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
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")

#: Ссылка вида [текст](путь). Внешние и якорные не проверяем.
_LINK = re.compile(r"\[[^\]]*\]\((?!https?:|mailto:|#)([^)#]+)(?:#[^)]*)?\)")


#: Документы в корне. Четыре первых названы ТЗ (раздел 4) и их читает эксперт;
#: список перечислен руками, а не собран обходом, чтобы случайный `.md` в корне
#: не попадал под проверки молча. **Добавляя документ в корень, добавь его
#: сюда**: 19 сентября `ARCHITECTURE`, `MODELS` и `AUDIT` были написаны и
#: оказались вне всех проверок — битая ссылка в них не уронила бы ничего.
_ROOT_DOCS = (
    "README.md",
    "ARCHITECTURE.md",
    "MODELS.md",
    "AUDIT.md",
    "CLAUDE.md",
    "THIRD-PARTY.md",
)


def _markdown_files() -> list[str]:
    found = [
        os.path.join(ROOT, name)
        for name in _ROOT_DOCS
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


def _state_queue() -> str:
    """Раздел «Что следующее» в `STATE` — единственная очередь задач."""
    state = _read(os.path.join(DOCS, "STATE.md"))
    assert "## Что следующее" in state, "в STATE нет раздела «Что следующее»"
    return state.split("## Что следующее", 1)[1].split("\n## ", 1)[0]


def test_state_holds_the_only_queue() -> None:
    """Очередь задач одна — в начале `STATE`; `BACKLOG` держит карточки.

    До 26 сентября очередь жила в двух местах — строкой фазы в `BACKLOG` и
    списком в `STATE`, — и этот тест сверял, что обе называют одну следующую
    задачу: 19 сентября они разошлись, `Z-37` против `Z-41`. Согласие копий не
    спасало от их устаревания: в тот же день обе дружно вели на сделанную
    `Z-26`, а 26 сентября статус одной задачи повторялся в десятке мест, и три
    вычитки подряд находили расхождения. Решение пользователя 26 сентября:
    статус — в одном месте. Проверок поэтому две:

    1. первая задача очереди не закрыта в своей карточке — одна устаревшая
       сводка опаснее двух расходящихся: её не с чем сравнить. Отложенную
       первой тест не поймает: место задачи в очереди живёт только здесь, и
       сравнить его не с чем;
    2. каждая открытая карточка `BACKLOG` названа в очереди. Таблицу «Все
       открытые задачи» сверяли глазами, а 21 сентября четыре задачи не
       значились ни в одной фазе.
    """
    queue = _state_queue()
    closed = _closed_tasks()
    backlog = _read(os.path.join(DOCS, "BACKLOG.md"))

    upcoming = queue.split("**Ближайшая работа", 1)
    assert len(upcoming) == 2, "в «Что следующее» нет «Ближайшей работы»"
    first = re.search(r"`(Z-\d+[a-z]?)`", upcoming[1])
    assert first, "«Ближайшая работа» не называет ни одной задачи"
    card = re.search("^### " + re.escape(first.group(1)) + r"\.[^\n]*", backlog, re.M)
    assert card, f"{first.group(1)}: карточки в BACKLOG нет вовсе"
    assert first.group(1) not in closed, (
        f"очередь начинается с {first.group(1)}, а её карточка помечена "
        f"закрытой: «{card.group(0)}»"
    )

    cards = set(re.findall(r"^### (Z-\d+[a-z]?)\.", backlog, re.M))
    missing = sorted(z for z in cards - closed if f"`{z}`" not in queue)
    assert not missing, f"открытые карточки, которых нет в очереди STATE: {missing}"


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


def test_documents_required_by_the_tz_exist() -> None:
    """Четыре документа названы ТЗ поимённо (раздел 4), и эксперт пойдёт
    по этим именам, а не по `docs/INDEX.md`.

    Отсутствующий документ — это ноль по пункту, который проверяют наличием.
    """
    missing = [
        name
        for name in ("README.md", "ARCHITECTURE.md", "MODELS.md", "AUDIT.md")
        if not os.path.exists(os.path.join(ROOT, name))
    ]
    assert not missing, f"требует ТЗ, раздел 4, но нет в корне: {missing}"


def test_audit_table_is_current() -> None:
    """Таблица тестов в `AUDIT.md` совпадает с тем, что порождает скрипт.

    Падение чинится одной командой — `python tools/audit_doc.py --write`, — и
    в тексте падения она названа. Цена невысокая, а без этой проверки документ
    со списком тестов разойдётся с тестами и никто не заметит.
    """
    done = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "audit_doc.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_every_test_file_is_described_in_audit() -> None:
    """Новый файл тестов обязан быть описан, а не просто посчитан.

    Число порождается скриптом и появится само; **смысл** — нет. Поэтому
    отдельно: пустой docstring делает строку таблицы бессодержательной, и это
    должно ронять прогон, а не молча проходить.
    """
    audit = _read(os.path.join(ROOT, "AUDIT.md"))
    tests_dir = os.path.join(ROOT, "tests")
    undescribed = []
    for name in sorted(os.listdir(tests_dir)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        if name not in audit:
            undescribed.append(name)
    assert not undescribed, f"нет в AUDIT.md: {undescribed}"
    assert "**нет docstring**" not in audit, (
        "у файла тестов пустой docstring: в AUDIT.md нечего написать про его "
        "область покрытия. Добавьте первую строку docstring и перегенерируйте."
    )


def test_tests_named_in_audit_exist() -> None:
    """`AUDIT.md` называет отдельные тесты поимённо — например, чем именно
    проверяется побайтовая детерминированность.

    Имя, которое переименовали, документ не заметит: таблица порождается, а
    проза вокруг неё пишется руками. Проверка заведена при логической вычитке
    19 сентября, сразу как имена появились, — а не после того, как разошлись.
    """
    audit = _read(os.path.join(ROOT, "AUDIT.md"))
    named = set(re.findall(r"`(test_[a-z0-9_]+)`", audit))
    defined = set()
    for name in os.listdir(os.path.join(ROOT, "tests")):
        if name.startswith("test_") and name.endswith(".py"):
            defined.add(name)                                  # имя файла
            defined |= set(
                re.findall(r"^def (test_[a-z0-9_]+)", _read(os.path.join(ROOT, "tests", name)), re.M)
            )
    missing = sorted(n for n in named if n not in defined and f"{n}.py" not in defined)
    assert not missing, f"в AUDIT.md названы, но не существуют: {missing}"


def _closed_tasks() -> set[str]:
    """Задачи, чьи карточки в `BACKLOG` помечены закрытыми (сделана, отменена или
    поглощена другой)."""
    backlog = _read(os.path.join(DOCS, "BACKLOG.md"))
    closed = set()
    for head in re.findall(r"^### (Z-\d+[a-z]?)\.[^\n]*", backlog, re.MULTILINE):
        card = re.search("^### " + re.escape(head) + r"\.[^\n]*", backlog, re.MULTILINE).group(0)
        if any(w in card.lower() for w in ("сделано", "сделана", "отменена", "поглощена")):
            closed.add(head)
    return closed


def test_state_does_not_point_at_a_closed_task() -> None:
    """`STATE` называет задачи не только в таблицах очереди, но и в тексте — и
    бывало, цепочкой со стрелками. Тест на очередь её не читает.

    19 сентября это разошлось: список вёл на `Z-43`, а цепочка всё ещё звала
    делать `Z-30` + `Z-33`, закрытые в тот же день. Оба места по отдельности
    выглядели осмысленно, и поймало это только чтение подряд.

    **Стрелка сама по себе не признак:** ею же записаны числа, «9 → 5».
    Цепочкой задач считается стрелка, у которой **с обеих сторон** стоит ссылка
    на задачу.

    Одного этого признака мало: 20 сентября абзац «мерка до `Z-24` … «16 → 1» …
    мерка до `Z-38`» дал ложную тревогу — числовая стрелка попала между двумя
    ссылками. Поэтому стрелка между цифрами не считается вовсе: цепочка задач
    пишется ссылками, а не числами.
    """
    closed = _closed_tasks()
    assert closed, "ни одной закрытой карточки не найдено — проверка бесполезна"

    text = _read(os.path.join(DOCS, "STATE.md"))
    bad = []
    for arrow in re.finditer(r"→", text):
        left = text[max(0, arrow.start() - 40):arrow.start()]
        right = text[arrow.end():arrow.end() + 40]
        if re.search(r"\d\s*$", left) and re.match(r"\s*\d", right):
            continue                                   # число, а не цепочка
        lt, rt = re.findall(r"`(Z-\d+)`", left), re.findall(r"`(Z-\d+)`", right)
        if not (lt and rt):
            continue                                   # не цепочка задач
        struck = set(re.findall(r"~~`?(Z-\d+)`?~~", left + "→" + right))
        for task in (lt[-1], rt[0]):
            if task in closed and task not in struck:
                context = " ".join((left[-30:] + "→" + right[:30]).split())
                bad.append((task, context))
    assert not bad, f"STATE зовёт делать закрытые задачи: {bad}"


def test_every_worklog_is_listed_in_its_index() -> None:
    """`WORKLOG/README.md` обязан перечислять все файлы каталога.

    Найдено сплошной сверкой 20 сентября: в таблице стояло **25** записей при
    **58** файлах. Дрейф молчаливый — индекс не врёт, он просто умалчивает, и
    заметить это можно только счётом.

    Правило проекта: правило, которое приходится помнить, надёжнее заменить
    проверкой, которая падает сама.
    """
    worklog = os.path.join(ROOT, "WORKLOG")
    index = _read(os.path.join(worklog, "README.md"))
    files = sorted(
        name for name in os.listdir(worklog)
        if name.endswith(".md") and name != "README.md"
    )
    missing = [name for name in files if f"({name})" not in index]
    assert not missing, f"нет в WORKLOG/README.md: {missing}"


def test_docs_agree_with_code() -> None:
    """Документы не обещают того, чего в коде нет (`tools/doc_check.py`).

    Заведена 21 сентября, после того как три чтения документации подряд дали
    находки каждое, а последняя была не в тексте, а в расхождении текста с
    кодом: карта модулей в `ARCHITECTURE.md` не знала о двух модулях,
    добавленных в тот же день.

    Проверяются шесть вещей: инструменты из `tools/`, модули в карте
    пайплайна, ссылки `Z`/`OQ`/`ADR`/`PLAN`, конфиги и замо́к, команды и флаги
    движка, числа-константы. Сам инструмент проверен мутацией — каждая из
    шести падает, когда ломаешь ровно её.

    Чего проверка не делает: числа замеров. Их даёт только прогон `report.py`
    с настоящим PowerPoint, и инструмент говорит об этом в выводе.
    """
    done = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "doc_check.py")],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert done.returncode == 0, (
        "документы разошлись с кодом; что именно — "
        f"`python tools/doc_check.py`:\n{done.stdout}{done.stderr}"
    )
