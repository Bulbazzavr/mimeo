"""Замо́к версий конфигов: версия не должна молча расходиться с содержимым.

Задача `Z-33`, решение `ADR-0022`, план `PLAN-7.0` Ш3.

    python tools/config_version.py            сверить замок с файлами
    python tools/config_version.py --check    то же явно
    python tools/config_version.py --write    перезаписать замок

Зачем это нужно, а не «версия в файле и хватит». Версию ставит человек, и
человек её забывает. Файл поменялся, `version` остался прежним — и оба поля
выглядят правдой: в артефакте прогона стоит `1.0`, а работал уже другой файл.
Тогда «воспроизводимый прогон» из критерия 2 ТЗ — обещание, а не факт.

`config/versions.lock.json` помнит, **каким был файл, когда версию объявляли**.
Расхождение между замком и файлом — это и есть забытая версия, и `--check`
на нём падает. Проверка, которая не может провалиться, ничего не стоит.

`--write` отказывается записать изменившийся файл под прежней версией: иначе
замок открывался бы тем же ключом, который должен запирать.

Заказчику это нужно не ради порядка. Сессия 17 сентября (`CTX-QA`): у них агенты
со скиллами в общем доступе, правка скилла одним человеком меняет генерации у
всех, и нужна **прослеживаемость** — что поменялось и как сказалось на
результате.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mimeo import config as cfg  # noqa: E402

LOCK_NAME = "versions.lock.json"

_NOTE = (
    "Замок версий конфигов: каким был каждый файл, когда объявляли его version. "
    "Пишется tools/config_version.py --write, сверяется --check и тестом "
    "tests/test_config_version.py. Расхождение sha256 при неизменной version "
    "означает забытую версию — см. ADR-0022 и PLAN-7.0."
)


def lock_path() -> str:
    return os.path.join(cfg.config_root(), LOCK_NAME)


def read_lock(path: str | None = None) -> dict:
    """Замок как словарь `имя → {version, sha256}`. Нет файла — пустой замок:
    отличать «замка нет» от «замок пуст» здесь не нужно, обе ситуации чинятся
    одной командой `--write`, и сообщение об этом одинаковое."""
    try:
        with open(path or lock_path(), encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    entries = raw.get("configs") if isinstance(raw, dict) else None
    return entries if isinstance(entries, dict) else {}


def compare(lock: dict, stamps: tuple[cfg.ConfigStamp, ...]) -> list[str]:
    """Что не сходится. Пустой список — сошлось всё.

    Каждая строка написана так, чтобы по ней было видно, **что сделать**:
    сообщение об ошибке, после которого надо гадать, стоит столько же, сколько
    его отсутствие.
    """
    problems: list[str] = []
    seen = {s.name for s in stamps}

    for extra in sorted(set(lock) - seen):
        problems.append(
            f"{extra}: есть в замке, но движок такого конфига не читает. "
            f"Убрать из замка (--write) или вернуть в mimeo/config.py KNOWN"
        )

    for s in stamps:
        entry = lock.get(s.name)
        if not s.loaded:
            problems.append(
                f"{s.name}: файла нет по пути {s.source}. Движок работает на "
                f"встроенных значениях; замок про это не знает"
            )
            continue
        if not s.version:
            problems.append(
                f"{s.name}: не объявлена version. Добавить поле \"version\" "
                f"в файл и обновить замок (--write)"
            )
            continue
        if not isinstance(entry, dict):
            problems.append(f"{s.name}: нет в замке. Обновить замок (--write)")
            continue

        was_version = entry.get("version")
        was_sha = entry.get("sha256")
        if was_sha == s.sha256 and was_version == s.version:
            continue
        if was_sha != s.sha256 and was_version == s.version:
            problems.append(
                f"{s.name}: файл изменился, а version остался {s.version}. "
                f"Поднять version в файле, потом обновить замок (--write). "
                f"Было {str(was_sha)[:12]}…, стало {s.sha256[:12]}…"
            )
        else:
            problems.append(
                f"{s.name}: version {was_version} → {s.version}, "
                f"замок не обновлён. Выполнить --write"
            )
    return problems


def forgotten(lock: dict, stamps: tuple[cfg.ConfigStamp, ...]) -> list[str]:
    """Только забытые версии: файл другой, номер прежний. `--write` их не пишет."""
    out = []
    for s in stamps:
        entry = lock.get(s.name)
        if not isinstance(entry, dict) or not s.loaded:
            continue
        if entry.get("sha256") != s.sha256 and entry.get("version") == s.version:
            out.append(s.name)
    return out


def write_lock(stamps: tuple[cfg.ConfigStamp, ...], path: str | None = None) -> None:
    body = {
        "_": _NOTE,
        "configs": {s.name: {"version": s.version, "sha256": s.sha256} for s in stamps if s.loaded},
    }
    text = json.dumps(body, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    with open(path or lock_path(), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Замок версий конфигов")
    ap.add_argument("--write", action="store_true", help="перезаписать замок по файлам")
    ap.add_argument("--check", action="store_true", help="сверить замок с файлами (по умолчанию)")
    args = ap.parse_args(argv)

    stamps = cfg.stamps()
    lock = read_lock()

    if args.write:
        stuck = forgotten(lock, stamps)
        if stuck:
            print("Замок не обновлён: сначала поднимите version.", file=sys.stderr)
            for name in stuck:
                print(f"  {name}: содержимое изменилось, version прежний", file=sys.stderr)
            return 1
        write_lock(stamps)
        print(f"Замок записан: {lock_path()}")
        for s in stamps:
            if s.loaded:
                print(f"  {s.name:16} {s.version:8} {s.sha256[:12]}…")
        return 0

    problems = compare(lock, stamps)
    if problems:
        print("Замок и конфиги разошлись:", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"Сошлось: {len(stamps)} конфига, замок совпадает с файлами.")
    for s in stamps:
        print(f"  {s.name:16} {s.version:8} {s.sha256[:12]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
