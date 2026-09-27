"""Выгрузка собранной колоды в `.html` и `.pdf` — `Z-27`, `PLAN-10.0`, Ш6.

ТЗ, п. 7: экспорт в `.html`, `.pptx`, `.pdf` — функция сервиса. `.pptx` —
сама колода; здесь — два других формата и слова о том, что вышло:

* **HTML** — свой рендер пакета (`export/html.py`), стандартная библиотека:
  работает везде, и на Linux, где запускают эксперты; текст — разметкой, не
  картинкой (организаторы 17 сентября: для HTML «безопаснее разметка, а не
  скриншоты»);
* **PDF** — PowerPoint или LibreOffice (`export/pdf.py`): его рисует тот, кто
  рисует слайды.

Файлы ложатся рядом с колодой или в `out_dir`, с тем же именем.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

FORMATS = ("html", "pdf")


@dataclass(frozen=True)
class Exported:
    format: str
    path: str | None      # None — не вышло
    line: str             # что сказать человеку
    busy: bool = False    # PDF: PowerPoint открыт пользователем


def target(deck: str, fmt: str, out_dir: str | None = None) -> str:
    stem = os.path.splitext(os.path.basename(deck))[0]
    return os.path.join(out_dir or os.path.dirname(os.path.abspath(deck)), f"{stem}.{fmt}")


def export_deck(deck: str, formats=FORMATS, out_dir: str | None = None) -> list[Exported]:
    """Колода `deck` во все форматы `formats`. Отказ одного формата не мешает
    другому и называется словами."""
    done: list[Exported] = []
    for fmt in formats:
        path = target(deck, fmt, out_dir)
        started = time.perf_counter()
        if fmt == "html":
            from .html import render_html

            try:
                report = render_html(deck, path)
            except Exception as exc:                  # noqa: BLE001 — рендер не валит сборку
                done.append(Exported("html", None, f"HTML не собран: {type(exc).__name__}: {exc}"))
                continue
            skipped = f"; упрощено или пропущено: {', '.join(report.unsupported)}" if report.unsupported else ""
            done.append(Exported("html", path,
                                 f"HTML {path} — слайдов {report.slides}, картинок {report.images}, "
                                 f"{time.perf_counter() - started:.1f} с{skipped}"))
        elif fmt == "pdf":
            from .pdf import export_pdf

            result = export_pdf(deck, path)
            if result.ok:
                pages = f"страниц {result.pages}" if result.pages is not None else "страниц не сосчитать"
                done.append(Exported("pdf", result.output,
                                     f"PDF {result.output} — {pages}, рисовал {result.engine}, "
                                     f"{time.perf_counter() - started:.1f} с"))
            else:
                done.append(Exported("pdf", None, f"PDF не собран: {result.problem}", busy=result.busy))
        else:
            done.append(Exported(fmt, None, f"формат {fmt!r} не знаем: {', '.join(FORMATS)}"))
    return done


def parse_formats(value: str | None) -> tuple[str, ...]:
    """`html,pdf` → ('html', 'pdf'); пусто — оба. Неизвестный — отказ словами."""
    if not value:
        return FORMATS
    wanted = tuple(dict.fromkeys(v.strip().lower().lstrip(".") for v in value.split(",") if v.strip()))
    unknown = [w for w in wanted if w not in FORMATS]
    if unknown:
        raise SystemExit(f"--export: формат {', '.join(unknown)} не знаем; есть {', '.join(FORMATS)}")
    return wanted
