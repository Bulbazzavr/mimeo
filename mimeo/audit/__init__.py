"""Аудит готовых слайдов с выбором пользователя — `Z-34`, `PLAN-11.0`.

ТЗ, раздел 2, п. 6: «система визуализирует найденные проблемы, пользователь
выбирает, какие из них исправить»; на защите — какие проверки
детерминированные, какие контекстуальные. Проверки двух видов:

* **кодом** (`checks.py`) — по плану колоды и отчёту VERIFY: пункты, слова,
  пустые и одинаковые слайды, остатки вёрстки. На одном слайде — один ответ;
* **моделью** (`vision.py`) — зрение Gemma по картинке готового слайда
  (`raster.py`, рисует PowerPoint): вопросы валидации Приложения 1 и вёрстка
  глазами.

У каждой находки — чем её исправить: `model` — модель переписывает названные
слайды (`plan/outline.run`, `revise`), `layout` — кегль текста слайда на
ступень ниже и снова VERIFY, `None` — исправления нет, и деталь говорит
почему. Исправляется только выбранное: `build --fix` (`fix.py`), в вебе —
галочки и «Исправить отмеченное».

**«Не смог» — не «чисто».** Нет PowerPoint или модели — отчёт говорит, что
модель не спрошена, со статусом `partial`, и ноль её находок так не читается.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from . import checks, vision

REPORT_VERSION = "1.0"

#: Что проверяет аудит — для показа «как устроены проверки» (ТЗ, п. 6).
CHECKS = (
    ("items", "deterministic", f"больше {checks.MAX_ITEMS} пунктов на слайде"),
    ("long_item", "deterministic", f"пункт длиннее {checks.MAX_WORDS} слов"),
    ("empty", "deterministic", "слайд с одним заголовком"),
    ("duplicate", "deterministic", "два слайда с одним заголовком"),
    ("overflow", "deterministic", "текст не влез и после ремонта (VERIFY)"),
    ("too_wide", "deterministic", "надпись шире своего места (VERIFY)"),
    ("occluded", "deterministic", "текст закрыт фигурой шаблона (VERIFY)"),
    ("collision", "deterministic", "надпись наезжает на соседнюю (замер PowerPoint)"),
    ("off_slide", "deterministic", "текст выходит за край слайда (замер PowerPoint)"),
) + tuple((name, "contextual", label) for name, _n, label, _f in vision.QUESTIONS)


@dataclass
class AuditReport:
    deck: str
    variant: int | None
    status: str = "ok"                   # ok | partial | not_run
    note: str = ""
    slides: int = 0
    findings: list = field(default_factory=list)
    asked: int = 0
    cached: int = 0
    verify: dict | None = None
    seconds: float = 0.0

    def to_json(self) -> dict:
        return {
            "version": REPORT_VERSION,
            "deck": self.deck.replace("\\", "/"),
            "variant": self.variant,
            "status": self.status,
            "note": self.note,
            "slides": self.slides,
            "checks": [{"check": c, "kind": k, "label": label} for c, k, label in CHECKS],
            "model": {"asked": self.asked, "cached": self.cached},
            "verify": self.verify,
            "seconds": round(self.seconds, 1),
            "findings": self.findings,
        }


def audit_deck(deck: str, plan, library, slides_written, verify: dict | None,
               model_config, inputs: tuple[str, ...], out_dir: str, variant: int | None = None,
               outage=None, use_model: bool = True) -> AuditReport:
    """Аудит одной собранной колоды. `verify` — отчёт VERIFY словарём или
    `None` (не запускался); `out_dir` — куда класть картинки слайдов."""
    import time

    from ..plan.client import Access

    started = time.perf_counter()
    views = checks.views(plan, library, slides_written)
    report = AuditReport(deck=deck, variant=variant, slides=len(views))
    notes = []
    measurement = None
    try:
        from ..verify.metrics import MeasurerUnavailable, measure

        measurement = measure([os.path.abspath(deck)])[0]
        if not measurement.ok:
            notes.append(f"замер PowerPoint не удался ({measurement.note}) — наезд надписей "
                         "не проверен")
            measurement = None
    except MeasurerUnavailable as exc:
        notes.append(f"замера PowerPoint нет ({exc}) — наезд надписей не проверен")
    found = checks.run(views, verify, measurement)
    if verify is not None:
        defects = verify.get("defects") or {}
        report.verify = {"before": defects.get("before"), "after": defects.get("after")}
    if notes:
        report.status = "partial"

    if not use_model or model_config.access is Access.OFF:
        report.status = "partial"
        notes.append("модель выключена — проверки по картинке слайда не делались")
    else:
        from . import raster

        pictures = raster.render(deck, out_dir)
        if not pictures.ok:
            report.status = "partial"
            notes.append(f"картинок слайдов нет ({pictures.problem}) — модель не спрошена")
        else:
            eye = vision.Eye(vision.load_config(), model_config, inputs, outage=outage)
            total = len(pictures.pngs)
            by_position = {v.position: v for v in views}
            unanswered = 0
            for n, png in enumerate(pictures.pngs, 1):
                view = by_position.get(n)
                if view is None:
                    continue
                answer = eye.look(png, n, total)
                if answer is None:
                    unanswered += 1
                    continue
                found.extend(vision.findings(answer, view))
            report.asked, report.cached = eye.asked, eye.cached
            if eye.failure:
                report.status = "partial"
                notes.append(f"модель не ответила ({eye.failure}) — без ответа слайдов {unanswered}")
            elif unanswered:
                report.status = "partial"
                notes.append(f"ответ о {unanswered} слайдах не разобрался")

    found.sort(key=lambda f: (f["slide"], f["kind"], f["check"]))
    for k, f in enumerate(found, 1):
        f["id"] = f"{variant or 0}-{k}"
        f["variant"] = variant
    report.findings = found
    report.note = "; ".join(notes)
    report.seconds = time.perf_counter() - started
    return report


def write_json(report: AuditReport, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report.to_json(), fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


def describe(report: AuditReport) -> list[str]:
    """Сводка человеку: сколько нашли, чьими проверками, что исправимо."""
    by_kind = {"deterministic": 0, "contextual": 0}
    for f in report.findings:
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1
    fixable = sum(1 for f in report.findings if f.get("fix"))
    head = (f"аудит       находок {len(report.findings)}: кодом {by_kind['deterministic']}, "
            f"моделью по картинке {by_kind['contextual']}; исправимо {fixable}")
    if report.asked or report.cached:
        head += f" (спрошено слайдов {report.asked}, из кэша {report.cached})"
    lines = [head]
    if report.note:
        lines.append(f"            не всё проверено: {report.note}")
    for f in report.findings[:12]:
        mark = {"model": "модель перепишет", "layout": "ужать"}.get(f.get("fix"), "не чинится")
        lines.append(f"            [{f['id']}] слайд {f['slide']}: {f['detail']} — {mark}")
    if len(report.findings) > 12:
        lines.append(f"            … ещё {len(report.findings) - 12}")
    return lines
