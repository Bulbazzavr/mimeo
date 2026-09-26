"""Вид слота под картинку: место под иллюстрацию или декор шаблона.

Задача `Z-28a`, план `PLAN-7.10`, шаг 2.

**Зачем это отдельный признак.** У слота под картинку `content_type` один —
`image`, — а назначений в живых шаблонах три: место под иллюстрацию, иконка
рядом с подписью и подложка карточки, лежащая под её же текстом. Положить нашу
схему в иконку значит испортить слайд; положить под текст — сделать текст
нечитаемым.

**Считается по геометрии, и это принципиально.** Размер слота, его положение и
перекрытие с текстовыми слотами лежат в XML точно, в EMU. Мы не угадываем и не
спрашиваем модель: тот же ход, что в `Z-43`, где признак гарнитуры живёт на
слоте и считается из того, что известно точно.

**Замер, из-за которого модуль появился** (`WORKLOG/2026-09-21-z28a-baseline.md`):
из **688** слотов `image` по корпусу годен под иллюстрацию **каждый пятый** —
135. Остальные: 542 меньше порога (иконки, буллиты, логотипы), 54 лежат под
текстовым слотом, 8 — фон во весь слайд. У выданного VK WorkSpace под текстом
**42 слота из 74**.

Пороги — в `config/images.json`, не здесь: это настройка, а не контракт
(`ADR-0022`).
"""

from __future__ import annotations

import json
import os
import re

#: Виды слота под картинку.
ILLUSTRATION = "illustration"   # место под иллюстрацию — единственное, куда мы кладём
ICON = "icon"                   # мельче порога: пиктограмма, буллит, логотип
BACKDROP = "backdrop"           # подложка карточки: под ней лежит текстовый слот
BACKGROUND = "background"       # фон во весь слайд — это стиль шаблона
FRAME = "frame"                 # рамка под фото с подсказкой дизайнера (`Z-55`)

#: Запасные значения, если конфиг не прочитался. Совпадают с файлом: движок
#: обязан работать и без `config/`, но молчать об этом не должен.
_FALLBACK = {
    "min_side_ratio": 0.08,
    "backdrop_overlap": 0.5,
    "background_area": 0.6,
    "aspect_tolerance": 0.3,
    "frame_hint_center": 0.2,
    "frame_hint_chars": 30,
}
_FALLBACK_HINTS = ("фото", "изображен", "картинк", "иллюстрац", "image", "photo", "picture")

_TEXTY = ("text", "list", "number")


def config_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(os.path.dirname(root), "config", "images.json")


class PictureConfig:
    """Пороги плюс признак «прочитан ли файл».

    Признак нужен по правилу `CLAUDE.md`: измеритель обязан отличать
    «проверено и чисто» от «проверить не смог». Молчаливый откат на встроенные
    значения — это второе, выданное за первое.
    """

    def __init__(self, values: dict, loaded: bool, version: str = "",
                 hints: tuple[str, ...] = _FALLBACK_HINTS) -> None:
        self.min_side_ratio = float(values["min_side_ratio"])
        self.backdrop_overlap = float(values["backdrop_overlap"])
        self.background_area = float(values["background_area"])
        self.aspect_tolerance = float(values["aspect_tolerance"])
        self.frame_hint_center = float(values["frame_hint_center"])
        self.frame_hint_chars = int(values["frame_hint_chars"])
        self.frame_hints = tuple(re.compile(h, re.IGNORECASE) for h in hints)
        self.loaded = loaded
        self.version = version


def load_config(path: str | None = None) -> PictureConfig:
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return PictureConfig(dict(_FALLBACK), loaded=False)
    values = dict(_FALLBACK)
    for key in _FALLBACK:
        if isinstance(raw.get(key), (int, float)):
            values[key] = raw[key]
    hints = raw.get("frame_hints")
    ok = isinstance(hints, list) and hints and all(isinstance(h, str) for h in hints)
    try:
        return PictureConfig(values, loaded=True, version=str(raw.get("version", "")),
                             hints=tuple(hints) if ok else _FALLBACK_HINTS)
    except re.error:
        return PictureConfig(values, loaded=False, version=str(raw.get("version", "")))


def _overlap(inner, outer) -> float:
    """Доля площади `inner`, накрытая `outer`."""
    x = max(0, min(inner.x + inner.cx, outer.x + outer.cx) - max(inner.x, outer.x))
    y = max(0, min(inner.y + inner.cy, outer.y + outer.cy) - max(inner.y, outer.y))
    area = inner.cx * inner.cy
    return (x * y) / area if area else 0.0


def classify(rect, text_rects, slide_cx: int, slide_cy: int,
             cfg: PictureConfig | None = None) -> str:
    """Вид слота под картинку по его геометрии.

    Порядок проверок не произволен. Сначала **фон**: слот во весь слайд почти
    всегда накрыт текстом, и без этой проверки он звался бы подложкой. Потом
    **подложка**: она бывает и крупной, и мелкой. Потом **размер**.

    **Перекрытие проверяется в обе стороны, и вторую нашёл растр** (`Z-51`,
    `PLAN-7.11`). Первый вопрос — «какая доля **картинки** накрыта текстом»:
    он ловит подложку карточки, на которой текст лежит сверху. Второй —
    «какая доля **текста** накрыта картинкой»: он ловит широкую полосу, внутри
    которой у шаблона стоит подпись. Это разные вопросы, и слот, невинный по
    первому, бывает губителен по второму: на `p25` выданного VK Tech слот
    `s03` 5.69×1.73 накрыт подписью `s06` лишь на 22 % своей площади — значит
    не подложка, — а саму подпись он накрывает **целиком**, и наша картинка
    прячет её без следа. Замер: таких слотов по корпусу **52 из 179**, из них
    40 на VK Tech и 7 на VK Education.

    Порог у обеих проверок один (`backdrop_overlap`): второго значения замер не
    потребовал, а заводить настройку, которую нечем обосновать, — тот самый
    шум, за который `CLAUDE.md` ругает первую версию `doc_check`.
    """
    cfg = cfg or load_config()
    area = slide_cx * slide_cy
    if area and (rect.cx * rect.cy) / area > cfg.background_area:
        return BACKGROUND
    for other in text_rects:
        if _overlap(rect, other) > cfg.backdrop_overlap:
            return BACKDROP
        if _overlap(other, rect) > cfg.backdrop_overlap:
            return BACKDROP
    if min(rect.cx, rect.cy) < cfg.min_side_ratio * min(slide_cx, slide_cy):
        return ICON
    return ILLUSTRATION


def classify_slots(slots, slide_cx: int, slide_cy: int,
                   cfg: PictureConfig | None = None) -> dict[str, str]:
    """Вид для каждого слота `image` раскладки. Ключ — `Slot.id`.

    Рамку под фото (`find_frames`) вид не переспрашивает: геометрия звала бы её
    подложкой — подсказка лежит в ней целиком."""
    cfg = cfg or load_config()
    text_rects = [s.rect for s in slots if s.content_type in _TEXTY]
    return {
        s.id: classify(s.rect, text_rects, slide_cx, slide_cy, cfg)
        for s in slots
        if s.content_type == "image" and s.picture_kind != FRAME
    }


def find_frames(shapes, slide_cx: int, slide_cy: int,
                cfg: PictureConfig | None = None) -> list[tuple[object, object]]:
    """Рамки под фото с подсказкой дизайнера: пары (рамка, подсказка) — `Z-55`.

    Рамка — фигура без текста с видимой заливкой, не картинка, не таблица и не
    диаграмма, крупнее иконки и мельче фона. Подсказка — короткий текст со
    словом из `frame_hints`, центр которого стоит у центра рамки. На выданном
    VK Tech это белая карточка 4.08 × 2.29 дюйма с «Вставить фото» посередине:
    движок писал туда тезис, межстрочный подсказки — 52 % кегля, и фраза
    ложилась строками друг на друга, а сама карточка оставалась пустой.

    **Текст решает, геометрия подтверждает.** По 14 шаблонам «короткая надпись
    в центре фигуры без текста» — 77 случаев, подсказок под фото 6: кнопки,
    номера в кружках и плашки выглядят так же (замер 26 сентября). Рамок у
    подсказки бывает несколько (карточка в тени карточки) — берётся наименьшая.
    """
    cfg = cfg or load_config()
    side = min(slide_cx, slide_cy)
    frames = [
        s for s in shapes
        if s.rect is not None and not s.has_text and not s.image_part
        and not s.has_chart and not s.has_table and s.kind == "sp"
        and s.fill_kind not in (None, "none")
        and min(s.rect.cx, s.rect.cy) >= cfg.min_side_ratio * side
        and s.rect.cx * s.rect.cy <= cfg.background_area * slide_cx * slide_cy
    ]
    pairs: list[tuple[object, object]] = []
    taken: set[int] = set()
    for hint in shapes:
        if hint.rect is None or not hint.has_text or len(hint.text) > cfg.frame_hint_chars:
            continue
        if not any(p.search(hint.text) for p in cfg.frame_hints):
            continue
        hx, hy = hint.rect.x + hint.rect.cx / 2, hint.rect.y + hint.rect.cy / 2
        around = [
            f for f in frames
            if id(f) not in taken
            and abs(hx - (f.rect.x + f.rect.cx / 2)) <= cfg.frame_hint_center * f.rect.cx
            and abs(hy - (f.rect.y + f.rect.cy / 2)) <= cfg.frame_hint_center * f.rect.cy
        ]
        if not around:
            continue
        frame = min(around, key=lambda f: f.rect.cx * f.rect.cy)
        taken.add(id(frame))
        pairs.append((frame, hint))
    return pairs
