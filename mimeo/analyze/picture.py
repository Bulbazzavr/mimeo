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

#: Виды слота под картинку.
ILLUSTRATION = "illustration"   # место под иллюстрацию — единственное, куда мы кладём
ICON = "icon"                   # мельче порога: пиктограмма, буллит, логотип
BACKDROP = "backdrop"           # подложка карточки: под ней лежит текстовый слот
BACKGROUND = "background"       # фон во весь слайд — это стиль шаблона

#: Запасные значения, если конфиг не прочитался. Совпадают с файлом: движок
#: обязан работать и без `config/`, но молчать об этом не должен.
_FALLBACK = {
    "min_side_ratio": 0.08,
    "backdrop_overlap": 0.5,
    "background_area": 0.6,
    "aspect_tolerance": 0.3,
}

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

    def __init__(self, values: dict, loaded: bool, version: str = "") -> None:
        self.min_side_ratio = float(values["min_side_ratio"])
        self.backdrop_overlap = float(values["backdrop_overlap"])
        self.background_area = float(values["background_area"])
        self.aspect_tolerance = float(values["aspect_tolerance"])
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
    return PictureConfig(values, loaded=True, version=str(raw.get("version", "")))


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
    """
    cfg = cfg or load_config()
    area = slide_cx * slide_cy
    if area and (rect.cx * rect.cy) / area > cfg.background_area:
        return BACKGROUND
    for other in text_rects:
        if _overlap(rect, other) > cfg.backdrop_overlap:
            return BACKDROP
    if min(rect.cx, rect.cy) < cfg.min_side_ratio * min(slide_cx, slide_cy):
        return ICON
    return ILLUSTRATION


def classify_slots(slots, slide_cx: int, slide_cy: int,
                   cfg: PictureConfig | None = None) -> dict[str, str]:
    """Вид для каждого слота `image` раскладки. Ключ — `Slot.id`."""
    cfg = cfg or load_config()
    text_rects = [s.rect for s in slots if s.content_type in _TEXTY]
    return {
        s.id: classify(s.rect, text_rects, slide_cx, slide_cy, cfg)
        for s in slots
        if s.content_type == "image"
    }
