"""Где картинка непрозрачна. Шаг Ш2 плана `PLAN-7.8` (`Z-48`).

Отвечает на один вопрос: **в какой своей части эта картинка что-то закрывает**.
Ответ нужен стадии ANALYZE, чтобы не считать заслонением текст, лежащий на
прозрачной части декоративного PNG.

## Почему по пикселям, а не по типу фигуры

Карточка `Z-48` предполагала, что `p:pic` можно считать непрозрачной целиком.
Замер это опроверг: по такому правилу сузились бы 90 слотов корпуса из 1657,
**31 до нуля**, и **30 обнулённых — на одном слайде выданного VK Tech**, где на
растре видны все подписи. «Заслонитель» там — рамка карточки, PNG 224×671,
непрозрачный на **1%** (`WORKLOG/2026-09-21-z48-baseline.md`, замер 4).

**Наличие альфа-канала признаком не служит:** 94 картинки-заслонителя корпуса
из 97 — RGBA. А значения альфы разделяют случаи чисто: 1% у ложных против 49%
у настоящего, и 85% в той его трети, куда уезжает наш текст.

## Что возвращается и чего не возвращается

Сетка долей непрозрачных пикселей, `rows` × `cols`. `None` означает **«судить
не по чему»** и отдельно от «прозрачно»: чересстрочный PNG, неизвестный формат,
картинка крупнее потолка. Сузить слот по такому ответу нельзя — измеритель
обязан отличать «проверено и чисто» от «проверить не смог».

Внешних зависимостей нет: `zlib` и `struct` из стандартной библиотеки
(`ADR-0001`). Что разбирается и что нет — `DOM-PKG §10`.
"""

from __future__ import annotations

import struct
import zlib

_PNG = b"\x89PNG\r\n\x1a\n"

#: Выше этого фигура считается непрозрачной. Полупрозрачная заливка текст не
#: прячет — замер `Z-47` поймал ровно такой случай: текст, накрытый на 100%,
#: читался отлично.
OPAQUE = 0.5

#: Потолок на размер картинки. Нужен не ради скорости, а ради предсказуемости:
#: ограничение по времени сделало бы ответ зависящим от машины, а
#: детерминированность обязательна. Самая крупная картинка корпуса — 4397×2302,
#: это 10.1 мегапикселя; потолок взят в полтора раза выше, чтобы ни одна из них
#: не получила отказ по размеру. Отказ здесь означает «судить не по чему», то
#: есть слот **не** сужается.
MAX_PIXELS = 16_000_000


def _chunks(data: bytes):
    i = len(_PNG)
    n = len(data)
    while i + 8 <= n:
        length, kind = struct.unpack(">I4s", data[i:i + 8])
        yield kind, data[i + 8:i + 8 + length]
        i += 12 + length


def _unfilter_plane(
    raw: bytes, width: int, height: int, bpp: int, offset: int
) -> list[bytes] | None:
    """Снять построчные фильтры PNG, разбирая **один канал** из `bpp`.

    Так можно, и это не приближение: любой фильтр PNG ссылается либо на
    `x[i - bpp]` — тот же канал предыдущего пикселя, — либо на `prev[i]`, тот же
    канал строкой выше. Каналы не перемешиваются, значит альфу можно снять, не
    трогая цвет. Для RGBA это вчетверо меньше работы.
    """
    stride = width * bpp
    if len(raw) < (stride + 1) * height:
        return None
    out: list[bytes] = []
    prev = bytes(width)
    pos = 0
    for _ in range(height):
        kind = raw[pos]
        line = bytearray(raw[pos + 1 + offset:pos + 1 + stride:bpp])
        pos += 1 + stride
        if len(line) < width:
            return None
        if kind == 1:
            for i in range(1, width):
                line[i] = (line[i] + line[i - 1]) & 0xFF
        elif kind == 2:
            for i in range(width):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif kind == 3:
            for i in range(width):
                left = line[i - 1] if i else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif kind == 4:
            for i in range(width):
                left = line[i - 1] if i else 0
                up = prev[i]
                upleft = prev[i - 1] if i else 0
                guess = left + up - upleft
                da, db, dc = abs(guess - left), abs(guess - up), abs(guess - upleft)
                best = left if (da <= db and da <= dc) else (up if db <= dc else upleft)
                line[i] = (line[i] + best) & 0xFF
        elif kind != 0:
            return None
        prev = bytes(line)
        out.append(prev)
    return out


#: Полупрозрачное не прячет: всё, что ниже половины, считается прозрачным.
_SOLID = bytes(0 if v < 128 else 1 for v in range(256))


def _grid(
    alpha_rows: list[bytes], width: int, cols: int, rows: int
) -> tuple[tuple[float, ...], ...]:
    """Доли непрозрачных пикселей по клеткам.

    Счёт идёт через `translate` и `count` — обе операции уровня C. Наивный
    перебор пикселей на Python стоил бы столько же, сколько сам разбор PNG.
    """
    height = len(alpha_rows)
    counts = [[0] * cols for _ in range(rows)]
    totals = [[0] * cols for _ in range(rows)]
    bounds = [(c * width // cols, (c + 1) * width // cols) for c in range(cols)]
    for y, line in enumerate(alpha_rows):
        band = min(rows - 1, y * rows // height)
        flat = line.translate(_SOLID)
        row_counts, row_totals = counts[band], totals[band]
        for c, (a, b) in enumerate(bounds):
            if b <= a:
                continue
            row_totals[c] += b - a
            row_counts[c] += flat[a:b].count(1)
    return tuple(
        tuple(
            (counts[r][c] / totals[r][c]) if totals[r][c] else 0.0
            for c in range(cols)
        )
        for r in range(rows)
    )


def _png_opacity(data: bytes, cols: int, rows: int) -> tuple[tuple[float, ...], ...] | None:
    header = palette_alpha = None
    idat: list[bytes] = []
    for kind, payload in _chunks(data):
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload[:13])
        elif kind == b"IDAT":
            idat.append(payload)
        elif kind == b"tRNS":
            palette_alpha = payload
        elif kind == b"IEND":
            break
    if header is None:
        return None
    width, height, depth, ctype, _, _, interlace = header
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        return None
    if interlace:
        return None                       # Adam7 не разбираем: судить не по чему

    if ctype in (0, 2) and palette_alpha is None:
        return _solid(cols, rows)         # серый и RGB без tRNS — непрозрачны
    if ctype == 3 and palette_alpha is None:
        return _solid(cols, rows)
    if depth not in (8, 16):
        return None                       # 1/2/4 бита на канал не разбираем
    if ctype in (0, 2):
        return None                       # tRNS-цвет: редкость, судить не по чему

    samples = {3: 1, 4: 2, 6: 4}.get(ctype)
    if samples is None:
        return None
    if ctype == 3 and depth != 8:
        return None
    width_bytes = depth // 8
    bpp = samples * width_bytes
    # Старший байт альфы: для 16 бит младший на ответ не влияет.
    offset = bpp - width_bytes
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error:
        return None
    lines = _unfilter_plane(raw, width, height, bpp, offset)
    if lines is None:
        return None

    if ctype == 3:
        # Палитра: прозрачность лежит в tRNS по индексу цвета. Индексов,
        # которых в tRNS нет, касается умолчание «непрозрачно».
        table = bytes(palette_alpha).ljust(256, bytes([0xFF]))
        lines = [line.translate(table) for line in lines]

    return _grid(lines, width, cols, rows)


def _solid(cols: int, rows: int) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(1.0 for _ in range(cols)) for _ in range(rows))


def opacity_grid(
    data: bytes, cols: int = 32, rows: int = 32
) -> tuple[tuple[float, ...], ...] | None:
    """Доли непрозрачных пикселей по клеткам, или `None` — судить не по чему.

    Форматы без альфы (JPEG, BMP) непрозрачны целиком и не разбираются вовсе.
    """
    if data.startswith(_PNG):
        return _png_opacity(data, cols, rows)
    if data[:2] == b"\xff\xd8" or data[:2] == b"BM":
        return _solid(cols, rows)         # JPEG и BMP альфы не несут
    return None
