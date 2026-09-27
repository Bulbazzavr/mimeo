"""Проверки кодом — детерминированная половина аудита (`Z-34`, `PLAN-11.0`, шаг 2).

На одном слайде всегда один ответ: опираются на план колоды и отчёт VERIFY —
пункты, слова, заголовки, габариты, — а не на смысл. Пороги — из Приложения 1
ТЗ: больше 6 пунктов, пункт длиннее 15 слов, пустой слайд, два одинаковых.
Остатки проверки вёрстки идут сюда же: они тоже счёт, а не суждение.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Приложение 1 ТЗ, «Плотность».
MAX_ITEMS = 6
MAX_WORDS = 15

#: Слайды, у которых заголовок — не вывод и содержания под ним нет по замыслу.
FRAME_KINDS = ("cover", "section", "closing", "agenda")

_WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё]")


@dataclass(frozen=True)
class SlideView:
    """Что аудиту нужно знать о слайде плана."""

    index: int                 # индекс в плане, 0-based
    position: int              # номер в колоде, 1-based
    kind: str                  # вид раскладки (`Pattern.kind`)
    title: str
    items: tuple[str, ...]
    texts: tuple[str, ...]     # весь текст слайда, кроме заголовка
    visuals: int               # картинки, таблицы, диаграммы


def views(plan, library, slides_written=()) -> tuple[SlideView, ...]:
    """Слайды плана в порядке колоды. Номер в колоде — по `slides_written`:
    сборка могла пропустить слайд, и номер плана тогда разъезжается с файлом."""
    patterns = {p.id: p for p in library.patterns}
    order = list(slides_written) or [s.index for s in plan.slides]
    by_index = {s.index: s for s in plan.slides}
    out = []
    for position, index in enumerate(order, 1):
        slide = by_index.get(index)
        if slide is None:
            continue
        pattern = patterns.get(slide.pattern_id)
        roles = {s.id: s.role for s in pattern.slots} if pattern else {}
        title, texts, items, visuals = "", [], [], 0
        for fill in slide.fills:
            if fill.kind in ("image", "table", "chart"):
                visuals += 1
                continue
            if fill.items:
                items.extend(i for i in fill.items if i)
                texts.extend(i for i in fill.items if i)
                continue
            if not fill.text:
                continue
            if not title and roles.get(fill.slot_id) == "title":
                title = fill.text
            else:
                texts.append(fill.text)
        if not title and texts:
            title = texts.pop(0)
        out.append(SlideView(index=index, position=position,
                             kind=pattern.kind if pattern else "",
                             title=" ".join(title.split()), items=tuple(items),
                             texts=tuple(texts), visuals=visuals))
    return tuple(out)


#: Допуск на округление координат замера, пункты (как `verify.space.SLACK`).
EDGE_SLACK = 2.0


def collisions(measurement) -> list[tuple[int, str, str, str]]:
    """Наезд надписей и выход текста за край слайда по замеру PowerPoint:
    (номер слайда в колоде, вид, id фигуры, словами).

    **Наезд** — прямоугольники набранного текста двух надписей перекрываются
    по ширине не меньше строки меньшего кегля и по высоте не меньше половины
    её. Порог взят текстом дизайнера до кода (правило `Z-53`): на 138 слайдах
    трёх выданных шаблонов такое перекрытие одно — звёздочка сноски VK Tech
    вплотную к тексту, 7 пт по ширине при кегле 11.7, — и порог «строка» её
    не зовёт; на девятке 27 сентября — одно, заголовок на подписи Education,
    34 пт при кегле 14, и оно настоящее.

    **Край** — набранный текст выходит за границу слайда."""
    import itertools

    if measurement is None or not getattr(measurement, "ok", False) or measurement.page is None:
        return []
    width, height = measurement.page
    by_slide: dict[int, list] = {}
    for s in measurement.shapes:
        if s.chars and s.bound_left is not None and s.bound_top is not None:
            by_slide.setdefault(s.slide, []).append(s)
    out = []
    for slide, shapes in sorted(by_slide.items()):
        for a in shapes:
            if (a.bound_left < -EDGE_SLACK or a.bound_top < -EDGE_SLACK
                    or a.bound_left + a.text_width > width + EDGE_SLACK
                    or a.bound_top + a.text_height > height + EDGE_SLACK):
                out.append((slide, "off_slide", a.shape_id, "текст выходит за край слайда"))
        for a, b in itertools.combinations(shapes, 2):
            ox = (min(a.bound_left + a.text_width, b.bound_left + b.text_width)
                  - max(a.bound_left, b.bound_left))
            oy = (min(a.bound_top + a.text_height, b.bound_top + b.text_height)
                  - max(a.bound_top, b.bound_top))
            sizes = [x for x in (a.font_size, b.font_size) if x > 0]
            line = min(sizes) if sizes else 12.0
            if ox >= line and oy >= 0.5 * line:
                out.append((slide, "collision", a.shape_id,
                            f"надпись наезжает на соседнюю: {ox:.0f} × {oy:.0f} пт при кегле {line:.0f}"))
    return out


def words(text: str) -> int:
    return sum(1 for w in text.split() if _WORD.search(w))


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\wё ]", " ", text.lower()).split())


def run(slides: tuple[SlideView, ...], verify: dict | None, measurement=None) -> list[dict]:
    """Находки кодом. Каждая — словарь полей находки без `id`. `measurement`
    — замер PowerPoint готовой колоды (`verify.metrics`) или `None`."""
    found: list[dict] = []
    by_position = {v.position: v for v in slides}
    for position, check, _shape, detail in collisions(measurement):
        v = by_position.get(position)
        if v is not None:
            found.append({"slide": position, "title": v.title, "check": check,
                          "kind": "deterministic", "detail": detail, "fix": "layout"})

    def add(view, check, detail, fix):
        found.append({"slide": view.position, "title": view.title, "check": check,
                      "kind": "deterministic", "detail": detail, "fix": fix})

    titles: dict[str, SlideView] = {}
    for v in slides:
        if len(v.items) > MAX_ITEMS:
            add(v, "items", f"пунктов {len(v.items)} при пределе {MAX_ITEMS}", "model")
        for item in v.items:
            n = words(item)
            if n > MAX_WORDS:
                short = item if len(item) <= 60 else item[:57].rstrip() + "…"
                add(v, "long_item", f"пункт «{short}» — {n} слов при пределе {MAX_WORDS}", "model")
        if v.kind not in FRAME_KINDS and not v.texts and not v.visuals:
            add(v, "empty", "на слайде один заголовок, содержания нет", "model")
        key = _norm(v.title)
        if key and v.kind not in FRAME_KINDS:
            if key in titles:
                add(v, "duplicate", f"заголовок повторяет слайд {titles[key].position}", "model")
            else:
                titles[key] = v

    if verify:
        by_index = {v.index: v for v in slides}
        for d in verify.get("unresolved") or ():
            v = by_index.get(d.get("slide"))
            if v is not None:
                add(v, "overflow", f"текст не влез в своё место в {d.get('ratio')} раза и ужат "
                                   "до предела читаемости — дальше не ужимается", None)
        for d in verify.get("overflow_width") or ():
            v = by_index.get(d.get("slide"))
            if v is not None:
                add(v, "too_wide", f"надпись шире своего места в {d.get('ratio')} раза", "layout")
        for d in verify.get("occluded") or ():
            v = by_index.get(d.get("slide"))
            if v is not None:
                add(v, "occluded", "текст закрыт фигурой шаблона — лечится выбором раскладки, "
                                   "а не кеглем", None)
    return found
