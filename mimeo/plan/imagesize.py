"""Собственные размеры картинки — из заголовка файла, без зависимостей.

Задача `Z-28a`, план `PLAN-7.10`, шаг 5. Нужно затем, чтобы выбрать слот, чья
пропорция ближе к пропорции картинки: обрезать и вписывать оказалось нельзя
(потеря содержания и чужая вёрстка соответственно), а выбрать место — можно.

Читается **только заголовок**, первые сотни байт: ни распаковки, ни пикселей.
Разбор пикселей у нас уже есть в `analyze/raster.py`, но он о другом — о
непрозрачности (`Z-48`), и тащить его сюда значило бы платить распаковкой
`zlib` за два числа.

**Формат не распознан — возвращается `None`, и это ответ «не смог», а не
«квадрат».** Правило `CLAUDE.md`: измеритель обязан отличать «проверено» от
«проверить не смог». Вызывающий на `None` оставляет порядок слотов прежним.
"""

from __future__ import annotations

import struct

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_GIF_MAGIC = (b"GIF87a", b"GIF89a")

#: Маркеры JPEG, несущие размеры кадра. Перечислены поимённо: `SOF4` (0xC4),
#: `SOF8` (0xC8) и `SOF12` (0xCC) — это таблицы Хаффмана и расширения, а не
#: кадры, и попытка прочесть из них размер даёт мусор.
_JPEG_SOF = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)

#: Сколько читаем. JPEG хранит кадр после таблиц, и у крупного файла он
#: отодвигается; 64 КиБ хватает с запасом и не грузит файл целиком.
_HEAD = 65536


def _png(data: bytes) -> tuple[int, int] | None:
    # IHDR идёт первым чанком и лежит на фиксированном месте.
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _gif(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10:
        return None
    return struct.unpack("<HH", data[6:10])


def _bmp(data: bytes) -> tuple[int, int] | None:
    if len(data) < 26:
        return None
    width, height = struct.unpack("<ii", data[18:26])
    # Высота отрицательна у растра, записанного сверху вниз.
    return (abs(width), abs(height))


def _jpeg(data: bytes) -> tuple[int, int] | None:
    i = 2
    end = len(data)
    while i + 9 < end:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in _JPEG_SOF:
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return (width, height)
        if length < 2:
            return None
        i += 2 + length
    return None


def image_size(path: str) -> tuple[int, int] | None:
    """Ширина и высота в пикселях, или `None`, если прочесть не вышло."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(_HEAD)
    except OSError:
        return None
    if data.startswith(_PNG_MAGIC):
        size = _png(data)
    elif data.startswith(_GIF_MAGIC):
        size = _gif(data)
    elif data.startswith(b"BM"):
        size = _bmp(data)
    elif data.startswith(b"\xff\xd8"):
        size = _jpeg(data)
    else:
        return None
    if not size or size[0] <= 0 or size[1] <= 0:
        return None
    return size
