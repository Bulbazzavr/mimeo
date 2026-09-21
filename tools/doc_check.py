"""Сверка документов с кодом: обещает ли документация то, чего нет.

    python tools/doc_check.py           показать находки
    python tools/doc_check.py --quiet   только итог, код возврата 1 при находках

**Зачем отдельный инструмент.** 21 сентября три чтения документации подряд дали
находки каждое, и последняя была не в тексте, а в **расхождении текста с
кодом**: карта модулей в `ARCHITECTURE.md` не знала о двух модулях, добавленных
в тот же день. Глазами такое ловится случайно, а список длинный.

**Что проверяется — шесть вещей, и каждая отвечает на свой вопрос.** Все шесть
выбраны по тому, на чём мы уже обжигались:

1. **Инструменты.** Каждый `tools/*.py`, названный в документах, существует.
   Правило `CLAUDE.md`: «Документ не должен обещать несуществующего».
2. **Модули.** Каждый модуль `mimeo/` назван в карте `ARCHITECTURE.md`, кроме
   инфраструктуры из `_ARCH_SKIP`. Именно эта проверка нашла `analyze/picture.py`,
   `plan/imagesize.py` и `analyze/typeface.py`.
3. **Ссылки.** `Z-NN` имеет карточку в `BACKLOG`, `OQ-NN` — запись в
   `OPEN-QUESTIONS`, `ADR-NNNN` и `PLAN-N.N` — файл. Здесь 21 сентября нашлась
   повисшая `Z-51`: на неё ссылались из `README` за тем, чего в карточке не было.
4. **Конфиги.** Каждый файл `config/*.json` (кроме замка) объявлен в
   `mimeo/config.py::KNOWN` и записан в `config/versions.lock.json`.
5. **Команды и флаги.** Каждый `python -m mimeo <команда>` и каждый `--флаг` из
   документов есть в `mimeo/cli.py`.
6. **Числа-константы.** Величины, названные в документах поимённо, совпадают со
   значением в коде. Это самая дорогая проверка: 21 сентября правка `Z-28a`
   поменяла содержимое слотов, и числа в четырёх документах разошлись с
   замером — искали руками.

**Чего проверка НЕ делает, и это надо знать.** Она не сверяет числа замеров —
переполнения, заслонения, время: их даёт только прогон `report.py` с настоящим
PowerPoint. Отличать «проверено и чисто» от «проверить не смог» обязан любой
измеритель (`CLAUDE.md`), поэтому итог прямо называет, что осталось за кадром.
"""

from __future__ import annotations

import glob
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Модули, которых в карте пайплайна нет намеренно: это инфраструктура, а
#: `ARCHITECTURE.md` описывает стадии, а не дерево файлов.
_ARCH_SKIP = {
    "__main__.py", "cli.py", "config.py", "model.py",
    "oxml/ns.py", "oxml/units.py", "compose/package.py", "analyze/color.py",
}

#: Константы, названные в документах числом. Ключ — как пишем в тексте,
#: значение — где искать в коде. Список ведётся руками: автоматически «какое
#: число в документе относится к какой константе» не выводится.
_CONSTANTS = [
    ("_OVERFLOW_HARD", "mimeo/plan/matching.py"),
    ("_PENALTY_EMPTY_SLOT", "mimeo/plan/matching.py"),
    ("_PENALTY_DROPPED_IMAGE", "mimeo/plan/matching.py"),
    ("_PENALTY_DISORDER", "mimeo/plan/matching.py"),
    ("_PENALTY_TYPEFACE", "mimeo/plan/matching.py"),
    ("_REPEAT_PENALTY", "mimeo/plan/matching.py"),
    ("_ROW_BAND", "mimeo/analyze/patterns.py"),
]


def _norm(path: str) -> str:
    """Прямые слэши. На Windows `glob` отдаёт обратные, и сравнение путей с
    текстом документа молча не срабатывает — обожглись 12 и 21 сентября."""
    return path.replace(os.sep, "/")


def _read(path: str) -> str:
    with io.open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


def _docs() -> dict[str, str]:
    paths = (
        glob.glob(os.path.join(ROOT, "*.md"))
        + glob.glob(os.path.join(ROOT, "docs", "**", "*.md"), recursive=True)
    )
    out = {}
    for path in paths:
        rel = _norm(os.path.relpath(path, ROOT))
        out[rel] = _read(rel)
    return out


def check_tools(blob: str) -> list[str]:
    named = set(re.findall(r"tools/([A-Za-z0-9_]+\.py)", blob))
    have = {os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "tools", "*.py"))}
    return [f"инструмент обещан, но его нет: tools/{n}" for n in sorted(named - have)]


def check_modules() -> list[str]:
    arch = _read("ARCHITECTURE.md")
    found = []
    for path in glob.glob(os.path.join(ROOT, "mimeo", "**", "*.py"), recursive=True):
        rel = _norm(os.path.relpath(path, os.path.join(ROOT, "mimeo")))
        if rel.endswith("__init__.py") or rel in _ARCH_SKIP:
            continue
        if rel not in arch:
            found.append(f"модуль есть в коде, но не назван в ARCHITECTURE.md: mimeo/{rel}")
    return found


def check_references(docs: dict[str, str], blob: str) -> list[str]:
    out = []
    cards = set(re.findall(r"^### (Z-\d+[a-z]?)\.", docs["docs/BACKLOG.md"], re.M))
    for zid in sorted(set(re.findall(r"`(Z-\d+[a-z]?)`", blob)) - cards):
        out.append(f"ссылка на {zid}, а карточки в BACKLOG нет")
    questions = set(re.findall(r"OQ-\d+", docs["docs/OPEN-QUESTIONS.md"]))
    for oq in sorted(set(re.findall(r"`(OQ-\d+)`", blob)) - questions):
        out.append(f"ссылка на {oq}, а записи в OPEN-QUESTIONS нет")
    adr = {
        re.match(r"ADR-\d+", os.path.basename(p)).group(0)
        for p in glob.glob(os.path.join(ROOT, "docs", "architecture", "adr", "ADR-*.md"))
    }
    for ref in sorted(set(re.findall(r"`(ADR-\d{4})`", blob)) - adr):
        out.append(f"ссылка на {ref}, а файла решения нет")
    plans = set()
    for path in glob.glob(os.path.join(ROOT, "docs", "plans", "*.md")):
        found = re.search(r"^id:\s*(PLAN-[\d.]+)", _read(_norm(os.path.relpath(path, ROOT))), re.M)
        if found:
            plans.add(found.group(1))
    for ref in sorted(set(re.findall(r"`(PLAN-[\d.]+)`", blob)) - plans):
        out.append(f"ссылка на {ref}, а файла плана нет")
    return out


def check_configs() -> list[str]:
    known = set(re.findall(r'"([a-z_]+\.json)"', _read("mimeo/config.py")))
    lock = _read("config/versions.lock.json")
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, "config", "*.json"))):
        name = os.path.basename(path)
        if name in ("versions.lock.json", "run.example.json"):
            continue
        if name not in known:
            out.append(f"конфиг есть, но не объявлен в mimeo/config.py::KNOWN: config/{name}")
        if f'"{name}"' not in lock:
            out.append(f"конфиг есть, но его нет в замке versions.lock.json: config/{name}")
    return out


def check_cli(docs: dict[str, str]) -> list[str]:
    """Команды и флаги движка — только те, что поданы как команды движка.

    Первая версия брала **все** `--флаги` из документов и выдала четырнадцать
    находок, из которых настоящей не было ни одной: `--all` и `--pretty` — от
    `git`, `--host` и `--mmproj` — от `llama-server`. Проверка, кричащая на
    шуме, хуже отсутствующей: её перестают читать. Поэтому флаг берётся только
    со строки, где стоит сам вызов движка.

    Команды тоже искались неверно — по `add_parser("имя"` в одну строку, а в
    `cli.py` два вызова из трёх многострочные. Отсюда «команды build нет»,
    когда она есть.
    """
    cli = _read("mimeo/cli.py")
    known = set(re.findall(r'add_parser\(\s*"(\w+)"', cli, re.S))
    out = []
    for doc, text in docs.items():
        for line in text.splitlines():
            if "python -m mimeo" not in line:
                continue
            name = re.search(r"python -m mimeo\s+([a-z]+)", line)
            if name and name.group(1) not in known:
                out.append(f"{doc}: команда `python -m mimeo {name.group(1)}`, а в cli.py её нет")
            for flag in re.findall(r"(?<![\w-])(--[a-z][a-z-]{2,})", line):
                if f'"{flag}"' not in cli:
                    out.append(f"{doc}: флаг `{flag}` у команды движка, а в cli.py его нет")
    return out


def check_constants(blob: str) -> list[str]:
    out = []
    for name, where in _CONSTANTS:
        source = _read(where)
        found = re.search(rf"^{re.escape(name)}\s*=\s*([\d.]+)", source, re.M)
        if not found:
            out.append(f"константа {name} названа в проверке, а в {where} не найдена")
            continue
        value = found.group(1)
        for doc, text in _docs().items():
            for line in text.splitlines():
                if f"`{name}`" not in line:
                    continue
                numbers = re.findall(r"(?<![\w.])(\d+\.\d+)(?![\w.])", line)
                if numbers and value not in numbers:
                    out.append(
                        f"{doc}: рядом с `{name}` стоит {numbers}, а в коде {value}"
                    )
    return out


def main() -> int:
    quiet = "--quiet" in sys.argv
    docs = _docs()
    blob = "\n".join(docs.values())
    groups = [
        ("инструменты", check_tools(blob)),
        ("модули в ARCHITECTURE", check_modules()),
        ("ссылки Z / OQ / ADR / PLAN", check_references(docs, blob)),
        ("конфиги и замок", check_configs()),
        ("команды и флаги", check_cli(docs)),
        ("числа-константы", check_constants(blob)),
    ]
    problems = [(title, items) for title, items in groups if items]
    if not quiet:
        print(f"Сверено документов: {len(docs)}\n")
        for title, items in groups:
            mark = "—" if not items else f"**{len(items)}**"
            print(f"  {title}: {mark}")
        print()
        for title, items in problems:
            print(f"## {title}")
            for item in items:
                print(f"  - {item}")
            print()
    total = sum(len(items) for _t, items in problems)
    print(
        f"Итог: находок {total}. "
        "Числа замеров — переполнения, заслонения, время — эта проверка НЕ "
        "сверяет: их даёт только прогон `report.py` с настоящим PowerPoint."
    )
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
