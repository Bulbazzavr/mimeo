"""Замер: какие раскладки пригодны каждому разделу контента и с каким рангом.

Инструмент для `PLAN-2.1` (`Z-19`). Отвечает на вопрос «из чего вообще был выбор»,
а не «что выбрали»: план показывает только победителя, а причина повторов видна
лишь по всему списку кандидатов и разрыву между ними.

    python tools/rank_dump.py samples/шаблон.pptx examples/content-demo.md

Сети не трогает, файлов не пишет.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from mimeo.analyze import analyze_template  # noqa: E402
from mimeo.plan import load_content  # noqa: E402
from mimeo.plan.deterministic import _with_cover  # noqa: E402
from mimeo.plan.matching import rank  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("template", type=pathlib.Path)
    parser.add_argument("content", type=pathlib.Path)
    parser.add_argument("--top", type=int, default=6, help="сколько кандидатов показывать")
    args = parser.parse_args(argv)

    analysis = analyze_template(args.template)
    doc = load_content(args.content)
    patterns = analysis.patterns.patterns
    size = {p.id: len(p.slots) for p in patterns}

    print("раскладки  " + ", ".join(f"{p.id}={size[p.id]}/{p.kind}" for p in patterns))
    print()

    for n, section in enumerate(_with_cover(doc)):
        units = sum(b.units for b in section.blocks) + (1 if section.heading else 0)
        title = (section.heading or section.id)[:40]
        print(f"--- раздел {n}: «{title}» — единиц контента: {units}")
        ranked = rank(section, patterns)
        if not ranked:
            print("    пригодных раскладок нет")
        for m in ranked[: args.top]:
            filled = len(m.fills)
            print(
                f"    {m.pattern_id} {m.kind:<11} ранг {m.score:+.3f}  "
                f"слотов {size[m.pattern_id]:>2}  заполнено {filled}  "
                f"пусто {size[m.pattern_id] - filled}"
            )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
