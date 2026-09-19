"""Наполнить кэш ответов модели и посмотреть, что в нём лежит. `ADR-0021`.

Зачем нужен отдельный инструмент. Кэш — это способ выполнить критерий 2:
эксперт без видеокарты обязан получить наши колоды, а не тихо другие. Но ключ
кэша включает текст запроса, а текст зависит от `matching.py`, `prompt.py` и
анализатора: **первая же правка каталога паттернов обесценивает весь накопленный
кэш**. Значит пересоздание должно быть одной командой, иначе правило останется
обещанием (`PLAN-2.6`, план кода, находка 3).

    python tools/llm_cache.py list                        что лежит в обоих кэшах
    python tools/llm_cache.py fill <шаблон> <контент>     наполнить, зовя модель
    python tools/llm_cache.py fill <шаблон> <контент> --dry   не зовя: что ушло бы

**Каталог выбирается сам, по входным путям.** Шаблон из-под `tz/` — ответы
уедут в `tz/cache/llm/`, который исключён `.gitignore`; всё остальное — в
`cache/llm/`, который коммитится. Забыть флаг можно, подменить путь нельзя.

**Модель зовётся только здесь и только по явной команде.** Сборка колоды в сеть
не ходит: у неё режим `cache` (`ADR-0021`). Перед наполнением стоит убедиться,
что модель вообще готова: `python tools/llm_probe.py`.

Сейчас источник запросов один — промпт-контракт `ADR-0010`, вызов на секцию.
Запрос про смысловые роли добавится вместе с `Z-37`, и добавится сюда же.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mimeo.analyze import analyze_template  # noqa: E402
from mimeo.plan import cache, load_content, rank  # noqa: E402
from mimeo.plan.client import Access, ModelClient, load_config  # noqa: E402
from mimeo.plan.prompt import build_request  # noqa: E402
from mimeo.plan.validate import check, extract_json  # noqa: E402


def requests_for(template: str, content: str):
    """Все запросы, которые нужны на эту пару «шаблон + контент»."""
    analysis = analyze_template(template)
    by_id = {p.id: p for p in analysis.patterns.patterns}
    doc = load_content(content)
    for section in doc.sections:
        matches = rank(section, analysis.patterns.patterns)
        if not matches:
            continue
        yield build_request(section, matches, by_id, analysis.design_system), by_id


def cmd_fill(args) -> int:
    config = load_config()
    if not config.loaded:
        print(f"конфиг не прочитан ({config.source}), работают встроенные значения")
    config = replace(config, access=Access.OFF if args.dry else Access.ON)

    client = ModelClient(config, inputs=[args.template, args.content])
    print(f"кэш       {client.cache_root}")
    print(f"модель    {config.endpoint.model} на {config.endpoint.base_url}")
    print(f"режим     {config.access.value}"
          + ("  (только показать, что ушло бы)" if args.dry else ""))

    started = time.monotonic()
    bad = 0
    pairs = list(requests_for(args.template, args.content))
    print(f"запросов  {len(pairs)}\n")
    for request, by_id in pairs:
        key = client.key(request)
        size = len(request.system) + len(request.user)
        if args.dry:
            есть = cache.read(client.cache_root, key) is not None
            print(f"  {request.section_id:16} {size:6} зн.  {key[:12]}  "
                  f"{'в кэше есть' if есть else 'в кэше нет'}")
            continue
        answer = client.complete(request)
        if not answer:
            bad += 1
            print(f"  {request.section_id:16} {size:6} зн.  ОТКАЗ: {answer.note}")
            continue
        payload = extract_json(answer.text)
        pattern = by_id.get((payload or {}).get("pattern_id"))
        problems = check(payload, request, pattern) if payload else [1]
        mark = "годен" if not problems else f"негоден ({len(problems)} замечаний)"
        print(f"  {request.section_id:16} {size:6} зн.  {answer.source:5} "
              f"{answer.elapsed:5.1f} с  {mark}")

    print()
    for note in client.notes:
        print(f"  {note}")
    print(f"\nвсего {time.monotonic() - started:.1f} с, отказов {bad}")
    return 1 if bad and not args.dry else 0


def cmd_list(args) -> int:
    config = load_config()
    repo = cache.repo_root()
    total = 0
    for label, rel in (("коммитится", config.cache_root), ("НЕ коммитится", config.tz_cache_root)):
        root = os.path.normpath(os.path.join(repo, rel))
        files = sorted(f for f in os.listdir(root) if f.endswith(".json")) \
            if os.path.isdir(root) else []
        size = sum(os.path.getsize(os.path.join(root, f)) for f in files)
        total += len(files)
        print(f"{rel:16} {len(files):4} ответов, {size / 1024:7.1f} КиБ   ({label})")
    if not total:
        print("\nКэш пуст. Наполнить: python tools/llm_cache.py fill <шаблон> <контент>")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    fill = sub.add_parser("fill", help="наполнить кэш, вызывая модель")
    fill.add_argument("template", help="путь к .pptx или .potx")
    fill.add_argument("content", help="путь к контенту")
    fill.add_argument("--dry", action="store_true",
                      help="не звать модель: показать, что ушло бы и чего нет в кэше")
    fill.set_defaults(func=cmd_fill)

    listing = sub.add_parser("list", help="что лежит в обоих кэшах")
    listing.set_defaults(func=cmd_list)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
