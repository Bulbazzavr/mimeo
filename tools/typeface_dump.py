"""Замер: сколько слайдов садятся в слот с гарнитурой не под прозу.

Инструмент задачи `Z-43` (`PLAN-7.3`). Отвечает на два вопроса, которые по
одному слайду не увидеть: **сколько** таких слайдов по всему корпусу и **какие
гарнитуры** шаблонов правило вообще считает чужими.

    python tools/typeface_dump.py                      весь корпус
    python tools/typeface_dump.py --fonts              только гарнитуры шаблонов
    python tools/typeface_dump.py samples/шаблон.pptx  один шаблон

Сети не трогает, файлов не пишет. Числа кладутся в `WORKLOG/`.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from mimeo.analyze import analyze_template  # noqa: E402
from mimeo.analyze.typeface import ICON, MONO, classify, load_config  # noqa: E402
from mimeo.plan import load_content  # noqa: E402
from mimeo.plan.deterministic import plan_deck  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
FOREIGN = (MONO, ICON)


def templates(given: list[pathlib.Path]) -> list[pathlib.Path]:
    if given:
        return given
    out = sorted((ROOT / "samples").glob("*.pptx"))
    tz = ROOT / "tz" / "templates"
    if tz.is_dir():
        out += sorted(tz.glob("*.pptx"))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("template", nargs="*", type=pathlib.Path)
    parser.add_argument("--content", type=pathlib.Path, default=None,
                        help="один входной текст вместо всего корпуса")
    parser.add_argument("--fonts", action="store_true",
                        help="только разбор гарнитур шаблонов, без планирования")
    args = parser.parse_args(argv)

    cfg = load_config()
    print(f"списки гарнитур: {cfg.source}"
          + ("" if cfg.loaded else "  (файла нет, работают встроенные)"))

    contents = ([args.content] if args.content
                else sorted((ROOT / "examples").glob("content-*.md")))
    total_slides = total_bad = 0
    rows: list[tuple[str, str, int, int]] = []

    for tpath in templates(args.template):
        analysis = analyze_template(tpath)
        foreign_slots: dict[str, dict[str, str]] = {}
        for pattern in analysis.patterns.patterns:
            hit = {s.id: s.typeface_kind for s in pattern.slots
                   if s.typeface_kind in FOREIGN and s.content_type in ("text", "list", "number")}
            if hit:
                foreign_slots[pattern.id] = hit

        if args.fonts or foreign_slots:
            seen = collections.Counter()
            for role in analysis.design_system.type_scale:
                seen[(role.latin, classify(role.latin, "", cfg))] += role.count
            chuzhie = [f"{fam}={kind}" for (fam, kind), _ in seen.most_common()
                       if kind in FOREIGN]
            print(f"\n=== {tpath.name}")
            print(f"    гарнитур в шкале: {len(seen)}"
                  + (f", чужих: {', '.join(chuzhie)}" if chuzhie else ", чужих нет"))
            if foreign_slots:
                for pid, hit in sorted(foreign_slots.items()):
                    kind = next(p.kind for p in analysis.patterns.patterns if p.id == pid)
                    print(f"    {pid} ({kind}): "
                          + ", ".join(f"{sid}={k}" for sid, k in sorted(hit.items())))
        if args.fonts:
            continue

        for cpath in contents:
            plan = plan_deck(load_content(cpath), analysis.patterns, analysis.design_system)
            # Считаются только слоты, куда лёг **текст**: ровно то, что
            # штрафует ранг. Картинке в слоте с чужой гарнитурой всё равно,
            # и считать её значило бы завышать дефект.
            bad = sum(
                1 for sl in plan.slides
                if any(f.slot_id in foreign_slots.get(sl.pattern_id, {})
                       and f.kind in ("text", "list", "number")
                       for f in sl.fills)
            )
            total_slides += len(plan.slides)
            total_bad += bad
            if bad:
                rows.append((tpath.name, cpath.name, len(plan.slides), bad))

    if args.fonts:
        return 0

    print()
    if rows:
        print(f"{'шаблон':<48} {'контент':<20} слайдов  чужих")
        for name, content, n, bad in rows:
            print(f"{name[:46]:<48} {content:<20} {n:>7}  {bad:>5}")
    else:
        print("Ни одного слайда в слоте с чужой гарнитурой.")
    print(f"\nвсего слайдов: {total_slides}, из них в слоте с чужой гарнитурой: {total_bad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
