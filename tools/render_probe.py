"""Разведка рендера: открыть собранный файл в PowerPoint и выгрузить слайды в PNG.

Инструмент для `Z-16`/`Z-17`, заготовка рендерера стадии VERIFY (`ADR-0013`).
COM вызывается через PowerShell из `subprocess` — `pywin32` не нужен, новых
зависимостей нет.

    python tools/render_probe.py out/deck.pptx -o out/render

Запускает настоящее приложение на рабочем столе, поэтому спрашивать согласие
пользователя обязательно (`ADR-0013`, раздел «Следствия»).
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

SCRIPT = pathlib.Path(__file__).with_name("com_probe.ps1")


def probe(deck: pathlib.Path, out_dir: pathlib.Path, width: int) -> dict[str, str]:
    """Прогнать PowerShell-зонд и вернуть его `key=value` как словарь."""
    proc = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-Deck",
            str(deck),
            "-OutDir",
            str(out_dir),
            "-TargetWidth",
            str(width),
        ],
        capture_output=True,
        text=True,
        # Без явной кодировки Python берёт локальную (на этой машине cp1251) и
        # падает на кириллице в пути. Найдено 15 сентября на шаблонах ТЗ.
        encoding="utf-8",
        errors="replace",
    )
    result: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            result[key.strip()] = value.strip()
    if proc.stderr.strip():
        result.setdefault("stderr", proc.stderr.strip().replace("\n", " / "))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("deck", type=pathlib.Path, help="путь к собранному .pptx")
    parser.add_argument(
        "-o", "--out", type=pathlib.Path, default=pathlib.Path("out/render"),
        help="каталог для PNG (по умолчанию out/render)",
    )
    parser.add_argument("--width", type=int, default=1280, help="ширина PNG в пикселях")
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        print("PowerPoint COM доступен только на Windows; см. Z-03 (LibreOffice)")
        return 2

    data = probe(args.deck, args.out, args.width)
    verdict = data.get("result", "НЕТ ОТВЕТА")

    if verdict == "ABORTED_USER_INSTANCE_RUNNING":
        print("PowerPoint уже запущен — PID:", data.get("preexisting_pids", "?"))
        print("Он одноэкземплярный: Quit() закрыл бы чужие документы.")
        print("Закрыть PowerPoint и повторить.")
        return 3

    for key in (
        "version", "opened", "slides", "png_size", "png_count",
        "app_start_s", "open_s", "export_total_s", "export_each_s",
        "exit_wait_s", "process_exited", "leftover_powerpnt",
        "fonts_used", "font_names", "error", "stderr",
    ):
        if key in data:
            print(f"{key:<20} {data[key]}")

    print(f"{'итог':<20} {verdict}")
    return 0 if verdict == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
