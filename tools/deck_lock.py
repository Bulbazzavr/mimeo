"""Замок Ш0 (`PLAN-9.0`): путь без модели даёт те же байты, что после части Б.

Задача `Z-57`, шаг Ш0. Модель встаёт в стадию PLAN, и план обещает: без модели
и при промахе кэша — **те же колоды, байт в байт** (приёмка 1). Замок помнит
sha256 колод, собранных кодом части Б (`Z-58`), и сверяет с ними новую сборку.

    python tools/deck_lock.py --check                  93 колоды, PowerPoint не нужен
    python tools/deck_lock.py --check --verify         23 колоды через PowerPoint, эта машина
    python tools/deck_lock.py --check -- --llm cache   промах кэша: обязано совпасть тоже
    python tools/deck_lock.py --write [--verify]       переснять замок — только осознанно

**С Ш2 команда сама передаёт `--llm off`**: умолчание сборки — звать модель
(`on`, с 26 сентября; `ADR-0021`), и без явного `off` голое `--check` собрало
бы колоды модели и показало бы «разошлись» при целом пути без модели. Хвост после `--`
стоит **после** `off` и перекрывает его: `argparse` берёт последнее значение.

**Собирает той же командой, что запускает эксперт** — `python -m mimeo build`,
а не внутренними функциями: иначе замок мерил бы не то, что сдаётся.

Слоёв два. **`compose`** — сборка без `--verify`, PowerPoint не нужен (на
другой машине замок не проверялся: переносимость — ожидание, а не факт): девятка сдачи
(основной текст, три выданных шаблона, `--variants 3 --slides 10-15`) и все
шесть входов корпуса на всех 14 шаблонах по колоде — 93 колоды. **`verify`** —
с `--verify`, только на машине с PowerPoint: девятка и основной текст на 14
шаблонах — 23 колоды; это ровно то, что меряет `tools/report.py verify`.

Код возврата: **0** — те же байты; **1** — разошлись, колоды названы; **2** —
проверить не смог: нет шаблонов, сборка упала, замка нет. «Не смог» и «чисто»
различаются намеренно: проверка, вернувшая пустоту, не должна читаться как
«совпало» (`CLAUDE.md`). **Код 2 у самой сборки при записанной колоде — не
отказ:** так `mimeo build` сообщает о структурной проблеме в колоде, и он тоже
хранится в замке и сверяется (`Z-60`: висячая связь на `svetlaja`).

План колоды в замок **не входит**: он несёт паспорт конфигов
(`source.configs`), и правка промпта в Ш1 сменила бы его, не тронув колоду.
Сама колода паспорта не несёт — замерено в `Z-58`: 9 колод корпуса совпали
байт в байт, когда в паспорт добавился новый конфиг.
"""

from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = os.path.join(ROOT, "tools", "deck_lock.json")
MAIN = "examples/content-mimeo.md"
NINE = ["--variants", "3", "--slides", "10-15"]


def _rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def jobs(layer: str) -> list[tuple[str, str, str, list[str]]]:
    """(ключ, шаблон, текст, флаги) — что собирать. Пути — от корня репозитория."""
    tz = sorted(glob.glob(os.path.join(ROOT, "tz", "templates", "*.pptx")))
    samples = sorted(glob.glob(os.path.join(ROOT, "samples", "*.pptx")))
    out = [(f"nine|{os.path.basename(t)}", _rel(t), MAIN, NINE) for t in tz]
    contents = [MAIN] if layer == "verify" else sorted(
        _rel(p) for p in glob.glob(os.path.join(ROOT, "examples", "content-*.md")))
    for c in contents:
        for t in samples + tz:
            out.append((f"single|{os.path.basename(c)}|{os.path.basename(t)}", _rel(t), c, []))
    return out


def command(template: str, content: str, flags: list[str], extra: list[str],
            here: str, verify: bool) -> list[str]:
    """Команда эксперта для одного задания. `--llm off` — между флагами задания и
    хвостом: в замок пишутся только флаги задания, и он от этого не меняется."""
    stem = os.path.splitext(os.path.basename(template))[0]
    cmd = [sys.executable, "-m", "mimeo", "build", template, content, *flags,
           "--llm", "off", *extra,
           "--output", os.path.join(here, stem + ".pptx"), "-o", os.path.join(here, "art"), "-q"]
    if verify:
        cmd.append("--verify")
    return cmd


def build(job, verify: bool, extra: list[str], workdir: str) -> dict:
    """Собрать одно задание командой эксперта и вернуть хэши получившихся колод."""
    key, template, content, flags = job
    here = os.path.join(workdir, hashlib.sha256(key.encode("utf-8")).hexdigest()[:12])
    os.makedirs(here, exist_ok=True)
    stem = os.path.splitext(os.path.basename(template))[0]
    cmd = command(template, content, flags, extra, here, verify)
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    decks = sorted(glob.glob(os.path.join(here, stem + "*.pptx")))
    if not decks:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
        return {"error": f"сборка не вышла (код {proc.returncode}): {' '.join(tail)}"}
    # Код 2 при записанной колоде — не отказ сборки, а найденная ею
    # структурная проблема (`cli.py`). Колода — факт, и код — тоже: оба в замок.
    return {
        "template_sha256": _sha(os.path.join(ROOT, template)),
        "content_sha256": _sha(os.path.join(ROOT, content)),
        "flags": flags,
        "exit": proc.returncode,
        "files": {os.path.basename(p): _sha(p) for p in decks},
    }


def run(layer: str, extra: list[str], workers: int) -> dict[str, dict]:
    todo = jobs(layer)
    with tempfile.TemporaryDirectory(prefix="deck_lock_") as workdir:
        verify = layer == "verify"
        # PowerPoint — одно приложение на машину: слой verify только по одному.
        with ThreadPoolExecutor(max_workers=1 if verify else workers) as pool:
            results = list(pool.map(lambda j: build(j, verify, extra, workdir), todo))
    return {job[0]: res for job, res in zip(todo, results)}


def _code() -> str:
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8").stdout.strip()
    head = git("rev-parse", "--short", "HEAD")
    dirty = git("status", "--porcelain", "--", "mimeo", "config")
    return head + (" + правки в рабочем дереве" if dirty else "")


def write(layer: str, got: dict[str, dict]) -> int:
    broken = {k: v["error"] for k, v in got.items() if "error" in v}
    if broken or not got:
        for k, e in sorted(broken.items()):
            print(f"  {k}: {e}")
        print("Замок не записан: " + ("шаблонов нет" if not got else f"не собралось {len(broken)}"))
        return 2
    lock = {}
    if os.path.exists(LOCK):
        with open(LOCK, encoding="utf-8") as fh:
            lock = json.load(fh)
    lock["_"] = ("Замок Ш0 (PLAN-9.0, Z-57): sha256 колод пути без модели. Пишется и "
                 "сверяется tools/deck_lock.py. Слой compose не требует PowerPoint (на другой "
                 "машине не проверялся), слой verify — только машина, где его снимали.")
    lock.setdefault("layers", {})[layer] = {
        "code": _code(),
        "written": datetime.date.today().isoformat(),
        "decks": {k: got[k] for k in sorted(got)},
    }
    with open(LOCK, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(lock, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    files = sum(len(v["files"]) for v in got.values())
    flagged = sorted(k for k, v in got.items() if v["exit"])
    print(f"Замок слоя {layer} записан: заданий {len(got)}, колод {files}, код {_code()}.")
    for k in flagged:
        print(f"  сборка сообщила о структурной проблеме (код {got[k]['exit']}): {k}")
    return 0


def load(layer: str) -> dict | None:
    try:
        with open(LOCK, encoding="utf-8") as fh:
            return json.load(fh)["layers"][layer]
    except (OSError, ValueError, KeyError):
        return None


def check(layer: str, got: dict[str, dict]) -> int:
    want = load(layer)
    if want is None:
        print(f"Замка слоя {layer} нет: сверять не с чем. Снять — --write.")
        return 2
    same = differ = cannot = 0
    lines = []
    for key, ref in sorted(want["decks"].items()):
        res = got.get(key)
        if res is None:
            cannot += len(ref["files"])
            lines.append(f"  не смог: {key} — шаблона нет")
            continue
        if "error" in res:
            cannot += len(ref["files"])
            lines.append(f"  не смог: {key} — {res['error']}")
            continue
        if res["template_sha256"] != ref["template_sha256"] or res["content_sha256"] != ref["content_sha256"]:
            cannot += len(ref["files"])
            lines.append(f"  не смог: {key} — шаблон или текст не те, что при записи замка")
            continue
        if res["exit"] != ref["exit"]:
            differ += 1
            lines.append(f"  разошлась: {key} — код сборки {ref['exit']} → {res['exit']}")
        for name in sorted(set(ref["files"]) | set(res["files"])):
            if ref["files"].get(name) == res["files"].get(name):
                same += 1
            else:
                differ += 1
                lines.append(f"  разошлась: {key} → {name}")
    for key in sorted(set(got) - set(want["decks"])):
        lines.append(f"  нет в замке: {key}")
    print(f"Слой {layer}, замок снят кодом {want['code']} ({want['written']}): "
          f"те же байты {same}, разошлись {differ}, проверить не смог {cannot}.")
    for ln in lines:
        print(ln)
    if differ:
        return 1
    return 2 if cannot or not same else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = argv[argv.index("--") + 1:] if "--" in argv else []
    own = argv[:argv.index("--")] if "--" in argv else argv
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="собрать и сверить с замком")
    mode.add_argument("--write", action="store_true", help="собрать и записать замок")
    parser.add_argument("--verify", action="store_true",
                        help="слой verify: сборка с --verify, нужен PowerPoint")
    parser.add_argument("--workers", type=int, default=4,
                        help="сборок параллельно в слое compose (verify — всегда одна)")
    args = parser.parse_args(own)
    layer = "verify" if args.verify else "compose"
    if args.check and load(layer) is None:
        # Раньше сборки: без замка сверять не с чем, и минуты PowerPoint незачем.
        return check(layer, {})
    got = run(layer, extra, args.workers)
    return write(layer, got) if args.write else check(layer, got)


if __name__ == "__main__":
    sys.exit(main())
