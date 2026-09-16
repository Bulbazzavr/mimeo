"""Сборка с проверкой вёрстки: собрать, измерить, починить план, пересобрать.

**С 13 сентября это тонкая обёртка.** Петля переехала в продукт
(`mimeo/verify/loop.py`, шаг 6 плана `PLAN-4.0`), и то же самое делает штатная
команда:

    python -m mimeo build шаблон.pptx контент.md --output out/deck.pptx --verify

Инструмент оставлен потому, что на него ссылаются `STATE` и прежние записи
`WORKLOG`, и потому что здесь удобнее крутить `--rounds`. Своей логики у него
больше нет — вся она в `verify_deck`.

    python tools/build_verified.py шаблон.pptx контент.md -o out/deck.pptx

Запускает настоящий PowerPoint на рабочем столе — только с согласия пользователя
(`ADR-0013`). Без Windows с Office петля пропускается, и файл собирается как
обычно: стадия необязательна по построению.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from mimeo.analyze import analyze_template  # noqa: E402
from mimeo.compose.builder import build  # noqa: E402
from mimeo.plan import load_content, plan_deck  # noqa: E402
from mimeo.verify import DEFAULT_ROUNDS, verify_deck  # noqa: E402
from mimeo.verify.report import describe  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("template", type=pathlib.Path)
    parser.add_argument("content", type=pathlib.Path)
    parser.add_argument("-o", "--output", type=pathlib.Path, default=pathlib.Path("out/deck.pptx"))
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    args = parser.parse_args(argv)

    analysis = analyze_template(args.template)
    doc = load_content(args.content)
    plan = plan_deck(doc, analysis.patterns, analysis.design_system.source.sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    report = build(str(args.template), plan, analysis.patterns, str(args.output))
    print(f"собрано     {report.slides} слайдов, подставлено {report.substituted} слотов")

    outcome = verify_deck(
        str(args.template), plan, analysis.patterns, str(args.output),
        report, rounds=args.rounds,
    )
    for line in describe(outcome.report):
        print(line)
    print(f"записано    {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
