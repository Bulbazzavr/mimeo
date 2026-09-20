"""Петля стадии VERIFY: собрать → измерить → починить план → пересобрать.

Шаг 6 плана `PLAN-4.0`. До 13 сентября петля жила в `tools/build_verified.py` и
печатала строки; здесь она возвращает **данные** — `VerifyReport`, — а печать и
запись отчёта остаются вызывающей стороне.

## Почему главный продукт — отчёт, а не исправленная вёрстка

Часть дефектов неустранима сознательно: ужимать текст ниже предела читаемости
хуже, чем оставить честное «не влезло» (`PLAN-4.0`, шаг 5). Поэтому петля обязана
объяснить, **почему она встала**, а не просто вернуть файл. Причин ровно пять, и
они различимы — `STOP_*` ниже. «Раунды кончились» и «упёрлись в предел» — разные
исходы: первый значит «сдался», второй «сделал всё, что мог».

## Откуда числа

Замерено на корпусе из одиннадцати шаблонов
(`WORKLOG/2026-09-13-verify-loop.md`).

**Потолок раундов.** Десяти колодам из одиннадцати хватает двух пересборок;
одиннадцатая не сходится и за шесть (`OQ-17`). Три — запас над двумя и граница
цены, а не круглое число.

**Контрольный замер пропускается, если колоду не пересобирали.** Сеансов
PowerPoint ровно `раундов + 1`, и каждый стоит ~4 с — это 60–70% всей стадии.
Когда петля встала по «всё влезло» или «упёрлись в предел», файл после
последнего замера не менялся, и контрольный замер померил бы тот же самый файл.

**Инвариант, который стоил бы тихой лжи:** `after` и `unresolved` никогда не
берутся из осмотра, сделанного **до** пересборки. За этим следит флаг `stale`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..model import SCHEMA_VERSION, DeckPlan, PatternLibrary
from .detect import Defect, Inspection, find_defects
from .metrics import MeasurerUnavailable, Measurement, measure
from .repair import repair_plan

#: Наших дефектов не осталось. Донорские могли остаться — см. `RoundRecord`.
STOP_FITS = "fits"
#: Ужимать дальше некуда: упёрлись в предел читаемости.
STOP_FLOOR = "floor"
#: Раунды кончились, а дефекты остались. Это «сдался», а не «сделал всё».
STOP_ROUNDS = "rounds"
#: Замер не состоялся. **Не** то же самое, что «дефектов нет» (`Z-20`).
STOP_NOT_MEASURED = "not_measured"
#: Мерить нечем: не Windows, нет Office, нет зонда. Стадия выключается сама.
STOP_UNAVAILABLE = "unavailable"

#: Замером подтверждённый потолок, не круглое число. См. «Откуда числа».
DEFAULT_ROUNDS = 3


@dataclass(frozen=True)
class RoundRecord:
    """Один раунд петли.

    `defects` и `ours` расходятся, и это не избыточность: донорская фигура может
    переполняться, а чинить её нечем — мы в неё ничего не подставляли. Сводка,
    показывающая один только `ours`, соврала бы «дефектов нет».
    """

    index: int
    status: str
    checked_slots: int
    defects: int          # всего, включая донорские фигуры
    ours: int             # наши слоты, которые можно чинить
    repaired: int         # слотов получили новую шкалу
    rebuilt: bool         # пересобирали ли колоду после этого осмотра

    def to_json(self) -> dict:
        return {
            "round": self.index,
            "status": self.status,
            "checked_slots": self.checked_slots,
            "defects": self.defects,
            "ours": self.ours,
            "repaired": self.repaired,
            "rebuilt": self.rebuilt,
        }


@dataclass(frozen=True)
class VerifyReport:
    """Что стадия сделала и чего не смогла.

    `before` и `after` равны `None`, когда замер не состоялся: ноль здесь читался
    бы как «дефектов нет», а это разные вещи (`Z-20`).
    """

    deck: str
    status: str                                   # ok | not_measured | unavailable
    stopped: str                                  # STOP_*
    rounds: tuple[RoundRecord, ...] = ()
    before: int | None = None
    after: int | None = None
    unresolved: tuple[Defect, ...] = ()
    seconds: float = 0.0
    note: str = ""
    version: str = SCHEMA_VERSION

    @property
    def measured(self) -> bool:
        return self.status == "ok"

    @property
    def fixed(self) -> int | None:
        if self.before is None or self.after is None:
            return None
        return self.before - self.after

    def to_json(self) -> dict:
        return {
            "version": self.version,
            "deck": self.deck,
            "status": self.status,
            "stopped": self.stopped,
            "rounds": [r.to_json() for r in self.rounds],
            "defects": {"before": self.before, "after": self.after},
            "unresolved": [
                {
                    "kind": d.kind,
                    "slide": d.slide_index,
                    "slot": d.slot_id,
                    "shape": d.shape_id,
                    "role": d.role,
                    "ratio": round(d.ratio, 3),
                }
                for d in self.unresolved
            ],
            "seconds": round(self.seconds, 2),
            "note": self.note,
        }


@dataclass
class VerifyOutcome:
    """Отчёт плюс то, что петля оставила после себя.

    План и отчёт сборки меняются по ходу: их возвращаем, чтобы вызывающая
    сторона печатала итог по **последней** сборке, а не по первой.
    """

    report: VerifyReport
    plan: DeckPlan
    build: object = None


def verify_deck(
    template: str,
    plan: DeckPlan,
    library: PatternLibrary,
    output: str,
    build_report,
    rounds: int = DEFAULT_ROUNDS,
    measurer=measure,
    builder=None,
) -> VerifyOutcome:
    """Догнать вёрстку до читаемой, насколько выйдет, и честно сказать, что вышло.

    Колода уже собрана — `output` и `build_report` от первой сборки. Петля
    меряет, чинит план и пересобирает; первую сборку она не делает, чтобы не
    собирать дважды.

    `measurer` и `builder` — швы для тестов. Без них ни один исход петли не
    проверить на машине без Office: всё интересное здесь — управляющая логика,
    а не измерение.

    Отдельной проверки «есть ли чем мерить» здесь нет намеренно: `measure()`
    сама бросает `MeasurerUnavailable`, а дубль наверху привязывал бы тесты к
    наличию Office на машине — проверено, шесть тестов от этого падали.
    """
    if builder is None:
        # Импорт внутри функции, а не в шапке модуля: `mimeo.verify` остаётся
        # импортируемым сам по себе, а шов для тестов — видимым в сигнатуре.
        # Цикла нет в любом случае — COMPOSE про VERIFY не знает.
        from ..compose.builder import build as builder   # noqa: PLC0415

    started = time.perf_counter()

    def done(status, stopped, log, before, after, unresolved, note=""):
        return VerifyOutcome(
            report=VerifyReport(
                deck=output,
                status=status,
                stopped=stopped,
                rounds=tuple(log),
                before=before,
                after=after,
                unresolved=tuple(unresolved),
                seconds=time.perf_counter() - started,
                note=note,
            ),
            plan=plan,
            build=build_report,
        )

    log: list[RoundRecord] = []
    before: int | None = None
    after: int | None = None
    unresolved: tuple[Defect, ...] = ()
    inspection: Inspection | None = None
    stale = False          # пересобирали ли колоду после последнего осмотра
    stopped = STOP_ROUNDS

    for index in range(1, max(1, rounds) + 1):
        try:
            measurement = _one(measurer, output)
        except MeasurerUnavailable as exc:
            return done("unavailable", STOP_UNAVAILABLE, log, before, after,
                        unresolved, str(exc))
        if measurement is None or not measurement.ok:
            note = measurement.note if measurement is not None else "измеритель молчит"
            status = measurement.status if measurement is not None else "not_measured"
            return done("not_measured", STOP_NOT_MEASURED, log,
                        before, after, unresolved, f"{status}: {note}")

        inspection = find_defects(measurement, plan, library, build_report.slides_written)
        if not inspection.measured:
            return done("not_measured", STOP_NOT_MEASURED, log, before, after,
                        unresolved, f"{inspection.status}: {inspection.note}")

        stale = False                                   # осмотр свеж: файл не трогали
        ours = len(inspection.repairable)
        if before is None:
            before = ours

        if ours == 0:
            log.append(_record(index, inspection, repaired=0, rebuilt=False))
            stopped = STOP_FITS
            break

        result = repair_plan(plan, inspection, measurement,
                             build_report.slides_written, library)
        if not result.changed:
            log.append(_record(index, inspection, repaired=0, rebuilt=False))
            stopped = STOP_FLOOR
            break

        plan = result.plan
        build_report = builder(template, plan, library, output)
        stale = True
        log.append(_record(index, inspection,
                           repaired=sum(1 for r in result.repairs if r.now != r.was),
                           rebuilt=True))

    # Контрольный замер — только если после последнего осмотра пересобирали.
    # Иначе он померил бы тот же файл, а это 4 секунды из 24 (`PLAN-4.0`).
    if stale:
        try:
            measurement = _one(measurer, output)
        except MeasurerUnavailable as exc:
            return done("unavailable", STOP_UNAVAILABLE, log, before, None,
                        (), str(exc))
        if measurement is None or not measurement.ok:
            note = measurement.note if measurement is not None else "измеритель молчит"
            return done("not_measured", STOP_NOT_MEASURED, log, before, None, (), note)
        inspection = find_defects(measurement, plan, library, build_report.slides_written)
        if not inspection.measured:
            return done("not_measured", STOP_NOT_MEASURED, log, before, None, (),
                        f"{inspection.status}: {inspection.note}")
        stale = False

    # Сюда можно попасть только со свежим осмотром — см. инвариант в шапке.
    if inspection is not None and not stale:
        after = len(inspection.repairable)
        unresolved = inspection.repairable

    # Раунды кончились ровно в тот момент, когда всё сошлось, — это «сделал», а
    # не «сдался». Без этой поправки честный успех отчитался бы как отказ.
    if stopped == STOP_ROUNDS and after == 0:
        stopped = STOP_FITS

    # А если не сошлось — спросить ремонт, изменил бы он ещё что-нибудь
    # (`Z-45`, `PLAN-7.5`). Это **не догадка, а тот же самый предикат**, которым
    # определён пол выше: план не меняется — значит пересборка дала бы байт в
    # байт тот же файл (`ADR-0003`), значит следующий осмотр нашёл бы те же
    # дефекты, значит петля встала бы с `floor`. Проба не предсказывает
    # следующий раунд, она его вычисляет.
    #
    # Замер: на VK Tech при лимите 3 отчёт писал «кончились раунды», а при 8
    # давал то же число и «предел читаемости» — четвёртый раунд находил 2 и
    # чинил 0. При лимите 1 и 2 «раунды» остаются правдой, и это проверяется
    # отдельно (`WORKLOG/2026-09-20-z45-baseline.md`, замер 2).
    #
    # Стоит проба один вызов чистой функции: ни PowerPoint, ни сборки. Поднять
    # лимит раундов было бы дороже — лишний запуск измерителя на каждой колоде.
    if stopped == STOP_ROUNDS and after and inspection is not None:
        probe = repair_plan(plan, inspection, measurement,
                            build_report.slides_written, library)
        if not probe.changed:
            stopped = STOP_FLOOR

    return done("ok", stopped, log, before, after, unresolved)


def _one(measurer, output: str) -> Measurement | None:
    """Замер одной колоды. Измеритель принимает список — берём первый ответ."""
    got = measurer([output])
    return got[0] if got else None


def _record(index: int, inspection: Inspection, repaired: int, rebuilt: bool) -> RoundRecord:
    return RoundRecord(
        index=index,
        status=inspection.status,
        checked_slots=inspection.checked_slots,
        defects=len(inspection.defects),
        ours=len(inspection.repairable),
        repaired=repaired,
        rebuilt=rebuilt,
    )
