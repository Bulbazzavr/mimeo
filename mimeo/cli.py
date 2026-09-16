"""Командная строка. argparse из стандартной библиотеки — ADR-0001."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zipfile
from xml.etree import ElementTree as ET

from .analyze import Analysis, analyze_template
from .model import DeckPlan, DesignSystem, PatternLibrary
from .compose import build as compose_deck
from .compose import inspect as inspect_package
from .plan import load_content, plan_deck
from .opc.package import PackageError
from .oxml.units import emu_to_inch

NL = chr(10)

#: Потолок раундов ремонта для `--verify`. Дублируется из `verify.loop`
#: намеренно: разбор аргументов не должен тянуть стадию, которой может не быть
#: чем воспользоваться. Совпадение стережёт тест.
VERIFY_ROUNDS = 3

_CONTRACTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contracts"
)
_ARTIFACTS = (
    ("design-system.json", "design-system.schema.json"),
    ("patterns.json", "pattern-library.schema.json"),
)
_PLAN_ARTIFACT = ("deck-plan.json", "deck-plan.schema.json")


def _summary(ds: DesignSystem, elapsed: float) -> str:
    lines = [
        f"шаблон      {ds.source.filename}",
        f"холст       {ds.slide.aspect}  "
        f"{emu_to_inch(ds.slide.cx_emu):.2f}x{emu_to_inch(ds.slide.cy_emu):.2f} in",
        f"структура   {ds.source.masters} мастер(ов), {ds.source.layouts} макет(ов), "
        f"{ds.source.slides} слайд(ов)",
        f"палитра     {len(ds.theme_palette)} ролей темы, "
        f"{len(ds.observed_palette)} цветов на слайдах",
    ]
    top = ", ".join(f"{c.hex}×{c.count}" for c in ds.observed_palette[:5])
    if top:
        lines.append(f"            {top}")

    lines.append(f"типографика {len(ds.type_scale)} ролей")
    for role in ds.type_scale[:6]:
        face = role.latin or "?"
        flags = "".join(("B" if role.bold else "", "I" if role.italic else ""))
        lines.append(f"            {role.role:<9} {role.size_pt:>6.1f} pt  {face} {flags}")

    g = ds.grid
    lines.append(
        f"сетка       поля {emu_to_inch(g.margin_left_emu):.2f}/"
        f"{emu_to_inch(g.margin_top_emu):.2f}/"
        f"{emu_to_inch(g.margin_right_emu):.2f}/"
        f"{emu_to_inch(g.margin_bottom_emu):.2f} in, "
        f"колонок {g.columns if g.columns is not None else '—'}, "
        f"по {g.samples} фигурам"
    )
    lines.append(f"формы       {len(ds.shapes)} типов")

    if ds.evidence.unhandled:
        lines.append("не разобрано")
        for u in ds.evidence.unhandled[:5]:
            lines.append(f"            {u.kind} {u.detail or ''} ×{u.count}")
    for note in ds.evidence.notes:
        lines.append(f"внимание    {note}")

    lines.append(f"время       {elapsed:.2f} с")
    return "\n".join(lines)


def _patterns_summary(lib: PatternLibrary) -> str:
    """Сводка для питча: какие раскладки нашлись и из каких слайдов."""
    lines = [f"паттерны    {len(lib.patterns)} раскладок"]
    for p in lib.patterns[:12]:
        fillable = sum(1 for s in p.slots if s.required)
        where = (
            f"слайды {','.join(str(m) for m in p.members)}"
            if p.members else "из макета"
        )
        cohesion = f", схожесть {p.cohesion:.2f}" if p.cohesion is not None else ""
        lines.append(
            f"            {p.id} {p.kind:<11} {fillable:>2} слот.  {where}{cohesion}"
        )
    if len(lib.patterns) > 12:
        lines.append(f"            … ещё {len(lib.patterns) - 12}")
    for note in lib.notes:
        lines.append(f"внимание    {note}")
    return NL.join(lines)


#: Целевой объём колоды из ТЗ (раздел 2, «Рамки решения»). По умолчанию НЕ
#: включён: замер показал, что цель 10–15 меняет 12 колод из 14 на размеченном
#: контенте (`WORKLOG/2026-09-15-deck-volume.md`), а молча менять давно
#: работающий результат нельзя. Кто хочет рамки ТЗ — просит их явно.
TZ_SLIDES = "10-15"


def parse_slides(value: str | None) -> tuple[int, int] | None:
    """`12` или `10-15` в пару чисел. Ошибку называем словами, а не трассировкой."""
    if not value:
        return None
    parts = value.replace("–", "-").split("-")
    try:
        numbers = [int(x.strip()) for x in parts if x.strip()]
    except ValueError:
        numbers = []
    if len(numbers) == 1:
        low = high = numbers[0]
    elif len(numbers) == 2:
        low, high = numbers
    else:
        raise SystemExit(f"--slides: ожидается N или N-M, получено «{value}»")
    if low < 1 or high < low:
        raise SystemExit(f"--slides: диапазон должен быть от 1 и по возрастанию, получено «{value}»")
    return low, high


def _validate(payload: dict, schema_name: str) -> list[str]:
    try:
        import jsonschema  # необязательная зависимость, только для --validate
    except ImportError:
        return ["jsonschema не установлен, проверка пропущена"]
    with open(os.path.join(_CONTRACTS_DIR, schema_name), encoding="utf-8") as fh:
        schema = json.load(fh)
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in e.path) or '<корень>'}: {e.message}"
        for e in validator.iter_errors(payload)
    ]


def cmd_analyze(args):
    started = time.perf_counter()
    analysis = analyze_template(args.template)
    elapsed = time.perf_counter() - started

    payloads = {
        "design-system.json": analysis.design_system.to_json(),
        "patterns.json": analysis.patterns.to_json(),
    }

    if args.validate:
        failed = False
        for filename, schema_name in _ARTIFACTS:
            errors = _validate(payloads[filename], schema_name)
            if errors:
                failed = True
                print(f"{filename} не соответствует схеме:", file=sys.stderr)
                for err in errors:
                    print(f"  {err}", file=sys.stderr)
        if failed:
            return 2

    os.makedirs(args.out, exist_ok=True)
    written = []
    for filename, _ in _ARTIFACTS:
        target = os.path.join(args.out, filename)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(payloads[filename], fh, ensure_ascii=False, indent=2)
            fh.write(NL)
        written.append(target)

    if not args.quiet:
        print(_summary(analysis.design_system, elapsed))
        print(_patterns_summary(analysis.patterns))
        print("записано    " + ", ".join(written))
    return 0


def _plan_summary(plan: DeckPlan, library: PatternLibrary, elapsed: float) -> str:
    """Сводка для питча: что из текста во что превратилось и почему."""
    kinds = {p.id: p.kind for p in library.patterns}
    lines = [
        f"контент     {plan.source.content}",
        f"планировщик {plan.planner}",
        f"слайдов     {len(plan.slides)}",
    ]
    for slide in plan.slides:
        head = next(
            (f.text for f in slide.fills if f.kind in ("text", "number") and f.text), ""
        )
        mark = "!" if any(f.over_capacity for f in slide.fills) else " "
        lines.append(
            f"  {slide.index:>2}{mark} {slide.pattern_id} {kinds.get(slide.pattern_id, '?'):<11} "
            f"{head[:38]}"
        )
        lines.append(f"       {slide.reason}")
    if plan.unplaced:
        lines.append(f"не размещено {', '.join(plan.unplaced)}")
    for w in plan.warnings:
        lines.append(f"внимание    {w}")
    lines.append(f"время       {elapsed:.2f} с")
    return NL.join(lines)


def cmd_plan(args):
    started = time.perf_counter()
    analysis = analyze_template(args.template)
    target = parse_slides(getattr(args, "slides", None))
    doc = load_content(args.content, target=target)
    plan = plan_deck(
        doc, analysis.patterns, analysis.design_system.source.sha256, target=target
    )
    elapsed = time.perf_counter() - started

    payload = plan.to_json()
    if args.validate:
        errors = _validate(payload, _PLAN_ARTIFACT[1])
        if errors:
            print("deck-plan.json не соответствует схеме:", file=sys.stderr)
            for err in errors:
                print(f"  {err}", file=sys.stderr)
            return 2

    os.makedirs(args.out, exist_ok=True)
    target = os.path.join(args.out, _PLAN_ARTIFACT[0])
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write(NL)

    if not args.quiet:
        print(_plan_summary(plan, analysis.patterns, elapsed))
        print(f"записано    {target}")
    return 0


def cmd_build(args):
    """Шаблон плюс контент — готовый файл. Без обращения к модели (ADR-0009)."""
    started = time.perf_counter()
    analysis = analyze_template(args.template)
    target = parse_slides(getattr(args, "slides", None))
    doc = load_content(args.content, target=target)
    plan = plan_deck(
        doc, analysis.patterns, analysis.design_system.source.sha256, target=target
    )

    os.makedirs(args.out, exist_ok=True)
    target = args.output or os.path.join(args.out, "deck.pptx")
    # `--output` может указывать мимо `--out`: каталог под файл тоже наш.
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    report = compose_deck(args.template, plan, analysis.patterns, target)
    elapsed = time.perf_counter() - started

    problems = inspect_package(target)

    # Стадия VERIFY: выключена по умолчанию. Она поднимает настоящий PowerPoint
    # и стоит 5-24 с на колоду против долей секунды у обычной сборки
    # (`WORKLOG/2026-09-13-verify-loop.md`), поэтому за неё платят по просьбе.
    verify = None
    if getattr(args, "verify", False):
        from .verify import verify_deck                       # noqa: PLC0415
        from .verify.report import describe, write_json       # noqa: PLC0415

        outcome = verify_deck(
            args.template, plan, analysis.patterns, target, report,
            rounds=args.rounds,
        )
        verify = outcome.report
        plan, report = outcome.plan, outcome.build
        elapsed = time.perf_counter() - started
        problems = inspect_package(target)      # файл пересобран — проверить заново
        write_json(verify, os.path.join(args.out, "render-report.json"))

    if not args.quiet:
        kinds = {p.id: p.kind for p in analysis.patterns.patterns}
        print(f"шаблон      {os.path.basename(args.template)}")
        print(f"контент     {args.content}")
        print(f"слайдов     {report.slides}")
        for slide in plan.slides:
            head = next((f.text for f in slide.fills if f.kind in ("text", "number") and f.text), "")
            print(f"  {slide.index:>2}  {slide.pattern_id} "
                  f"{kinds.get(slide.pattern_id, '?'):<11} {head[:40]}")
        print(f"подставлено {report.substituted} слотов")
        print(f"унаследовано {report.inherited_shapes} фигур донора — их мы даже не разбирали")
        if report.cleared_slots:
            print(f"очищено    {report.cleared_slots} пустых слотов от текста донора")
        for w in report.warnings:
            print(f"внимание    {w}")
        print(f"проверка    структурных проблем: {len(problems)}")
        for x in problems[:5]:
            print(f"            {x}")
        if verify is not None:
            for line in describe(verify):
                print(line)
            print(f"            отчёт: {os.path.join(args.out, 'render-report.json')}")
        print(f"время       {elapsed:.2f} с")
        print(f"записано    {target}")

    # Неустранённая вёрстка кодом возврата не считается: отказ ужимать текст
    # ниже предела читаемости — штатный исход, а не ошибка (`PLAN-4.0`,
    # первая логическая проверка шагов 6-9).
    return 2 if problems else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mimeo", description="Перенос стиля презентаций: разбор шаблона и сборка колоды."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="разобрать шаблон и извлечь дизайн-систему")
    analyze.add_argument("template", help="путь к .pptx или .potx")
    analyze.add_argument("-o", "--out", default="out", help="каталог для артефактов")
    analyze.add_argument("--validate", action="store_true", help="проверить артефакт по схеме")
    analyze.add_argument("-q", "--quiet", action="store_true", help="без сводки")
    analyze.set_defaults(func=cmd_analyze)

    plan = sub.add_parser(
        "plan", help="разложить контент по раскладкам шаблона (без обращения к модели)"
    )
    plan.add_argument("template", help="путь к .pptx или .potx")
    plan.add_argument("content", help="путь к markdown или текстовому файлу с контентом")
    plan.add_argument("-o", "--out", default="out", help="каталог для артефактов")
    plan.add_argument("--validate", action="store_true", help="проверить артефакт по схеме")
    plan.add_argument(
        "--slides",
        metavar="N|N-M",
        help=f"целевое число слайдов; рамки ТЗ — {TZ_SLIDES}. "
             "По умолчанию объём определяется контентом",
    )
    plan.add_argument("-q", "--quiet", action="store_true", help="без сводки")
    plan.set_defaults(func=cmd_plan)

    build = sub.add_parser(
        "build", help="шаблон плюс контент -> готовый .pptx, без обращения к модели"
    )
    build.add_argument("template", help="путь к .pptx или .potx")
    build.add_argument("content", help="путь к markdown или текстовому файлу с контентом")
    build.add_argument("-o", "--out", default="out", help="каталог для артефактов")
    build.add_argument("--output", help="путь к итоговому файлу (по умолчанию out/deck.pptx)")
    build.add_argument(
        "--slides",
        metavar="N|N-M",
        help=f"целевое число слайдов; рамки ТЗ — {TZ_SLIDES}. "
             "По умолчанию объём определяется контентом",
    )
    build.add_argument("-q", "--quiet", action="store_true", help="без сводки")
    build.add_argument(
        "--verify",
        action="store_true",
        help="проверить вёрстку в PowerPoint и ужать непоместившееся "
             "(нужен Windows с Office; медленнее в десятки раз)",
    )
    build.add_argument(
        "--rounds",
        type=int,
        default=VERIFY_ROUNDS,
        help=f"сколько раундов ремонта при --verify (по умолчанию {VERIFY_ROUNDS})",
    )
    build.set_defaults(func=cmd_build)

    return parser


def _force_utf8_output() -> None:
    """Консоль Windows по умолчанию в cp1251 и роняет вывод на кириллице и «×»."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"Файл не найден: {exc.filename}", file=sys.stderr)
        return 1
    except (PackageError, zipfile.BadZipFile) as exc:
        print(f"Не удалось разобрать пакет: {exc}", file=sys.stderr)
        return 1
    except ET.ParseError as exc:
        print(f"Битый XML внутри пакета: {exc}", file=sys.stderr)
        return 1
