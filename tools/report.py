"""Порождение таблиц замеров для WORKLOG.

Таблицы в `WORKLOG/*.md` не пишутся руками — они выводятся прогоном по корпусу
образцов. Этот скрипт их и печатает.

    python tools/report.py analyze     разбор шаблонов
    python tools/report.py plan        раскладка контента по шаблонам
    python tools/report.py compose     сборка готовых файлов и три проверки
    python tools/report.py volume      объём колоды: без цели и с целью (`--slides`)
    python tools/report.py prose       что даёт вход: форма, ёмкость слотов, колода
                                       (`--content путь`, по умолчанию прозаический пример;
                                       `--raw` — без сегментации, как было до `Z-25`)

Печатает в стандартный вывод намеренно: файлы в `WORKLOG/` содержат ещё и
разбор наблюдений, написанный руками, и перезаписывать их целиком нельзя.
Новые числа надо посмотреть и осознанно вклеить, а не затереть анализ.

Образцы в `samples/` не коммитятся (они чужие). Скачать заново:

    python tools/fetch_samples.py samples
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mimeo.analyze import analyze_template  # noqa: E402
from mimeo.compose import build, inspect  # noqa: E402
from mimeo.oxml.units import emu_to_inch as inch  # noqa: E402
from mimeo.plan import load_content, load_markdown, plan_deck  # noqa: E402

CONTENT = "examples/content-demo.md"


#: Каталог с шаблонами. Меняется флагом `--templates`: те же таблицы нужны и по
#: нашему корпусу-прокси (`samples/`), и по выданным материалам (`tz/templates/`),
#: и смешивать их в одной таблице нельзя — у них разный статус (`tz/README.md`).
TEMPLATES = "samples"


def samples() -> list[str]:
    found = sorted(glob.glob(os.path.join(TEMPLATES, "*.pptx")))
    if not found:
        sys.exit(
            f"В {TEMPLATES}/ нет ни одного .pptx. Для samples/ скачать: "
            "python tools/fetch_samples.py samples"
        )
    return found


def report_analyze() -> None:
    print("| Файл | Холст, дюймы | Сл. | Мак. | Цветов | Ядро | Типо-ролей | Форм | "
          "Фигур сод./поз. | Паттернов | Слотов | Сек. |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    rows = []
    for f in samples():
        started = time.perf_counter()
        a = analyze_template(f)
        elapsed = time.perf_counter() - started
        d, p, e = a.design_system, a.patterns, a.design_system.evidence
        rows.append((os.path.basename(f), d, p))
        print(
            f"| `{os.path.basename(f)}` | {inch(d.slide.cx_emu):.1f}×{inch(d.slide.cy_emu):.1f} "
            f"| {d.source.slides} | {d.source.layouts} | {len(d.observed_palette)} "
            f"| {len(d.core_palette)} | {len(d.type_scale)} | {len(d.shapes)} "
            f"| {e.shapes_used}/{e.shapes_positioned} | {len(p.patterns)} "
            f"| {sum(len(x.slots) for x in p.patterns)} | {elapsed:.2f} |"
        )

    print("\n### Поля, выведенные по текстовым блокам\n")
    print("| Файл | Л | В | П | Н | Колонок |")
    print("|---|---|---|---|---|---|")
    for name, d, _ in rows:
        g = d.grid
        print(f"| `{name}` | {inch(g.margin_left_emu):.2f} | {inch(g.margin_top_emu):.2f} "
              f"| {inch(g.margin_right_emu):.2f} | {inch(g.margin_bottom_emu):.2f} "
              f"| {g.columns} |")

    print("\n### Размеры холста\n")
    print("| EMU | Дюймы | Файлов |")
    print("|---|---|---|")
    sizes = Counter((d.slide.cx_emu, d.slide.cy_emu) for _, d, _ in rows)
    for (cx, cy), n in sorted(sizes.items(), key=lambda kv: -kv[1]):
        print(f"| {cx} × {cy} | {inch(cx):.2f} × {inch(cy):.2f} | {n} |")

    print("\n### Кластеризация\n")
    print("| Файл | Слайдов | Паттернов | Слитых групп | Размеры групп |")
    print("|---|---|---|---|---|")
    for name, d, p in rows:
        merged = [len(x.members) for x in p.patterns if len(x.members) > 1]
        print(f"| `{name}` | {d.source.slides} | {len(p.patterns)} | {len(merged)} "
              f"| {merged or '—'} |")


def report_plan() -> None:
    doc = load_content(CONTENT)
    print("| Шаблон | Паттернов | Слайдов | Не размещено | Сверх ёмкости | Предупр. "
          "| Виды раскладок |")
    print("|---|---|---|---|---|---|---|")
    clean = 0
    total = 0
    for f in samples():
        total += 1
        a = analyze_template(f)
        p = plan_deck(doc, a.patterns, a.design_system.source.sha256)
        kinds = {x.id: x.kind for x in a.patterns.patterns}
        sequence = " → ".join(kinds.get(s.pattern_id, "?") for s in p.slides)
        over = sum(1 for s in p.slides for fill in s.fills if fill.over_capacity)
        clean += not p.unplaced
        print(f"| `{os.path.basename(f)}` | {len(a.patterns.patterns)} | {len(p.slides)} "
              f"| {len(p.unplaced)} | {over} | {len(p.warnings)} | {sequence} |")
    print(f"\n**Итог: {clean} шаблонов из {total} разложены без потерь.**")


def report_compose() -> None:
    doc = load_content(CONTENT)
    os.makedirs("out/decks", exist_ok=True)
    print("| Шаблон | Слайдов | Подставлено слотов | Унаследовано фигур "
          "| Структурных проблем | Круговая | python-pptx | КБ | Сек |")
    print("|---|---|---|---|---|---|---|---|---|")
    bad = 0
    total = 0
    for f in samples():
        total += 1
        name = os.path.basename(f)
        target = f"out/decks/{name}"
        started = time.perf_counter()
        a = analyze_template(f)
        plan = plan_deck(doc, a.patterns, a.design_system.source.sha256)
        report = build(f, plan, a.patterns, target)
        problems = len(inspect(target))

        try:
            circular = f"{analyze_template(target).design_system.source.slides} сл."
        except Exception as exc:  # noqa: BLE001 — это и есть проверка
            circular = f"ПАДЕНИЕ {type(exc).__name__}"
        try:
            from pptx import Presentation

            third = f"{len(Presentation(target).slides)} сл."
        except ImportError:
            third = "нет python-pptx"
        except Exception as exc:  # noqa: BLE001
            third = f"ПАДЕНИЕ {type(exc).__name__}"

        if problems or "ПАДЕНИЕ" in circular or "ПАДЕНИЕ" in third:
            bad += 1
        print(f"| `{name}` | {report.slides} | {report.substituted} "
              f"| {report.inherited_shapes} | {problems} | {circular} | {third} "
              f"| {os.path.getsize(target) // 1024} | {time.perf_counter() - started:.2f} |")
    print(f"\n**Итог: {total - bad} шаблонов из {total} собраны и прошли все три проверки.**")


def _quartiles(values: list[int]) -> tuple[int, int, int]:
    """Медиана и квартили. Пустой список — не ноль, а отсутствие ответа."""
    if not values:
        raise ValueError("нечего мерить")
    v = sorted(values)
    return v[len(v) // 4], v[len(v) // 2], v[3 * len(v) // 4]


def report_prose(content: str, raw: bool = False) -> None:
    """Замеры по `Z-25`: что вход даёт на корпусе и во что упирается.

    Отдельно от `plan`, потому что меряет не шаблон, а **вход**: одна и та же
    таблица гоняется по прозе и по размеченному контенту, и сравниваются они
    между собой.

    `raw=True` обходит сегментацию и показывает, что было до `Z-25`. Нужен, чтобы
    таблица «до» в `WORKLOG/2026-09-14-prose-input.md` осталась воспроизводимой:
    иначе документ обещал бы числа, которых уже ничем не получить.
    """
    doc = load_markdown(content) if raw else load_content(content)
    mode = "без сегментации, как до `Z-25`" if raw else "с сегментацией"
    print(f"### Форма входа: `{os.path.basename(content)}` — {mode}\n")
    print(f"Разделов {len(doc.sections)}, блоков {doc.block_count}, "
          f"заголовок колоды {'есть' if doc.title else 'нет'}.\n")
    print("| Раздел | Заголовок | Блок | Знаков | Единиц |")
    print("|---|---|---|---|---|")
    for s in doc.sections:
        for b in s.blocks:
            head = (s.heading or "—")[:36]
            print(f"| {s.id} | {head} | {b.kind} | {b.length} | {b.units} |")

    print("\n### Ёмкость слотов корпуса по ролям\n")
    print("| Роль | Слотов | max_chars: кв1 | медиана | кв3 |")
    print("|---|---|---|---|---|")
    by_role: dict[str, list[int]] = {}
    for f in samples():
        for pattern in analyze_template(f).patterns.patterns:
            for slot in pattern.slots:
                if slot.capacity and slot.capacity.max_chars:
                    by_role.setdefault(slot.role, []).append(slot.capacity.max_chars)
    for role, caps in sorted(by_role.items(), key=lambda kv: -len(kv[1])):
        q1, med, q3 = _quartiles(caps)
        print(f"| {role} | {len(caps)} | {q1} | {med} | {q3} |")

    print("\n### Колода из этого входа по шаблонам\n")
    print("| Шаблон | Слайдов | Не размещено | Заливок | Сверх ёмкости | Медиана знаков в заливке |")
    print("|---|---|---|---|---|---|")
    empty = 0
    total = 0
    for f in samples():
        total += 1
        name = os.path.basename(f)
        try:
            a = analyze_template(f)
            p = plan_deck(doc, a.patterns, a.design_system.source.sha256)
        except Exception as exc:  # noqa: BLE001 — падение обязано быть видно в таблице
            print(f"| `{name}` | ПАДЕНИЕ {type(exc).__name__} | — | — | — | — |")
            empty += 1
            continue
        fills = [fill for s in p.slides for fill in s.fills]
        over = sum(1 for fill in fills if fill.over_capacity)
        lengths = [len(fill.text) for fill in fills if getattr(fill, "text", None)]
        median = _quartiles(lengths)[1] if lengths else "—"
        empty += not p.slides
        print(f"| `{name}` | {len(p.slides)} | {len(p.unplaced)} | {len(fills)} "
              f"| {over} | {median} |")
    print(f"\n**Итог: пустых колод {empty} из {total}.**")


def report_volume(content: str, slides: str | None) -> None:
    """Замеры по `Z-35`: что делает цель по объёму. Сравнивает «без цели» и
    «с целью» на одном и том же входе и шаблоне."""
    from mimeo.cli import parse_slides

    target = parse_slides(slides or "10-15")
    doc_free = load_content(content)
    doc_aim = load_content(content, target=target)
    print(f"### Вход `{os.path.basename(content)}`, цель {target[0]}-{target[1]}")
    print()
    print(f"Разделов: без цели {len(doc_free.sections)}, с целью {len(doc_aim.sections)}")
    print()
    print("| Шаблон | Раскладок | Слайдов без цели | Слайдов с целью | Сверх ёмкости без/с |")
    print("|---|---|---|---|---|")
    for f in samples():
        a = analyze_template(f)
        sha = a.design_system.source.sha256
        free = plan_deck(doc_free, a.patterns, sha)
        aim = plan_deck(doc_aim, a.patterns, sha, target=target)

        def over(p):
            return sum(1 for s in p.slides for fill in s.fills if fill.over_capacity)

        print(f"| `{os.path.basename(f)[:44]}` | {len(a.patterns.patterns)} "
              f"| {len(free.slides)} | {len(aim.slides)} | {over(free)}/{over(aim)} |")


#: Отложенная часть корпуса (`ADR-0019`). Настраиваемся на своих одиннадцати
#: шаблонах, проверяемся на трёх выданных — их выбирали не мы, и это самое
#: близкое к финальной ситуации, что у нас есть. Список зафиксирован здесь, а не
#: живёт в голове: иначе его незаметно подвинут в удобную сторону.
HOLDOUT = (
    "2_Датасет VK Tech шаблон.pptx",
    "2_Датасет VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
    "2_Датасет Шаблон презентации VK Education.pptx",
)


def report_quality() -> None:
    """Весы колоды, дешёвый ярус: базовая линия по корпусу (`PLAN-2.5`).

    Столбцы — ступени сравнения по старшинству: потери, поломки, разнообразие,
    «на донышке». Меньше лучше везде, кроме разнообразия.
    """
    from mimeo.plan.quality import score_deck

    inputs = {"demo": load_content(CONTENT),
              "prose": load_content("examples/content-prose.md")}
    print("| Шаблон | Часть | Вход | Слайдов | Заливок | Потеряно | Сломано | "
          "Раскладок | На донышке |")
    print("|---|---|---|---|---|---|---|---|---|")
    totals = {}
    for f in samples():
        name = os.path.basename(f)
        part = "отложенная" if name in HOLDOUT else "настроечная"
        a = analyze_template(f)
        sha = a.design_system.source.sha256
        for label, doc in inputs.items():
            plan = plan_deck(doc, a.patterns, sha)
            sc = score_deck(plan, a.patterns)
            acc = totals.setdefault((part, label), [0, 0, 0, 0, 0])
            acc[0] += sc.slides; acc[1] += sc.fills; acc[2] += sc.lost
            acc[3] += sc.broken; acc[4] += sc.thin
            print(f"| `{name[:42]}` | {part} | {label} | {sc.slides} | {sc.fills} "
                  f"| {sc.lost} | {sc.broken} | {sc.layouts} | {sc.thin} |")
    print()
    print("| Часть | Вход | Слайдов | Заливок | Потеряно | Сломано | На донышке |")
    print("|---|---|---|---|---|---|---|")
    for (part, label), a in sorted(totals.items()):
        print(f"| {part} | {label} | {a[0]} | {a[1]} | {a[2]} | {a[3]} | {a[4]} |")


def main() -> int:
    global TEMPLATES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("analyze", "plan", "compose", "prose", "volume", "quality"))
    parser.add_argument("--content", default="examples/content-prose.md",
                        help="вход для stage=prose (по умолчанию прозаический пример)")
    parser.add_argument("--slides", help="цель для stage=volume (по умолчанию 10-15)")
    parser.add_argument("--raw", action="store_true",
                        help="stage=prose: без сегментации прозы, состояние до Z-25")
    parser.add_argument("--templates", default=TEMPLATES,
                        help="каталог с шаблонами (по умолчанию samples)")
    args = parser.parse_args()
    TEMPLATES = args.templates
    if args.stage == "prose":
        report_prose(args.content, args.raw)
        return 0
    if args.stage == "volume":
        report_volume(args.content, args.slides)
        return 0
    if args.stage == "quality":
        report_quality()
        return 0
    {"analyze": report_analyze, "plan": report_plan, "compose": report_compose}[args.stage]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
