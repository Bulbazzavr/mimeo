"""Командная строка. argparse из стандартной библиотеки — ADR-0001."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zipfile
from xml.etree import ElementTree as ET

from . import config as cfg
from .analyze import Analysis, analyze_template
from .model import DeckPlan, DesignSystem, PatternLibrary
from .compose import build as compose_deck
from .compose import inspect as inspect_package
from .plan import load_content, outline, plan_deck
from .plan.client import Access, Outage
from .plan.client import load_config as load_model_config
from .plan.outline import MODES
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

#: Доступ к модели и режим текста (`PLAN-9.0`, Ш2; `ADR-0021`, `ADR-0023`).
#: Значения берутся из самих модулей, чтобы флаг и код не разошлись.
LLM_CHOICES = tuple(a.value for a in Access)
TEXT_CHOICES = MODES


def model_path(args, doc, target, config=None, outage=None):
    """Путь модели для этой сборки (`PLAN-9.0`, Ш3; `ADR-0023`) и его запись.

    Один вызов на сборку — и на `--variants N` тоже: запрос от политики
    варианта не зависит, и три вёрстки берут одно содержание. Возвращает
    исход и путь к `<out>/outline.json`; документ исхода идёт в вёрстку —
    колода модели или документ пути без модели с заметкой почему.

    `config` — настройки модели, если не из `config/model.json`: так
    `tools/report.py` мерит колоды тем же вызовом, что собирает сборка, с
    ответами из своего каталога (`PLAN-9.0`, Ш9), а не своей копией вызова.
    `outage` — отказ сервера модели, общий на сборку (`client.Outage`).
    """
    revise, revise_prompt = (), ""
    picks = getattr(args, "fix_picks", None) or ()
    if picks:
        from .audit import fix, vision

        revise, revise_prompt = fix.revise_lines(list(picks)), vision.load_config().revise
    outcome = outline.run(
        args.content, doc,
        access=getattr(args, "llm", None), text_mode=getattr(args, "text", None),
        target=target, config=config or load_model_config(), outage=outage,
        revise=revise, revise_prompt=revise_prompt, purpose=getattr(args, "purpose", None),
    )
    return outcome, outline.write_record(args.out, outcome.record)


def _visuals(doc, analysis):
    """Таблицы и диаграммы колоды (`Z-32`, `plan/visual.py`): наименьшее место
    под них — доля от слайда (`config/images.json`, `visual_min_side`); если
    места нет ни в одной раскладке шаблона, данные становятся списком, и
    предупреждение плана это называет."""
    from dataclasses import replace as _replace
    from .analyze.picture import load_config as load_picture_config
    from .plan import visual
    from .plan.matching import visual_host

    if not any(b.kind in ("table", "chart") and b.data is not None
               for s in doc.sections for b in s.blocks):
        return doc
    slide = analysis.design_system.slide
    doc = visual.with_floor(doc, visual.floor((slide.cx_emu, slide.cy_emu),
                                              load_picture_config().visual_min_side))
    patterns = analysis.patterns.patterns
    doc, notes = visual.without_hosts(
        doc, lambda block: any(visual_host(p, block) is not None for p in patterns))
    return _replace(doc, notes=tuple(doc.notes) + tuple(notes)) if notes else doc


def _pictures(args, doc, analysis, outage=None):
    """Картинки по идеям модели (`Z-28`): заготовки в документ до плана и тот,
    кто их нарисует после. Возвращает (документ, художник, заметка)."""
    from dataclasses import replace as _replace
    from .plan import images

    gen = images.load_config()
    wanted = getattr(args, "images", None)
    if wanted is not None:
        gen = _replace(gen, access=wanted, access_source="задано при запуске")
    slide = analysis.design_system.slide
    size = (slide.cx_emu, slide.cy_emu)
    model = load_model_config()
    access = getattr(args, "llm", None)
    if access is not None:
        model = _replace(model, access=Access(access))

    translations: dict[str, str] = {}       # сцена по-английски → по-русски, для человека
    deck = doc                              # вся колода — контекст каждой сцены

    scene_state: dict = {}                  # стиль колоды — один на все запросы сцен

    def rewrite(ideas, sections=(), rejected=None):
        # Уже написанные сцены колоды — ключи переводов: новые их не повторят.
        # `rejected` — сцена, которую забраковало зрение, и причина.
        return images.scenes(ideas, gen, model, inputs=(args.content,), outage=outage,
                             translations=translations, deck=deck, sections=sections,
                             taken=tuple(translations), state=scene_state, rejected=rejected)

    doc, placeholders, note = images.add_placeholders(doc, args.out, gen, slide_size=size)
    painter = images.Painter(gen, placeholders, llm_base_url=model.endpoint.base_url, slide_size=size,
                             rewrite=rewrite, folder=os.path.join(args.out, images.FOLDER),
                             translations=translations,
                             check=images.picture_check(gen, model, inputs=(args.content,), outage=outage)
                             if placeholders or gen.access == "on" else None)
    return doc, painter, note


def _painted(plan, painter, doc, library, replan):
    """План, у которого заготовки стали готовыми файлами в пропорции своего
    места. Картинка не нарисовалась — план перестраивается без неё: иначе в
    месте под иллюстрацию осталась бы картинка донора (`Z-62`). Пустые рамки
    под фото шаблона после этого заливает генератор (`Z-55`). Сцены всех
    картинок — и заготовок, и рамок — модель пишет до рисования одним
    запросом по всей колоде (`Painter.prepare`)."""
    from .plan import images

    dropped: set[str] = set()
    painter.prepare([plan], library, doc)
    plan, failed = painter.paint(plan, library)
    while failed:                       # каждый круг снимает хотя бы одну заготовку
        dropped |= failed
        plan, failed = painter.paint(replan(images.without(doc, dropped)), library)
    return painter.frames(plan, library, doc)


def _judge(args, analysis, outage=None):
    """Зрение модели для картинок донора (`Z-62`, `plan/donor.py`): тот же
    доступ к модели, что у колоды, — флаг `--llm` старше конфига, — и тот же
    отказ сервера: лёг на колоде — картинки не стучатся заново."""
    from dataclasses import replace as _replace
    from .plan import donor

    model = load_model_config()
    access = getattr(args, "llm", None)
    if access is not None:
        model = _replace(model, access=Access(access))
    slide = analysis.design_system.slide
    return donor.Judge(donor.load_config(), model, args.template,
                       slide_size=(slide.cx_emu, slide.cy_emu), outage=outage)


def _icons(args, outage=None):
    """Пиктограммы по смыслу пункта на месте значков шаблона (`Z-32`,
    `plan/icons.py`): тот же доступ к модели и тот же отказ сервера, что у
    колоды."""
    from dataclasses import replace as _replace
    from .plan import icons

    model = load_model_config()
    access = getattr(args, "llm", None)
    if access is not None:
        model = _replace(model, access=Access(access))
    return icons.Picker(icons.load_config(), model, args.template, outage=outage)


def _model_line(outcome, record_path) -> str:
    """Строка сводки: каким путём собран текст колоды и почему — ИКР `PLAN-9.0`
    («без модели сборка идёт прежним путём и говорит об этом словами»)."""
    return f"модель      {outcome.line}; подробно — {_slash(record_path)}"


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


#: Целевой объём колоды из ТЗ (раздел 2, «Рамки решения»). Колоде модели он
#: рамки промпта, если `--slides` не задан (`outline.TZ_FRAMES`). Пути без
#: модели целиком по умолчанию НЕ включён: добор до 10 менял 12 колод из 14 на
#: размеченном контенте (`WORKLOG/2026-09-15-deck-volume.md`, `Z-35`).
TZ_SLIDES = "10-15"
#: Объём пути без модели, когда `--slides` не задан (`Z-70`): **потолок** ТЗ —
#: не больше 15 слайдов, — без добора до 10. Замер `Z-35` был про добор; без
#: потолка текст из 12 абзацев давал 19 слайдов на VK Tech, а ТЗ велит 10–15.
DEFAULT_VOLUME = (1, int(TZ_SLIDES.split("-")[1]))


def volume_for(doc, target):
    """Объём для вёрстки: заданный — как задан; не задан — колоде модели
    ничего (её рамки — в промпте, слайды она строит сама), пути без модели —
    потолок ТЗ (`DEFAULT_VOLUME`)."""
    if target:
        return target
    return None if getattr(doc, "planner", None) == "mixed" else DEFAULT_VOLUME


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
    # Запасной документ — путь без модели: без `--slides` — потолок ТЗ (`Z-70`).
    doc = load_content(args.content, target=target or DEFAULT_VOLUME)
    outcome, record_path = model_path(args, doc, target)
    plan = plan_deck(
        _visuals(outcome.doc, analysis), analysis.patterns, analysis.design_system.source.sha256,
        target=volume_for(outcome.doc, target),
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
        print(_model_line(outcome, record_path))
        print(f"записано    {target}")
    return 0


#: Форма машинночитаемого итога сборки (`Z-46`). **Объявлена в `SPEC-WEB`,
#: разделе 4, ещё до реализации** — как обещание второму участнику, чтобы он мог
#: писать против контракта, не дожидаясь кода. Исполнитель с тех пор сменился,
#: обещание нет: форма не меняется, схема лежит в
#: `contracts/build-report.schema.json` и проверяется тестом.
#:
#: **Зачем вообще:** `build` печатает сводку русской прозой, и разбирать её
#: нельзя — формулировки меняются, разбор ломается на первой же правке
#: (`SPEC-WEB`, раздел 4, прямым текстом).
_REPORT_VERSION = "1.0"


def _slash(path):
    """Путь в отчёте — всегда через `/`, даже на Windows.

    Отчёт читает машина, и `os.path.join` на Windows даёт
    `out/run\render-report.json` — разделители в одной строке разные. Прямой
    слэш понимают обе системы, и потребителю не приходится гадать.
    """
    return path.replace("\\", "/") if isinstance(path, str) else path


def _deck_entry(path, slides, variant, problems, render_report, audit=None, pictures=()):
    """Одна колода в отчёте.

    `render_report` — путь или **`None`**, и `None` значит «стадия VERIFY не
    запускалась», а не «дефектов нет». Это главное правило проекта, и здесь оно
    держится типом: пустой строкой такое не выразить, а нулём тем более. Так же
    `audit` — путь к отчёту аудита (`Z-34`) или `None`: аудит не запускался.
    `pictures` — картинки генератора на слайдах колоды с их промптами.
    """
    return {
        "path": _slash(path),
        "slides": slides,
        "variant": variant,
        "structural_problems": len(problems),
        "render_report": _slash(render_report),
        "audit": _slash(audit),
        "pictures": list(pictures),
    }


def _placed(plan, built, painter) -> list[dict]:
    """Картинки генератора в собранной колоде: слайд и промпт (`images.placed`)."""
    if painter is None:
        return []
    from .plan import images

    return images.placed(plan, built.slides_written, painter.prompts, painter.ru)


def _fix_picks(args):
    """Выбранные находки аудита для `build --fix` (`Z-34`, `audit/fix.py`):
    пусто — исправлять нечего. Файла нет или он не читается — это ошибка
    запуска, а не «исправлять нечего»: молча собрать прежнюю колоду значило бы
    сказать «исправлено», не исправив."""
    spec = getattr(args, "fix", None)
    if not spec:
        return []
    from .audit import fix

    try:
        return fix.load(spec, args.out)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"--fix: не прочитать выбор находок {spec}: {exc}")


def _shrunk(plan, library, picks, variant):
    """План с ужатыми по выбору слайдами (`layout`) и строка об этом."""
    from .audit import fix

    titles = fix.layout_titles(picks, variant)
    plan, hit = fix.shrink(plan, library, titles)
    if not titles:
        return plan, None
    return plan, (f"Исправление по аудиту: ужато слайдов {hit} из {len(titles)} выбранных"
                  + ("" if hit == len(titles) else " — прочие не нашлись по заголовку в новой колоде")
                  + " (Z-34).")


def _audited(args, path, plan, library, built, verdict_path, variant, outage):
    """Аудит собранной колоды (`Z-34`): отчёт рядом с ней и строки сводки."""
    from dataclasses import replace as _replace

    from . import audit

    model = load_model_config()
    access = getattr(args, "llm", None)
    if access is not None:
        model = _replace(model, access=Access(access))
    verify = None
    if verdict_path and os.path.isfile(verdict_path):
        with open(verdict_path, encoding="utf-8") as fh:
            verify = json.load(fh)
    name = f"audit-{variant}" if variant else "audit"
    target = parse_slides(getattr(args, "slides", None))
    report = audit.audit_deck(path, plan, library, built.slides_written, verify, model,
                              (args.template,), os.path.join(args.out, name), variant=variant,
                              outage=outage, max_slides=target[1] if target else 15)
    target = audit.write_json(report, os.path.join(args.out, name + ".json"))
    return target, audit.describe(report)


def write_build_report(target, decks, seconds, plan_or_plans, extra_warnings=()):
    """Кладёт итог сборки в файл. `target` пуст — не делает ничего.

    `plan_or_plans` — план колоды или список планов: предупреждения и
    неразмещённые разделы собираются со всех вариантов, потому что у каждого
    они свои.

    `extra_warnings` — то, что движок говорит **не о плане, а о прогоне**, и
    чего в планах поэтому нет. Сегодня это одно: **отобрано вариантов меньше,
    чем просили**. Причина печатается человеку с 19 сентября (`Z-26`), но в
    отчёт не попадала, и потребитель отчёта — веб — показывал бы пять колод
    вместо девяти **молча**. Это ровно «молчаливый ноль» из `Z-20`: формально
    правдиво и вводит в заблуждение. Найдено замером 22 сентября, когда у веба
    появилось поле «сколько вариантов».
    """
    if not target:
        return
    plans = plan_or_plans if isinstance(plan_or_plans, (list, tuple)) else [plan_or_plans]
    warnings, unplaced = [w for w in extra_warnings if w], []
    for plan in plans:
        for w in getattr(plan, "warnings", ()) or ():
            if w not in warnings:
                warnings.append(w)
        for u in getattr(plan, "unplaced", ()) or ():
            if u not in unplaced:
                unplaced.append(u)
    payload = {
        "version": _REPORT_VERSION,
        "decks": list(decks),
        "seconds": round(seconds, 3),
        "diagnostics": {"unplaced": unplaced, "warnings": warnings},
    }
    directory = os.path.dirname(os.path.abspath(target))
    os.makedirs(directory, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write(chr(10))


def cmd_build(args):
    """Шаблон плюс контент — готовый файл. Колоду из сплошного текста строит
    модель, если она доступна и её ответ прошёл проверки (`ADR-0023`); иначе —
    путь без модели (`ADR-0009`). Вёрстка — код в обоих случаях."""
    started = time.perf_counter()
    # Выбор находок аудита читается первым: нечитаемый файл — отказ до сборки.
    args.fix_picks = _fix_picks(args)
    if args.fix_picks:
        args.audit = True           # исправили — проверить заново
    analysis = analyze_template(args.template)
    target = parse_slides(getattr(args, "slides", None))
    # Запасной документ — путь без модели: без `--slides` — потолок ТЗ (`Z-70`).
    doc = load_content(args.content, target=target or DEFAULT_VOLUME)
    # Отказ сервера модели — один на сборку: колода, сцены и зрение о
    # картинках донора его разделяют (`client.Outage`).
    outage = Outage()
    # До ветки вариантов: один вызов модели на все варианты (`ADR-0023`,
    # «Следствия» — три вёрстки одного содержания).
    outcome, record_path = model_path(args, doc, target, outage=outage)
    doc, painter, picture_note = _pictures(args, _visuals(outcome.doc, analysis), analysis, outage)
    # Объём вёрстки: колоде модели — только заданный (рамки промпта она уже
    # получила выше), пути без модели — и потолок ТЗ по умолчанию (`Z-70`).
    target = volume_for(doc, target)

    # Несколько вариантов вёрстки — отдельная ветка (`ADR-0020`, `Z-26`).
    # Умолчание не меняется: без флага собирается одна колода ровно как раньше.
    # Это не вежливость к старому коду — девять сдаточных колод собираются этой
    # же командой, и молчаливая смена поведения испортила бы их незаметно.
    judge = _judge(args, analysis, outage)
    picker = _icons(args, outage)
    if int(getattr(args, "variants", 1) or 1) > 1:
        return _build_variants(args, analysis, doc, target, started,
                               _model_line(outcome, record_path), painter, picture_note, judge,
                               outage, picker)

    sha = analysis.design_system.source.sha256
    plan = plan_deck(doc, analysis.patterns, sha, target=target)
    plan = _painted(plan, painter, doc, analysis.patterns,
                    lambda d: plan_deck(d, analysis.patterns, sha, target=target))
    plan = judge.mark(plan, analysis.patterns)
    plan = picker.mark(plan, analysis.patterns)
    plan, fix_note = _shrunk(plan, analysis.patterns, args.fix_picks, None)
    picture_notes = tuple(n for n in (picture_note, painter.note(), judge.note(), picker.note()) if n)

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

        # Своё имя, а не `outcome`: то занято исходом пути модели, и сводка ниже
        # печатает его строку — с общим именем `build --verify` одной колоды
        # падал в конце сводки (найдено 26 сентября, PLAN-10.0, Ш5).
        checked = verify_deck(
            args.template, plan, analysis.patterns, target, report,
            rounds=args.rounds,
        )
        verify = checked.report
        plan, report = checked.plan, checked.build
        elapsed = time.perf_counter() - started
        problems = inspect_package(target)      # файл пересобран — проверить заново
        write_json(verify, os.path.join(args.out, "render-report.json"))

    audit_path, audit_lines = None, []
    if getattr(args, "audit", False):
        audit_path, audit_lines = _audited(
            args, target, plan, analysis.patterns, report,
            os.path.join(args.out, "render-report.json") if verify is not None else None,
            None, outage)

    export_lines, export_failed = ([], [])
    if getattr(args, "export", None):
        export_lines, export_failed = _export_lines([target], args.export)

    write_build_report(
        getattr(args, "report", None),
        [_deck_entry(
            target, report.slides, None, problems,
            os.path.join(args.out, "render-report.json") if verify is not None else None,
            audit_path,
            _placed(plan, report, painter),
        )],
        elapsed,
        plan,
        extra_warnings=picture_notes + ((fix_note,) if fix_note else ()) + tuple(export_failed),
    )

    if not args.quiet:
        kinds = {p.id: p.kind for p in analysis.patterns.patterns}
        print(f"шаблон      {os.path.basename(args.template)}")
        print(f"контент     {args.content}")
        print(_model_line(outcome, record_path))
        for note in picture_notes:
            print(f"картинки    {note}")
        if fix_note:
            print(f"исправление {fix_note}")
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
        for line in audit_lines:
            print(line)
        print(f"время       {elapsed:.2f} с")
        print(f"записано    {target}")
        for line in export_lines:
            print(f"экспорт     {line}")

    # Неустранённая вёрстка кодом возврата не считается: отказ ужимать текст
    # ниже предела читаемости — штатный исход, а не ошибка (`PLAN-4.0`,
    # первая логическая проверка шагов 6-9).
    return 2 if problems else 0


def _build_variants(args, analysis, doc, target, started, model_summary="",
                    painter=None, picture_note=None, judge=None, outage=None, picker=None):
    """Три (или сколько попросили) варианта вёрстки одного контента.

    Требование ТЗ, раздел 2, п. 5, и пункт критерия 3, который проверяют
    наличием. Архитектура — `ADR-0020`, разбор противоречия «различны, но
    одинаковы» — `PLAN-6.0`.
    """
    from .plan.variants import generate, load_policies, report as variants_report, select

    policies, min_distance, source = load_policies()
    variants = generate(
        doc, analysis.patterns, analysis.design_system.source.sha256, policies, target
    )
    chosen, reason = select(variants, int(args.variants), min_distance)

    os.makedirs(args.out, exist_ok=True)
    base = args.output or os.path.join(args.out, "deck.pptx")
    os.makedirs(os.path.dirname(os.path.abspath(base)), exist_ok=True)
    stem, ext = os.path.splitext(base)

    # Проверка вёрстки применяется **к каждому отобранному варианту**, а не к
    # одному победителю, как предполагал `ADR-0020`: победитель там был один, а
    # здесь все три идут в сдачу и все три увидит эксперт. Пропущено в первой
    # редакции и найдено растром — `WORKLOG/2026-09-19-variants-raster.md`.
    do_verify = bool(getattr(args, "verify", False))
    if do_verify:
        from .verify import verify_deck                       # noqa: PLC0415
        from .verify.report import describe, write_json       # noqa: PLC0415

    written, worst, fix_notes = [], 0, []
    sha = analysis.design_system.source.sha256
    audits: dict[int, tuple] = {}
    if painter is not None:
        # Сцены всех картинок всех вариантов — заготовок и рамок — одним
        # вызовом модели по всей колоде (`Painter.prepare`).
        painter.prepare([v.plan for v in chosen], analysis.patterns, doc)
    for n, variant in enumerate(chosen, 1):
        path = f"{stem}-{n}{ext}"
        plan = variant.plan
        if painter is not None:
            # Картинка — в пропорции места, которое ей дал именно этот вариант;
            # одинаковые размеры варианты берут из кэша художника (`Z-28`).
            tuning = variant.policy.tuning
            plan = _painted(plan, painter, doc, analysis.patterns,
                            lambda d, t=tuning: plan_deck(d, analysis.patterns, sha, target, tuning=t))
        if judge is not None:
            plan = judge.mark(plan, analysis.patterns)
        if picker is not None:
            plan = picker.mark(plan, analysis.patterns)
        plan, fix_note = _shrunk(plan, analysis.patterns, getattr(args, "fix_picks", ()), n)
        if fix_note:
            fix_notes.append(f"вариант {n}: {fix_note}")
        built = compose_deck(args.template, plan, analysis.patterns, path)
        verdict = None
        if do_verify:
            outcome = verify_deck(
                args.template, plan, analysis.patterns, path, built,
                rounds=args.rounds,
            )
            verdict, plan, built = outcome.report, outcome.plan, outcome.build
            write_json(verdict, os.path.join(args.out, f"render-report-{n}.json"))
        problems = inspect_package(path)
        worst = max(worst, len(problems))
        audit_path, audit_lines = None, []
        if getattr(args, "audit", False):
            audit_path, audit_lines = _audited(
                args, path, plan, analysis.patterns, built,
                os.path.join(args.out, f"render-report-{n}.json") if verdict is not None else None,
                n, outage)
        audits[n] = (audit_path, audit_lines)
        written.append((n, variant, path, built, problems, verdict, plan))
    elapsed = time.perf_counter() - started
    picture_notes = tuple(n for n in (picture_note, painter.note() if painter else None,
                                      judge.note() if judge else None,
                                      picker.note() if picker else None) if n)
    export_lines, export_failed = ([], [])
    if getattr(args, "export", None):
        export_lines, export_failed = _export_lines([path for _n, _v, path, *_ in written], args.export)

    write_build_report(
        getattr(args, "report", None),
        [
            _deck_entry(
                path, built.slides, n, problems,
                os.path.join(args.out, f"render-report-{n}.json") if verdict is not None else None,
                audits.get(n, (None,))[0],
                _placed(pl, built, painter),
            )
            for n, _v, path, built, problems, verdict, pl in written
        ],
        elapsed,
        [pl for *_rest, pl in written],
        extra_warnings=(reason,) + picture_notes + tuple(fix_notes) + tuple(export_failed),
    )

    if not args.quiet:
        print(f"шаблон      {os.path.basename(args.template)}")
        print(f"контент     {args.content}")
        if model_summary:
            print(model_summary)
        for note in picture_notes:
            print(f"картинки    {note}")
        for note in fix_notes:
            print(f"исправление {note}")
        print(f"политик     {len(policies)} ({source}), порог различия {min_distance:.0%}")
        for line in variants_report(chosen, reason):
            print(f"            {line}")
        for n, _v, path, built, problems, verdict, _pl in written:
            print(f"  вариант {n}: слайдов {built.slides}, "
                  f"структурных проблем {len(problems)} -> {path}")
            if verdict is not None:
                for line in describe(verdict):
                    print(f"    {line}")
            for line in audits.get(n, (None, []))[1]:
                print(f"    {line}")
        print(f"время       {elapsed:.2f} с на {len(chosen)} вариант(ов)")
        for line in export_lines:
            print(f"экспорт     {line}")

    # Отобрано меньше запрошенного — это не ошибка запуска, а свойство шаблона,
    # и причина уже напечатана. Кодом возврата отвечает только структурная
    # проверка, как и в обычной сборке.
    return 2 if worst else 0


def _export_lines(decks, value) -> tuple[list[str], list[str]]:
    """`build --export`: каждая собранная колода — в `.html` и `.pdf`
    (`Z-27`, `PLAN-10.0`, Ш6). Возвращает строки сводки и отказы — их сборка
    кладёт в предупреждения отчёта: форма отчёта неизменна (`Z-46`)."""
    from .export.run import export_deck, parse_formats

    formats = parse_formats(value)
    lines, failed = [], []
    for deck in decks:
        for item in export_deck(deck, formats):
            lines.append(item.line)
            if item.path is None:
                failed.append(f"{os.path.basename(deck)}: {item.line}")
    return lines, failed


def cmd_export(args):
    """Собранная колода — в `.html` и `.pdf` (ТЗ, п. 7; `Z-27`). Без флагов —
    оба формата, рядом с колодой или в `-o`."""
    from .export.run import FORMATS, export_deck

    wanted = tuple(f for f in FORMATS if getattr(args, f, False)) or FORMATS
    out_dir = getattr(args, "out", None)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    done = export_deck(args.deck, wanted, out_dir)
    for item in done:
        print(item.line)
    if any(item.busy for item in done):
        return 3
    return 0 if all(item.path for item in done) else 1


def _add_model_args(parser: argparse.ArgumentParser) -> None:
    """Флаги модели — у `plan` и у `build`: модель живёт в стадии PLAN (`ADR-0023`).

    Умолчание обоих — `None`, а не значение: доступ без флага берётся из
    `config/model.json` (иначе умолчаний стало бы два), режим текста —
    `outline.DEFAULT_MODE` (`improve` — решение пользователя 26 сентября). `None`
    нужен и сводке: она различает «задано при запуске» и «умолчание».
    """
    parser.add_argument(
        "--llm",
        choices=LLM_CHOICES,
        default=None,
        help="доступ к модели: off — без модели; cache — ответы из кэша, сети нет; "
             "on — звать модель и пополнять кэш. По умолчанию — access из "
             "config/model.json (ADR-0021). Модель строит колоду только из сплошного "
             "текста; нет ответа, ответ не прошёл проверки или текст длиннее порога — "
             "колода собирается путём без модели, и сводка говорит почему "
             f"(ADR-0023; подробно — <out>/{outline.RECORD_NAME})",
    )
    parser.add_argument(
        "--text",
        choices=TEXT_CHOICES,
        default=None,
        help="что модель делает с текстом автора: keep — фразы дословно; improve — "
             "переписать для ясности, не добавляя фактов (ADR-0023). По умолчанию "
             f"{outline.DEFAULT_MODE}",
    )


def _check_model_args(args: argparse.Namespace) -> None:
    """`choices` действуют только на командную строку: значение из конфига
    прогона `argparse` не видит, и `{"llm": "maybe"}` прошёл бы молча (замер Ш2)."""
    for name, allowed in (("llm", LLM_CHOICES), ("text", TEXT_CHOICES)):
        value = getattr(args, name, None)
        if value is not None and value not in allowed:
            raise SystemExit(
                f"{args.command}: {name} = {value!r} — ожидается одно из: {', '.join(allowed)}"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mimeo", description="Перенос стиля презентаций: разбор шаблона и сборка колоды."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="разобрать шаблон и извлечь дизайн-систему")
    analyze.add_argument("template", nargs="?", help="путь к .pptx или .potx")
    analyze.add_argument("-o", "--out", default="out", help="каталог для артефактов")
    analyze.add_argument("--validate", action="store_true", help="проверить артефакт по схеме")
    analyze.add_argument("-q", "--quiet", action="store_true", help="без сводки")
    analyze.add_argument(
        "--config",
        metavar="ФАЙЛ",
        help="JSON с аргументами прогона: те же ключи, что у флагов. Командная строка важнее файла (ТЗ, раздел 6)",
    )
    analyze.set_defaults(func=cmd_analyze)

    plan = sub.add_parser(
        "plan", help="разложить контент по раскладкам шаблона; колоду из сплошного "
                     "текста строит модель (--llm), раскладки выбирает код"
    )
    plan.add_argument("template", nargs="?", help="путь к .pptx или .potx")
    plan.add_argument("content", nargs="?", help="путь к markdown или текстовому файлу с контентом")
    plan.add_argument("-o", "--out", default="out", help="каталог для артефактов")
    plan.add_argument("--validate", action="store_true", help="проверить артефакт по схеме")
    plan.add_argument(
        "--slides",
        metavar="N|N-M",
        help=f"целевое число слайдов; рамки ТЗ — {TZ_SLIDES}. По умолчанию модель "
             f"получает рамки ТЗ, путь без модели — только потолок {DEFAULT_VOLUME[1]} (Z-70)",
    )
    plan.add_argument("-q", "--quiet", action="store_true", help="без сводки")
    _add_model_args(plan)
    plan.add_argument(
        "--config",
        metavar="ФАЙЛ",
        help="JSON с аргументами прогона: те же ключи, что у флагов. Командная строка важнее файла (ТЗ, раздел 6)",
    )
    plan.set_defaults(func=cmd_plan)

    build = sub.add_parser(
        "build", help="шаблон плюс контент -> готовый .pptx; колоду из сплошного "
                      "текста строит модель (--llm), вёрстку — код"
    )
    build.add_argument("template", nargs="?", help="путь к .pptx или .potx")
    build.add_argument("content", nargs="?", help="путь к markdown или текстовому файлу с контентом")
    build.add_argument("-o", "--out", default="out", help="каталог для артефактов")
    build.add_argument("--output", help="путь к итоговому файлу (по умолчанию out/deck.pptx)")
    build.add_argument(
        "--slides",
        metavar="N|N-M",
        help=f"целевое число слайдов; рамки ТЗ — {TZ_SLIDES}. По умолчанию модель "
             f"получает рамки ТЗ, путь без модели — только потолок {DEFAULT_VOLUME[1]} (Z-70)",
    )
    build.add_argument(
        "--variants",
        type=int,
        default=1,
        metavar="N",
        help="сколько вариантов вёрстки собрать (ТЗ требует 3). Файлы получают "
             "суффикс -1, -2, -3. Если шаблон беден раскладками и N заметно "
             "различных колод из него не выходит, будет собрано меньше и "
             "названа причина",
    )
    build.add_argument(
        "--report",
        metavar="ФАЙЛ",
        help="куда положить машинночитаемый ИТОГ сборки (JSON): пути колод, число "
             "слайдов, структурные проблемы, предупреждения. Не путать с "
             "--output, который задаёт путь самой колоды. Нужен веб-слою: "
             "разбирать печатаемую сводку нельзя, её формулировки меняются "
             "(contracts/build-report.schema.json)",
    )
    build.add_argument("-q", "--quiet", action="store_true", help="без сводки")
    _add_model_args(build)
    build.add_argument(
        "--images",
        choices=("on", "off"),
        default=None,
        help="рисовать ли картинки по идеям модели генератором (Z-28): on — да, "
             "off — идеи остаются словами. По умолчанию — access из config/generator.json",
    )
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
    build.add_argument(
        "--purpose",
        choices=outline.PURPOSES,
        default=None,
        help="назначение презентации (ТЗ, бизнес-задача 2; Z-37): feature — питч фичи, "
             "product — питч продукта, project — отчёт по проекту, initiative — защита "
             "инициативы. Модель расставляет темы автора в порядке, принятом для такого "
             "назначения, и новых разделов не добавляет. Без флага — порядок по тексту",
    )
    build.add_argument(
        "--audit",
        action="store_true",
        help="аудит готовых слайдов (ТЗ, п. 6; Z-34): проверки кодом — пункты, слова, наезд "
             "надписей — и моделью по картинке слайда — вывод в заголовке, служебный текст, "
             "язык. Отчёт audit[-N].json рядом с колодой; картинки рисует PowerPoint (Windows)",
    )
    build.add_argument(
        "--fix",
        metavar="ФАЙЛ|all",
        help="исправить выбранные находки аудита прошлой сборки (Z-34): ФАЙЛ — JSON с "
             "выбранными находками (его пишет веб), all — все исправимые из audit*.json в "
             "каталоге -o. Смысловые переписывает модель, вёрсточные ужимаются; после — "
             "аудит заново",
    )
    build.add_argument(
        "--export",
        metavar="html,pdf",
        help="выгрузить каждую собранную колоду ещё и в .html и/или .pdf рядом с ней "
             "(ТЗ, п. 7; Z-27): html — свой рендер, работает везде; pdf — PowerPoint "
             "(Windows) или LibreOffice. Без флага — только .pptx",
    )
    build.add_argument(
        "--config",
        metavar="ФАЙЛ",
        help="JSON с аргументами прогона: те же ключи, что у флагов. Командная строка важнее файла (ТЗ, раздел 6)",
    )
    build.set_defaults(func=cmd_build)

    export = sub.add_parser(
        "export", help="собранную колоду .pptx — в .html и .pdf (ТЗ, п. 7)"
    )
    export.add_argument("deck", help="путь к собранному .pptx")
    export.add_argument("--html", action="store_true",
                        help="HTML: свой рендер, текст разметкой, картинки внутри файла; работает везде")
    export.add_argument("--pdf", action="store_true",
                        help="PDF: рисует PowerPoint (Windows) или LibreOffice")
    export.add_argument("-o", "--out", help="каталог для файлов (по умолчанию — рядом с колодой)")
    export.set_defaults(func=cmd_export)

    return parser


#: Ключи конфига прогона, которые не являются аргументами команды.
#: `_`-ключи — комментарии рядом со значением, как в остальных конфигах.
_RUN_META = ("version", "config", "func", "command")


def _named_explicitly(argv: list[str] | None) -> set[str]:
    """Что пользователь назвал в командной строке **явно**.

    Нужно ради приоритета: командная строка важнее файла. Сравнивать значение с
    умолчанием нельзя — `--variants 1` совпадает с умолчанием и выглядел бы как
    «не задано», и тогда конфиг молча перебил бы явную просьбу пользователя.

    Поэтому разбор идёт вторым парсером, у которого умолчания сняты: в
    результат попадает только то, что действительно было в `argv`.
    """
    shadow = build_parser()
    stack = [shadow]
    while stack:
        parser = stack.pop()
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                stack.extend(action.choices.values())
                continue
            action.default = argparse.SUPPRESS
    try:
        return set(vars(shadow.parse_args(argv)))
    except SystemExit:
        # Настоящий парсер разберёт те же аргументы и сам скажет, что не так.
        return set()


def apply_run_config(args: argparse.Namespace, argv: list[str] | None) -> list[str]:
    """Подставить значения из `--config` туда, где пользователь смолчал.

    Возвращает строки для сводки: чем прогон задан и что в файле лишнее.
    Приоритет — командная строка выше файла выше умолчания (ТЗ, раздел 6:
    «воспроизводимый сетап и запуск конфиг файлом»).
    """
    path = getattr(args, "config", None)
    if not path:
        return []

    stamp = cfg.stamp_file(path)
    if not stamp.loaded:
        raise SystemExit(f"Конфиг прогона не прочитан: {path}")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except ValueError as exc:
        # Словами, а не трассировкой. Частая причина на Windows — путь с
        # обратными слэшами: в JSON это управляющая последовательность.
        raise SystemExit(
            f"Конфиг прогона — не JSON: {path}{NL}  {exc}{NL}"
            f"  Пути в JSON пишутся через / или с удвоенным обратным слэшем."
        ) from exc
    if not isinstance(raw, dict):
        raise SystemExit(f"Конфиг прогона должен быть объектом JSON: {path}")

    explicit = _named_explicitly(argv)
    known = set(vars(args)) - set(_RUN_META)
    taken, ignored, unknown = [], [], []
    for key, value in raw.items():
        # Пояснения рядом со значением. В конфигах движка их две формы, и обе
        # настоящие: `"_"` — заметка про весь файл, `"ключ__"` — про соседний
        # ключ (`config/variants.json`, `config/prose.json`). Знать надо обе:
        # проверка поймала ровно это — половина пояснений уходила в «не знает
        # таких ключей» и выглядела как россыпь опечаток.
        if key.startswith("_") or key.endswith("__") or key in _RUN_META:
            continue
        if key not in known:
            unknown.append(key)
            continue
        if key in explicit:
            ignored.append(key)
            continue
        setattr(args, key, value)
        taken.append(key)

    version = f" версия {stamp.version}" if stamp.version else " версия не объявлена"
    lines = [f"конфиг      {path}{version}, sha256 {stamp.sha256[:12]}…"]
    if taken:
        lines.append(f"            из файла: {', '.join(sorted(taken))}")
    if ignored:
        lines.append(f"            перекрыто командной строкой: {', '.join(sorted(ignored))}")
    if unknown:
        # Не «мягко проигнорировать»: опечатка в ключе иначе останется незамеченной,
        # а прогон тихо пойдёт не на тех значениях.
        lines.append(f"            команда {args.command} не знает ключей: {', '.join(sorted(unknown))}")
    return lines


def _require(args: argparse.Namespace, *names: str) -> None:
    """Позиционные стали необязательными ради `--config`; проверить их теперь
    наша работа, а не argparse."""
    missing = [n for n in names if not getattr(args, n, None)]
    if missing:
        hint = " или задайте их в --config" if not getattr(args, "config", None) else ""
        raise SystemExit(
            f"{args.command}: не задано обязательное — {', '.join(missing)}{hint}"
        )


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
        notes = apply_run_config(args, argv)
        _check_model_args(args)
        if args.command != "export":
            _require(args, *(("template",) if args.command == "analyze" else ("template", "content")))
        if notes and not getattr(args, "quiet", False):
            print(NL.join(notes))
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
    except cfg.MissingConfig as exc:
        # Промптов в коде нет (`Z-72`): без файла — остановка и как починить.
        print(f"Нет конфига: {exc}", file=sys.stderr)
        return 1
