"""Измеритель вёрстки: габариты текста от самого движка. Шаг 1 плана `PLAN-4.0`.

Модуль называется `metrics`, а не `measure`, как стояло в плане: функция
`measure` затеняла бы одноимённый модуль после реэкспорта в `__init__`, и
`mimeo.verify.measure` означало бы то функцию, то модуль.

Стадия VERIFY не разбирает растр. Она спрашивает у PowerPoint то, что он и так
посчитал: насколько высок набранный текст и насколько высок бокс, в котором он
лежит. Переполнение после этого — арифметика, без порогов на яркость и без
зависимости от разрешения (`WORKLOG/2026-09-12-overflow-truth.md`).

COM вызывается через PowerShell из `subprocess` — `pywin32` не нужен, новых
зависимостей нет (`ADR-0013`).

**Измеритель сменный.** `measure` здесь — реализация на PowerPoint. Когда
появится LibreOffice (`Z-03`), он даст ту же `Measurement`, и стадия не заметит
разницы. Нет ни одного — VERIFY выключается, а пайплайн собирает файл как
раньше.

Запуск поднимает настоящее приложение на рабочем столе, поэтому делается с
согласия пользователя.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

PROBE = pathlib.Path(__file__).with_name("powerpoint_probe.ps1")

#: Запас против округления: габариты приходят в пунктах с двумя знаками, и
#: равенство «впритык» не должно считаться переполнением.
TOLERANCE = 1.02


@dataclass(frozen=True)
class ShapeMetric:
    """Одна текстовая фигура собранного слайда, как её видит движок вёрстки."""

    slide: int          # номер слайда, с единицы — как в COM
    shape_id: str       # `p:cNvPr/@id`, тот же якорь, что у слота
    width: float        # размеры бокса, пункты
    height: float
    text_width: float   # габариты набранного текста
    text_height: float
    margin_left: float
    margin_right: float
    margin_top: float
    margin_bottom: float
    autofit: int        # 0 нет, 1 бокс под текст, 2 текст под бокс
    chars: int
    #: Левый верхний угол фигуры, пункты. Нужен, чтобы понять, что лежит вокруг:
    #: дефект вёрстки — не «текст выше бокса», а «тексту некуда деться»
    #: (`PLAN-4.2`, `ADR-0015`).
    left: float = 0.0
    top: float = 0.0
    #: Вертикальный якорь: 1 верх, 2 центр, 3 низ, 4 по ширине, 5 распределён.
    #: Задаёт, КУДА растёт непоместившийся текст, а значит где искать место.
    anchor: int = 1
    #: Видимый кегль в пунктах — уже с учётом `normAutofit`, а не номинал.
    #: 0 или -2 означает «неизвестен»: в фигуре разные размеры.
    font_size: float = 0.0

    @property
    def usable_height(self) -> float:
        return max(1.0, self.height - self.margin_top - self.margin_bottom)

    @property
    def usable_width(self) -> float:
        return max(1.0, self.width - self.margin_left - self.margin_right)

    @property
    def height_ratio(self) -> float:
        """Во сколько раз текст выше отведённого места. <= 1 — влезает."""
        return self.text_height / self.usable_height

    @property
    def width_ratio(self) -> float:
        return self.text_width / self.usable_width

    def overflows(self, tolerance: float = TOLERANCE) -> bool:
        """Переполнение по высоте. Ширина измеряется, но пока не чинится."""
        return self.height_ratio > tolerance


@dataclass(frozen=True)
class Box:
    """Прямоугольник одной фигуры слайда, пункты.

    Нужен не сам по себе, а как **помеха**: свободное место рядом с текстом
    занимает что угодно, а текст есть лишь у 143 фигур корпуса из 2209
    (`WORKLOG/2026-09-13-autofit.md`).
    """

    slide: int
    shape_id: str
    x: float
    y: float
    w: float
    h: float
    visible: bool = True

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h


@dataclass(frozen=True)
class Measurement:
    """Результат по одной колоде. `status` — не украшение: файл может не
    открыться вовсе, и это надо увидеть, а не принять за «ноль дефектов»."""

    deck: str
    status: str                                   # ok | open_failed | walk_failed | not_measured
    shapes: tuple[ShapeMetric, ...] = ()
    slides: int = 0
    note: str = ""
    skipped: tuple[str, ...] = field(default_factory=tuple)
    #: Прямоугольники всех фигур, включая нетекстовые. Пусто у старого зонда —
    #: тогда детектор откатывается к прежней мерке, а не снимает дефекты молча.
    boxes: tuple[Box, ...] = field(default_factory=tuple)
    #: Габариты слайда, пункты. Край слайда — такая же граница, как соседняя
    #: фигура. `None`, когда зонд их не отдал.
    page: tuple[float, float] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class MeasurerUnavailable(RuntimeError):
    """Мерить нечем: не Windows, нет PowerShell или нет PowerPoint."""


def available() -> bool:
    """Можно ли мерить здесь. Дешёвая проверка, приложение не запускается."""
    if sys.platform != "win32":
        return False
    return PROBE.exists() and bool(_powershell())


def _powershell() -> str | None:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = pathlib.Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if candidate.exists():
        return str(candidate)
    return "powershell.exe" if os.environ.get("PATH") else None


def _num(raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _parse(line: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for token in line.split():
        key, sep, value = token.partition("=")
        if sep:
            out[key] = value
    return out


def measure(decks: list[str], timeout: float = 300.0) -> tuple[Measurement, ...]:
    """Габариты текста для каждой колоды. Порядок ответа повторяет порядок входа.

    Все колоды меряются в **одном** сеансе приложения. Отдельный запуск на файл
    давал гонку: предыдущий экземпляр ещё не вышел, зонд отказывался работать, а
    наверх приходил пустой ответ, неотличимый от «дефектов нет».
    """
    if not decks:
        return ()
    if not available():
        raise MeasurerUnavailable(
            "Измерить вёрстку нечем: нужен Windows с установленным PowerPoint. "
            "Стадия VERIFY пропускается, сборка от этого не страдает."
        )

    paths = [str(pathlib.Path(d).resolve()) for d in decks]
    handle, list_path = tempfile.mkstemp(suffix=".txt", prefix="mimeo-verify-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write("\n".join(paths))
        proc = subprocess.run(
            [
                _powershell() or "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(PROBE),
                "-ListFile",
                list_path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass

    return _collect(proc.stdout, paths)


def _collect(stdout: str, paths: list[str]) -> tuple[Measurement, ...]:
    """Разбор вывода зонда. Колоды обозначены номером, а не именем: имя бывает
    нелатинским, а вывод в трубу идёт в кодировке консоли."""
    status: dict[int, str] = {}
    notes: dict[int, str] = {}
    slides: dict[int, int] = {}
    shapes: dict[int, list[ShapeMetric]] = {}
    boxes: dict[int, list[Box]] = {}
    pages: dict[int, tuple[float, float]] = {}
    skipped: dict[int, list[str]] = {}
    current = -1
    fatal = ""

    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("deck="):
            current = int(line[5:] or -1)
            shapes.setdefault(current, [])
        elif line.startswith("status="):
            status[current] = line[7:]
        elif line.startswith("slides="):
            slides[current] = int(line[7:] or 0)
        elif line.startswith("note="):
            notes[current] = line[5:]
        elif line.startswith("skipped="):
            skipped.setdefault(current, []).append(line[8:])
        elif line.startswith("result=") and line[7:] != "OK":
            fatal = line[7:]
        elif line == "exit=stuck":
            # Приложение не удалось завершить. Следующий замер упрётся в BUSY,
            # и молчать об этом нельзя: пустой ответ неотличим от «дефектов нет».
            fatal = "PowerPoint не завершился; следующий замер может не пройти"
        elif line.startswith("shape "):
            d = _parse(line[6:])
            shapes.setdefault(current, []).append(
                ShapeMetric(
                    slide=int(d.get("slide", "0") or 0),
                    shape_id=d.get("id", ""),
                    width=_num(d.get("w", "0")),
                    height=_num(d.get("h", "0")),
                    text_width=_num(d.get("tw", "0")),
                    text_height=_num(d.get("th", "0")),
                    margin_left=_num(d.get("ml", "0")),
                    margin_right=_num(d.get("mr", "0")),
                    margin_top=_num(d.get("mt", "0")),
                    margin_bottom=_num(d.get("mb", "0")),
                    autofit=int(d.get("fit", "0") or 0),
                    chars=int(d.get("len", "0") or 0),
                    font_size=_num(d.get("sz", "0")),
                    left=_num(d.get("x", "0")),
                    top=_num(d.get("y", "0")),
                    anchor=int(d.get("anchor", "1") or 1),
                )
            )
        elif line.startswith("box "):
            d = _parse(line[4:])
            boxes.setdefault(current, []).append(
                Box(
                    slide=int(d.get("slide", "0") or 0),
                    shape_id=d.get("id", ""),
                    x=_num(d.get("x", "0")),
                    y=_num(d.get("y", "0")),
                    w=_num(d.get("w", "0")),
                    h=_num(d.get("h", "0")),
                    visible=d.get("vis", "1") != "0",
                )
            )
        elif line.startswith("page "):
            d = _parse(line[5:])
            pages[current] = (_num(d.get("w", "0")), _num(d.get("h", "0")))

    out = []
    for n, path in enumerate(paths):
        found = shapes.get(n, [])
        found_boxes = sorted(boxes.get(n, []), key=lambda b: (b.slide, b.shape_id))
        # Порядок обхода фигур в COM идёт по стеку и воспроизводимым не является.
        # Сортируем явно: детерминированность обязательна на всех стадиях.
        found.sort(key=lambda m: (m.slide, m.shape_id))
        out.append(
            Measurement(
                deck=path,
                status=status.get(n, "not_measured"),
                shapes=tuple(found),
                slides=slides.get(n, 0),
                note=notes.get(n) or fatal,
                skipped=tuple(skipped.get(n, [])),
                boxes=tuple(found_boxes),
                page=pages.get(n),
            )
        )
    return tuple(out)
