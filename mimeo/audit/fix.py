"""Исправление выбранного — `build --fix`, `Z-34`, `PLAN-11.0`, шаг 5.

ТЗ, п. 6: «пользователь выбирает, какие из них исправить». На вход — файл с
выбранными находками аудита (их пишет веб, или `--fix all` берёт все
исправимые из отчётов прошлой сборки в том же каталоге). Два вида исправления:

* `model` — смысл: заголовок не вывод, пункт длиннее 15 слов, служебный текст…
  Модель получает свою колоду и перечень находок по заголовкам слайдов и
  переписывает названные слайды; ответ судят те же проверки, что первый
  (`plan/outline.run`, `revise`). Колода модели одна на все варианты, поэтому
  правка смысла меняет все три.
* `layout` — вёрстка: наезд надписей, текст шире места, за краем слайда.
  Кегль текста этого слайда — на ступень ниже (`STEP`), дальше VERIFY, как
  всегда. Только в своём варианте.
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import replace

#: Ступень ужатия слайда за одно исправление. Та же шкала шрифта, которой
#: ремонт VERIFY ужимает переполнение (`ADR-0005`), — не новая мерка.
STEP = 0.85
#: Ниже этой шкалы исправление не ужимает — предел ремонта VERIFY.
MIN_SCALE = 25


def load(spec: str, out_dir: str) -> list[dict]:
    """Выбранные находки: путь к JSON (`{"findings": [...]}` или список) или
    `all` — все исправимые из `audit*.json` в `out_dir`."""
    if spec == "all":
        picked = []
        for path in sorted(glob.glob(os.path.join(out_dir, "audit*.json"))):
            try:
                with open(path, encoding="utf-8") as fh:
                    picked.extend(f for f in json.load(fh).get("findings") or () if f.get("fix"))
            except (OSError, ValueError, AttributeError):
                continue
        return picked
    with open(spec, encoding="utf-8") as fh:
        raw = json.load(fh)
    items = raw.get("findings") if isinstance(raw, dict) else raw
    return [f for f in items or () if isinstance(f, dict) and f.get("fix")]


def revise_lines(picked: list[dict]) -> tuple[str, ...]:
    """Строки перечня для модели: по заголовку слайда, без повторов."""
    lines, seen = [], set()
    for f in picked:
        if f.get("fix") != "model":
            continue
        where = f"слайд «{f['title']}»" if f.get("title") else "вся колода"
        line = f"{where}: {f.get('detail')}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return tuple(lines)


def layout_titles(picked: list[dict], variant: int | None) -> set[str]:
    """Заголовки слайдов этого варианта, которые надо ужать."""
    return {" ".join(str(f.get("title") or "").split()) for f in picked
            if f.get("fix") == "layout" and (f.get("variant") or None) == (variant or None)}


def shrink(plan, library, titles: set[str]) -> tuple[object, int]:
    """План, где у слайдов с этими заголовками текст на ступень мельче.
    Возвращает (план, сколько слайдов ужато)."""
    from .checks import views

    if not titles:
        return plan, 0
    hit = {v.index for v in views(plan, library) if v.title in titles}
    if not hit:
        return plan, 0
    slides = []
    for slide in plan.slides:
        if slide.index in hit:
            fills = tuple(
                replace(f, font_scale=max(MIN_SCALE, int((f.font_scale or 100) * STEP)))
                if (f.text or f.items) and f.kind not in ("image", "table", "chart") else f
                for f in slide.fills)
            slide = replace(slide, fills=fills)
        slides.append(slide)
    return replace(plan, slides=tuple(slides)), len(hit)


def variant_of(finding: dict) -> int | None:
    return finding.get("variant") or None
