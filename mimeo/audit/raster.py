"""Картинки готовых слайдов для зрения модели — `Z-34`, `PLAN-11.0`, шаг 1.

Рисует **PowerPoint** — тот же, чем колоду откроет эксперт, через COM из
PowerShell (`ADR-0013`, без `pywin32`), как проверка вёрстки и PDF. Своего
растеризатора нет: картинка, нарисованная нами, врала бы о том, как PowerPoint
раскладывает текст, а аудит ищет именно это.

Чужой PowerPoint не трогаем: приложение одноэкземплярное, и открытое у
пользователя — отказ «закройте PowerPoint», а не закрытие его документов.
Не Windows — отказ «картинки нет»; аудит тогда говорит, что модель не
спрошена, а не «чисто».
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raster_probe.ps1")
#: Ширина картинки слайда. Зрению модели хватает, чтобы прочесть кегль 10–12 пт
#: на слайде 13.3 дюйма; крупнее — больше токенов на картинку.
WIDTH = 1280
TIMEOUT_SEC = 300


@dataclass(frozen=True)
class Raster:
    pngs: tuple[str, ...]
    problem: str | None = None      # словами; None — нарисовано
    busy: bool = False

    @property
    def ok(self) -> bool:
        return self.problem is None and bool(self.pngs)


def render(deck: str, out_dir: str, width: int = WIDTH) -> Raster:
    """PNG каждого слайда колоды в `out_dir`, по порядку слайдов."""
    if sys.platform != "win32":
        return Raster((), "картинки слайдов рисует PowerPoint, а он есть только на Windows")
    os.makedirs(out_dir, exist_ok=True)
    for old in os.listdir(out_dir):
        # Колода могла стать короче: картинка прошлого прогона выдала бы себя
        # за нынешний слайд.
        if old.startswith("slide-") and old.endswith(".png"):
            os.remove(os.path.join(out_dir, old))
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT,
             "-Deck", os.path.abspath(deck), "-OutDir", os.path.abspath(out_dir),
             "-Width", str(width)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return Raster((), f"PowerPoint не уложился в {TIMEOUT_SEC} с")
    except OSError as exc:
        return Raster((), f"PowerShell не запустился: {exc}")
    pngs, data = [], {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key.strip() == "png":
            pngs.append(value.strip())
        else:
            data[key.strip()] = value.strip()
    verdict = data.get("result")
    if verdict == "BUSY":
        return Raster((), "PowerPoint уже открыт — закройте его и повторите: приложение "
                          "одноэкземплярное, закрывать ваши документы мы не вправе", busy=True)
    pngs = [p for p in pngs if os.path.isfile(p)]
    if verdict != "OK" or not pngs:
        why = data.get("error") or proc.stderr.strip()[-300:] or verdict or "нет ответа"
        return Raster((), f"PowerPoint не нарисовал слайды: {why}")
    return Raster(tuple(pngs))
