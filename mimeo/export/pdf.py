"""Экспорт колоды в `.pdf` — `Z-27`, `PLAN-10.0`, Ш6.

PDF рисует **тот, кто рисует слайды**: PowerPoint (Windows) — через COM из
PowerShell, как проверка вёрстки (`ADR-0013`, без `pywin32`); на прочих
системах — LibreOffice, если он установлен (`soffice --headless`). Своего
растеризатора у нас нет и не будет: PDF, собранный нами, врал бы о том, как
PowerPoint раскладывает текст. Организаторы 17 сентября: для HTML и PDF
редактируемость не нужна (`CTX-QA`).

**Чужой PowerPoint не трогаем.** Приложение одноэкземплярное: если у
пользователя открыт PowerPoint, COM подцепится к нему, и закрытие закрыло бы
его документы. Тогда — отказ со словами «закройте PowerPoint», как у
растрового зонда (`tools/render_probe.py`).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_probe.ps1")
#: PowerPoint на колоду в 13–34 МБ тратит секунды; потолок — с запасом, чтобы
#: зависшее приложение не держало сборку вечно.
TIMEOUT_SEC = 300

_PAGE = re.compile(rb"/Type\s*/Page(?![A-Za-z])")


@dataclass(frozen=True)
class PdfResult:
    output: str | None
    engine: str                 # "PowerPoint", "LibreOffice" или "" — не вышло
    pages: int | None
    problem: str | None = None  # что не так — словами; None — готово
    busy: bool = False          # PowerPoint открыт пользователем

    @property
    def ok(self) -> bool:
        return self.problem is None and self.output is not None


def count_pages(path: str) -> int | None:
    """Страниц в PDF по объектам `/Type /Page`; в сжатых потоках объектов их
    не видно — тогда `None`, а не ноль: «не смог сосчитать» ≠ «пусто»."""
    try:
        with open(path, "rb") as fh:
            found = len(_PAGE.findall(fh.read()))
    except OSError:
        return None
    return found or None


def _soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
            candidate = os.path.join(base, "LibreOffice", "program", "soffice.exe")
            if base and os.path.isfile(candidate):
                return candidate
    return None


def _powerpoint(deck: str, out: str) -> PdfResult:
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT,
             "-Deck", deck, "-Pdf", out],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return PdfResult(None, "PowerPoint", None, f"PowerPoint не уложился в {TIMEOUT_SEC} с")
    except OSError as exc:
        return PdfResult(None, "PowerPoint", None, f"PowerShell не запустился: {exc}")
    data = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            data[key.strip()] = value.strip()
    verdict = data.get("result")
    if verdict == "BUSY":
        return PdfResult(None, "PowerPoint", None,
                         "PowerPoint уже открыт — закройте его и повторите: приложение "
                         "одноэкземплярное, закрывать ваши документы мы не вправе", busy=True)
    if verdict != "OK" or not os.path.isfile(out):
        why = data.get("error") or proc.stderr.strip()[-300:] or verdict or "нет ответа"
        return PdfResult(None, "PowerPoint", None, f"PowerPoint не сохранил PDF: {why}")
    return PdfResult(out, "PowerPoint", count_pages(out))


def _libreoffice(soffice: str, deck: str, out: str) -> PdfResult:
    with tempfile.TemporaryDirectory() as tmp:
        try:
            proc = subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, deck],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            return PdfResult(None, "LibreOffice", None, f"LibreOffice не уложился в {TIMEOUT_SEC} с")
        made = os.path.join(tmp, os.path.splitext(os.path.basename(deck))[0] + ".pdf")
        if not os.path.isfile(made):
            why = (proc.stderr or proc.stdout).strip()[-300:] or "файла нет"
            return PdfResult(None, "LibreOffice", None, f"LibreOffice не сохранил PDF: {why}")
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        shutil.move(made, out)
    return PdfResult(out, "LibreOffice", count_pages(out))


def export_pdf(deck: str, out: str) -> PdfResult:
    """Колода `deck` в PDF `out`: PowerPoint на Windows, иначе LibreOffice.
    Не вышло ни так ни так — причина словами, а не исключение."""
    deck, out = os.path.abspath(deck), os.path.abspath(out)
    if not os.path.isfile(deck):
        return PdfResult(None, "", None, f"колоды нет: {deck}")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if sys.platform == "win32":
        result = _powerpoint(deck, out)
        if result.ok or result.busy:
            return result
        soffice = _soffice()
        return _libreoffice(soffice, deck, out) if soffice else result
    soffice = _soffice()
    if soffice:
        return _libreoffice(soffice, deck, out)
    return PdfResult(None, "", None,
                     "PDF рисует PowerPoint (Windows) или LibreOffice — ни того, ни другого здесь нет; "
                     "HTML собирается и без них (mimeo export --html)")
