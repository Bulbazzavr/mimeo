"""Ш1: скачать настоящие шаблоны и ПРОВЕРИТЬ каждый файл.

Проверка обязательна: сайты шаблонов регулярно отдают HTML-заглушку вместо
файла, и без неё в samples/ окажется мусор, на котором будет отлаживаться Ш2.
"""

from __future__ import annotations

import io
import os
import re
import sys
import urllib.parse
import urllib.request
import zipfile

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
SLD_ID = re.compile(rb"<p:sldId\b")
PPTX_HREF = re.compile(r'href=["\']([^"\']+\.pptx)["\']', re.I)


def get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def inspect(blob: bytes) -> dict | None:
    """Возвращает характеристики, если это настоящий PPTX, иначе None."""
    if not blob[:2] == b"PK":
        return None
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
        names = set(zf.namelist())
        if "ppt/presentation.xml" not in names:
            return None
        pres = zf.read("ppt/presentation.xml")
        slides = len(SLD_ID.findall(pres))
        layouts = sum(1 for n in names if n.startswith("ppt/slideLayouts/slideLayout"))
        masters = sum(1 for n in names if n.startswith("ppt/slideMasters/slideMaster"))
        media = sum(1 for n in names if n.startswith("ppt/media/"))
        return {
            "slides": slides,
            "layouts": layouts,
            "masters": masters,
            "media": media,
            "parts": len(names),
            "bytes": len(blob),
        }
    except zipfile.BadZipFile:
        return None


def links_on(page: str, limit: int = 40) -> list[str]:
    try:
        html = get(page).decode("utf-8", "replace")
    except Exception as exc:                      # noqa: BLE001 - разведка
        print(f"  ! {page} -> {type(exc).__name__}: {exc}")
        return []
    out, seen = [], set()
    for href in PPTX_HREF.findall(html):
        full = urllib.parse.urljoin(page, href)
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out[:limit]


def fetch(url: str, dest_dir: str, label: str, min_slides: int) -> dict | None:
    name = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(url).path))
    if not name.lower().endswith(".pptx"):
        name += ".pptx"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80]
    try:
        blob = get(url)
    except Exception as exc:                      # noqa: BLE001
        print(f"  ! {name}: {type(exc).__name__}")
        return None

    info = inspect(blob)
    if info is None:
        print(f"  x {name}: не PPTX ({len(blob)} б, начало {blob[:12]!r})")
        return None
    if info["slides"] < min_slides:
        print(f"  - {name}: слайдов {info['slides']} < {min_slides}, пропускаю")
        return None

    path = os.path.join(dest_dir, name)
    with open(path, "wb") as fh:
        fh.write(blob)
    print(f"  + {name}: {info['slides']} сл., {info['layouts']} мак., "
          f"{info['media']} медиа, {info['bytes'] // 1024} КБ")
    return {"file": name, "url": url, "label": label, **info}


def main() -> int:
    dest = sys.argv[1] if len(sys.argv) > 1 else "samples"
    os.makedirs(dest, exist_ok=True)
    collected: list[dict] = []

    # 1. Реальные «грязные» файлы из тестового корпуса Apache POI — проверка на
    #    устойчивость: старые версии, странные генераторы, битые конструкции.
    print("[POI test-data] грязь реального мира")
    poi = ("https://raw.githubusercontent.com/apache/poi/trunk/test-data/slideshow/")
    for name in ("2411-Performance_Up.pptx", "SampleShow.pptx", "SmartArt.pptx",
                 "WithMaster.pptx", "ArtisticEffectSample.pptx", "60042.pptx"):
        got = fetch(poi + name, dest, "apache-poi-test-data", min_slides=2)
        if got:
            collected.append(got)

    # 2. Страницы с корпоративными шаблонами: вытаскиваем прямые ссылки.
    pages = [
        ("presentation-creation.ru", "https://presentation-creation.ru/powerpoint-templates/biznes.html"),
        ("petr-panda", "https://templates.petr-panda.ru/20shablonov-prezentacij-dlja-biznesa/"),
        ("templateswise", "https://templateswise.com/business-powerpoint-templates.html"),
    ]
    for label, page in pages:
        print(f"[{label}] {page}")
        found = links_on(page)
        print(f"  найдено ссылок на .pptx: {len(found)}")
        for url in found[:6]:
            got = fetch(url, dest, label, min_slides=4)
            if got:
                collected.append(got)

    print(f"\nИтого пригодных файлов: {len(collected)}")
    for c in sorted(collected, key=lambda c: -c["slides"]):
        print(f"  {c['slides']:>3} сл.  {c['file']}")

    with open(os.path.join(dest, "MANIFEST.md"), "w", encoding="utf-8") as fh:
        fh.write("# Источники образцов\n\n")
        fh.write("Скачано автоматически, в репозиторий не коммитится (см. .gitignore).\n")
        fh.write("Используется только для локальной проверки анализатора.\n\n")
        fh.write("| Файл | Слайдов | Макетов | Медиа | КБ | Источник |\n")
        fh.write("|---|---|---|---|---|---|\n")
        for c in sorted(collected, key=lambda c: -c["slides"]):
            fh.write(f"| `{c['file']}` | {c['slides']} | {c['layouts']} | {c['media']} | "
                     f"{c['bytes'] // 1024} | [{c['label']}]({c['url']}) |\n")
    return 0 if collected else 1


if __name__ == "__main__":
    sys.exit(main())
