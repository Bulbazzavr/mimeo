"""Установка одним файлом и подъём серверов моделей — `Z-76`.

Курс пользователя 26 сентября (вечер): эксперт ставит наш код, запускает один
файл — и программа сама скачивает и ставит всё нужное: сервер языковой модели
(`llama.cpp`), веса Gemma, генератор картинок (`stable-diffusion.cpp`,
Z-Image-Turbo с кодировщиком и VAE), поднимает серверы и открывает веб. Файл —
`start.bat`; он зовёт этот инструмент.

    python tools/runtime.py install     докачать и сверить всё из config/runtime.json
    python tools/runtime.py start       поднять llama-server и sd-server, дождаться готовности
    python tools/runtime.py stop        остановить то, что поднял start
    python tools/runtime.py status      что лежит и что отвечает

Всё ложится в `runtime/` внутри репозитория (вне git). Файл сверяется по
`sha256` после скачивания; уже лежащий файл нужного размера не качается заново
(`--verify` — пересчитать суммы и у лежащих). Оборванная загрузка продолжается
с места обрыва. Стандартная библиотека, как весь продукт (`ADR-0001`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "config", "runtime.json")
CHUNK = 1 << 20


def load(path: str = MANIFEST) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def runtime_dir(manifest: dict) -> str:
    return os.path.join(ROOT, manifest.get("root") or "runtime")


def _gb(n: float) -> str:
    return f"{n / 1e9:.2f} ГБ" if n >= 1e9 else f"{n / 1e6:.0f} МБ"


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: str, size: int, sha256: str) -> None:
    """Качает `url` в `target` через `target.part`, продолжая оборванное, и
    сверяет размер и сумму. Не сошлось — файл удаляется, и это ошибка."""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    part = target + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have > size:
        os.remove(part)
        have = 0
    request = urllib.request.Request(url, headers={"User-Agent": "mimeo-runtime"})
    if have:
        request.add_header("Range", f"bytes={have}-")
    started, shown, done = time.monotonic(), 0.0, have
    with urllib.request.urlopen(request, timeout=60) as response:
        if have and response.status != 206:          # сервер начал сначала
            have = done = 0
        with open(part, "ab" if have else "wb") as out:
            while True:
                block = response.read(CHUNK)
                if not block:
                    break
                out.write(block)
                done += len(block)
                now = time.monotonic()
                if now - shown >= 2:
                    speed = (done - have) / max(now - started, 1e-6)
                    print(f"    {_gb(done)} из {_gb(size)}, {speed / 1e6:.0f} МБ/с", flush=True)
                    shown = now
    if os.path.getsize(part) != size:
        raise RuntimeError(f"размер {os.path.getsize(part)} вместо {size} — загрузка не завершена")
    got = sha256_of(part)
    if got != sha256:
        os.remove(part)
        raise RuntimeError(f"sha256 не сошёлся: {got} вместо {sha256} — файл удалён")
    os.replace(part, target)


def install(manifest: dict, verify: bool = False) -> int:
    """Всё из манифеста — на место. Возвращает код выхода."""
    root = runtime_dir(manifest)
    os.makedirs(root, exist_ok=True)
    for item in manifest["items"]:
        name = item["name"]
        if "path" in item:
            target = os.path.join(root, item["path"])
            if os.path.isfile(target) and os.path.getsize(target) == item["size"]:
                if verify and sha256_of(target) != item["sha256"]:
                    print(f"  ✗ {name}: sha256 не сошёлся, качаю заново", flush=True)
                    os.remove(target)
                else:
                    print(f"  ✓ {name}", flush=True)
                    continue
            print(f"  ↓ {name} — {_gb(item['size'])}", flush=True)
            download(item["url"], target, item["size"], item["sha256"])
            print(f"  ✓ {name}", flush=True)
            continue
        folder = os.path.join(root, item["unzip_to"])
        check = os.path.join(root, item["check"])
        if not os.path.isfile(check):
            archive = os.path.join(root, "downloads", os.path.basename(item["url"]))
            print(f"  ↓ {name} — {_gb(item['size'])}", flush=True)
            if not (os.path.isfile(archive) and os.path.getsize(archive) == item["size"]
                    and sha256_of(archive) == item["sha256"]):
                download(item["url"], archive, item["size"], item["sha256"])
            with zipfile.ZipFile(archive) as z:
                z.extractall(folder)
            os.remove(archive)
            if not os.path.isfile(check):
                raise RuntimeError(f"после распаковки нет {item['check']}")
        for rel in item.get("copy_from", ()):
            source, target = os.path.join(root, rel), os.path.join(folder, os.path.basename(rel))
            if not os.path.isfile(target):
                shutil.copyfile(source, target)
        print(f"  ✓ {name}", flush=True)
    return 0


def _local(url: str):
    """Свои серверы — мимо прокси из окружения (так же, как клиент модели)."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ready(url: str) -> bool:
    try:
        with _local(url).open(url, timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _pids_path(manifest: dict) -> str:
    return os.path.join(runtime_dir(manifest), "pids.json")


def start(manifest: dict) -> int:
    root = runtime_dir(manifest)
    logs = os.path.join(root, "logs")
    os.makedirs(logs, exist_ok=True)
    pids = {}
    for server in manifest["servers"]:
        name = server["name"]
        if ready(server["ready"]):
            print(f"  ✓ {name}: уже работает", flush=True)
            continue
        exe = os.path.join(root, server["exe"])
        if not os.path.isfile(exe):
            print(f"  ✗ {name}: нет {server['exe']} — сначала install", flush=True)
            return 1
        args = [a.replace("{root}", root) for a in server["args"]]
        log = open(os.path.join(logs, os.path.basename(server["exe"]) + ".log"), "w", encoding="utf-8")
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen([exe, *args], cwd=os.path.dirname(exe), stdout=log, stderr=log,
                                stdin=subprocess.DEVNULL, creationflags=flags)
        pids[name] = proc.pid
        print(f"  … {name}: поднимается (pid {proc.pid})", flush=True)
        deadline = time.monotonic() + manifest.get("ready_timeout_sec", 180)
        while not ready(server["ready"]):
            if proc.poll() is not None:
                print(f"  ✗ {name}: вышел с кодом {proc.returncode}, журнал — {log.name}", flush=True)
                return 1
            if time.monotonic() > deadline:
                print(f"  ✗ {name}: не ответил за {manifest.get('ready_timeout_sec', 180)} с, "
                      f"журнал — {log.name}", flush=True)
                return 1
            time.sleep(1)
        print(f"  ✓ {name}: готов", flush=True)
    if pids:
        known = {}
        if os.path.isfile(_pids_path(manifest)):
            with open(_pids_path(manifest), encoding="utf-8") as fh:
                known = json.load(fh)
        known.update(pids)
        with open(_pids_path(manifest), "w", encoding="utf-8") as fh:
            json.dump(known, fh, ensure_ascii=False, indent=2)
    return 0


def stop(manifest: dict) -> int:
    path = _pids_path(manifest)
    if not os.path.isfile(path):
        print("  нечего останавливать: start ничего не поднимал", flush=True)
        return 0
    with open(path, encoding="utf-8") as fh:
        pids = json.load(fh)
    for name, pid in pids.items():
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        print(f"  ■ {name}: остановлен (pid {pid})", flush=True)
    os.remove(path)
    return 0


def status(manifest: dict) -> int:
    root = runtime_dir(manifest)
    for item in manifest["items"]:
        where = os.path.join(root, item.get("path") or item["check"])
        mark = "✓" if os.path.isfile(where) else "✗"
        print(f"  {mark} {item['name']}", flush=True)
    for server in manifest["servers"]:
        print(f"  {'✓' if ready(server['ready']) else '·'} {server['name']}: "
              f"{'отвечает' if ready(server['ready']) else 'не отвечает'}", flush=True)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="установка и серверы моделей mimeo (Z-76)")
    parser.add_argument("command", choices=("install", "start", "stop", "status"))
    parser.add_argument("--verify", action="store_true", help="install: пересчитать sha256 и у лежащих файлов")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        # Вывод в файл или трубу идёт в кодировке системы (cp1251), а в ней нет «✓».
        sys.stdout.reconfigure(errors="replace")
    manifest = load()
    try:
        if args.command == "install":
            print("Установка: всё нужное — в runtime/", flush=True)
            return install(manifest, verify=args.verify)
        if args.command == "start":
            print("Серверы моделей:", flush=True)
            return start(manifest)
        if args.command == "stop":
            return stop(manifest)
        return status(manifest)
    except (OSError, RuntimeError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        print(f"  ✗ ошибка: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
