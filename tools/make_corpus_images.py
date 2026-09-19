"""Картинки для корпуса входов (`Z-36`). Свои, простые, на стандартной библиотеке.

Зачем не растры наших колод. Колода собирается на **чужом** шаблоне из
`samples/`, и её растр — производное от чужого дизайна. `samples/` не
коммитится именно поэтому; класть в коммиченный `examples/` картинку, снятую с
чужого шаблона, значит обойти собственное же правило.

Поэтому картинки рисуются здесь: цвета и формы наши, прав третьих лиц нет.
Содержание намеренно простое — задача картинок не иллюстрировать, а **дать
тракту вставки изображений** (`Z-28`) чем работать.

    python tools/make_corpus_images.py

PNG пишется вручную: `zlib` и `struct` из стандартной библиотеки, никаких
зависимостей (`ADR-0001`). Формат — RGB без прозрачности, 8 бит на канал.
"""

from __future__ import annotations

import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "examples", "img")


def png(path: str, width: int, height: int, pixels: list[list[tuple[int, int, int]]]) -> None:
    """Пишет PNG. `pixels[y][x]` — тройка 0..255."""
    raw = bytearray()
    for row in pixels:
        raw.append(0)                               # фильтр строки: none
        for r, g, b in row:
            raw += bytes((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    body = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(body)


def canvas(w: int, h: int, colour=(250, 250, 248)):
    return [[colour for _ in range(w)] for _ in range(h)]


def box(img, x0: int, y0: int, x1: int, y1: int, colour) -> None:
    h, w = len(img), len(img[0])
    for y in range(max(0, y0), min(h, y1)):
        row = img[y]
        for x in range(max(0, x0), min(w, x1)):
            row[x] = colour


#: Наша палитра: тёмно-синий, бирюзовый, серый. Ничего заимствованного.
INK = (32, 46, 72)
ACCENT = (46, 160, 156)
MUTED = (198, 203, 212)


def stages(path: str) -> None:
    """Четыре стадии пайплайна как четыре блока со стрелками между ними."""
    w, h = 960, 260
    img = canvas(w, h)
    x, top, bw, bh, gap = 40, 80, 190, 100, 30
    for i in range(4):
        left = x + i * (bw + gap)
        box(img, left, top, left + bw, top + bh, INK if i % 2 == 0 else ACCENT)
        if i < 3:                                    # стрелка-перемычка
            box(img, left + bw, top + bh // 2 - 4, left + bw + gap, top + bh // 2 + 4, MUTED)
    box(img, 40, h - 40, w - 40, h - 36, MUTED)      # подчёркивающая линия
    png(path, w, h, img)


def overflow(path: str) -> None:
    """Столбики «было 16 — стало 1»: числа из WORKLOG/2026-09-13-verify-loop."""
    w, h = 640, 420
    img = canvas(w, h)
    base, unit = h - 60, 20                          # 20 пикселей на единицу
    box(img, 60, base - 16 * unit, 260, base, MUTED)
    box(img, 380, base - 1 * unit, 580, base, ACCENT)
    box(img, 40, base, w - 40, base + 4, INK)        # ось
    png(path, w, h, img)


def main() -> int:
    made = []
    for name, draw in (("pipeline-stages.png", stages), ("overflow-16-to-1.png", overflow)):
        path = os.path.join(OUT, name)
        draw(path)
        made.append((name, os.path.getsize(path)))
    for name, size in made:
        print(f"{name:24} {size:7} байт")
    print(f"каталог: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
