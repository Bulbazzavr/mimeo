"""Таблица тестов для `AUDIT.md`: порождается, а не пишется руками.

Задача `Z-30`, план `PLAN-7.0` Ш8. ТЗ, раздел 4: «AUDIT — список тестов и их
область покрытия».

    python tools/audit_doc.py           напечатать таблицу
    python tools/audit_doc.py --write   вклеить её в AUDIT.md между маркерами
    python tools/audit_doc.py --check   не устарела ли вклеенная (код 1, если да)

Почему порождается. Список тестов, написанный руками, верен в день написания и
врёт на следующем коммите. К 29 сентября между написанием и сдачей пройдёт
десять дней работы — этого хватит, чтобы документ разошёлся с тестами и эксперт
прочёл неправду в документе, который сам же и перечисляет проверки.

Почему не целиком. Список из трёхсот имён не отвечает на вопрос «что покрыто».
Поэтому машина считает **числа и состав**, а смысл остаётся написанным руками —
и живёт в том же файле, снаружи маркеров, куда скрипт не лезет. Ровно то же
правило, по которому `tools/report.py` печатает в вывод и не трогает `WORKLOG/`.

Откуда берётся «что покрывает». Из **первой строки docstring самого файла
тестов**. Не из отдельного списка, который пришлось бы вести параллельно:
описание живёт там же, где тесты, и устаревает вместе с ними, а не отдельно.

Что считается. **Тест-функции**, найденные разбором AST, а не число прогонов
`pytest`. Прогонов больше — параметризация размножает один тест, — но их число
меняется от вещей, к тестам не относящихся: `test_docs.py` параметризован по
файлам документации, и любая новая заметка в `WORKLOG/` сдвинула бы таблицу.
Считать надо то, что меняется вместе с покрытием.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DOC = os.path.join(ROOT, "AUDIT.md")
TESTS = os.path.join(ROOT, "tests")

BEGIN = "<!-- порождается tools/audit_doc.py — руками не править -->"
END = "<!-- /порождается -->"


def _count_and_summary(path: str) -> tuple[int, str]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    tests = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    )
    doc = (ast.get_docstring(tree) or "").strip()
    summary = doc.split("\n")[0].strip() if doc else ""
    return tests, summary


def inventory() -> list[tuple[str, int, str]]:
    """`(имя файла, тест-функций, что покрывает)`, по алфавиту."""
    rows = []
    for name in sorted(os.listdir(TESTS)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        count, summary = _count_and_summary(os.path.join(TESTS, name))
        rows.append((name, count, summary))
    return rows


def table(rows: list[tuple[str, int, str]]) -> str:
    lines = ["| Файл | Тест-функций | Что покрывает |", "|---|---:|---|"]
    for name, count, summary in rows:
        cell = summary.replace("|", "\\|") if summary else "**нет docstring**"
        lines.append(f"| [`tests/{name}`](tests/{name}) | {count} | {cell} |")
    lines.append(f"| **всего** | **{sum(r[1] for r in rows)}** | в {len(rows)} файлах |")
    return "\n".join(lines)


def block() -> str:
    return f"{BEGIN}\n\n{table(inventory())}\n\n{END}"


def splice(text: str, fresh: str) -> str:
    """Подменить только между маркерами. Всё остальное в файле — руками
    написанный разбор, и затирать его нельзя."""
    start = text.find(BEGIN)
    stop = text.find(END)
    if start < 0 or stop < 0:
        raise SystemExit(
            f"В {DOC} нет маркеров {BEGIN} … {END}. Скрипт не станет угадывать, "
            f"куда вставлять таблицу, и не тронет файл."
        )
    return text[:start] + fresh + text[stop + len(END) :]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Таблица тестов для AUDIT.md")
    ap.add_argument("--write", action="store_true", help="вклеить таблицу в AUDIT.md")
    ap.add_argument("--check", action="store_true", help="сверить вклеенную с порождаемой")
    args = ap.parse_args(argv)

    fresh = block()

    if args.write:
        with open(DOC, encoding="utf-8") as fh:
            text = fh.read()
        with open(DOC, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(splice(text, fresh))
        print(f"Таблица вклеена в {DOC}")
        return 0

    if args.check:
        with open(DOC, encoding="utf-8") as fh:
            text = fh.read()
        if fresh in text:
            print("AUDIT.md: таблица тестов свежая.")
            return 0
        print(
            "AUDIT.md: таблица тестов устарела. "
            "Выполните: python tools/audit_doc.py --write",
            file=sys.stderr,
        )
        return 1

    print(fresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
