"""Пиктограммы по смыслу пункта — `Z-32`, `ADR-0028`.

ТЗ, раздел 2, п. 3: «генерация графиков, таблиц, диаграмм, пиктограмм… внутри
слайда». Место под пиктограмму шаблон даёт сам — значок рядом с пунктом
(`Slot.picture_kind == icon`): на выданных шаблонах это линейные иконки одного
цвета, и на клоне они стоят те же, о чём бы ни был пункт, — лампочка у
«Выгрузки в pptx». Здесь решается, **какая** иконка встанет вместо значка:
это смысл, и выбирает модель — из открытого набора (`config/icons.json`,
файлы — `assets/icons/tabler/`). Ставит её сборка нативной фигурой
(`compose/icon.py`).

**Какие значки меняются.** Картинка донора, мельче порога иконки
(`analyze/picture.py`), почти квадратная (`max_aspect`: вытянутая — логотип
или полоска текста), не фирменная (не стоит на макетах и мастерах шаблона —
бренд живёт там, `plan/donor.py`) и стоит у пункта, в который лёг наш текст:
зазор до ближайшего заполненного текстового места не больше `max_gap` сторон
значка. Остальные значки остаются как были.

**Цвет** — цвет самого значка донора: так дизайнер решил контраст с плашкой
под ним (белая иконка на синем круге VK Education, синяя — на белом). Цвет
снять не удалось — первый акцент темы шаблона.

Запрос к модели — на колоду, пакетами до 40 пунктов; вариант вёрстки
спрашивает только о новых пунктах, одинаковые с прошлым вариантом берёт из
памяти выбора. Модели нет, она не
ответила или ответ не по форме — значки шаблона остаются, и сводка это
называет. Промпт — только в конфиге (`ADR-0022`, `Z-72`): нет файла —
сборка останавливается с понятной ошибкой (`config.MissingConfig`).
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import zlib
from dataclasses import dataclass, field, replace

CONFIG_NAME = "icons.json"

#: Самое большее пунктов в одном запросе: список иконок и пункты должны
#: уместиться в контекст с запасом; больше — несколько запросов.
_BATCH = 40
#: Пункт длиннее — обрезается: для выбора иконки хватает начала.
_TEXT_LIMIT = 200


@dataclass(frozen=True)
class IconConfig:
    system: str = ""
    #: Реплика с пунктами: `{catalog}` — строки «имя — о чём», `{items}` —
    #: пронумерованные пункты.
    user: str = ""
    #: Имя иконки → о чём она. Только те, чей файл есть в наборе.
    icons: dict = field(default_factory=dict)
    max_aspect: float = 1.67
    max_gap: float = 3.0
    version: str = ""


def config_path() -> str:
    from .cache import repo_root

    return os.path.join(repo_root(), "config", CONFIG_NAME)


def load_config(path: str | None = None, icon_root: str | None = None) -> IconConfig:
    """Настройки и словарь иконок. Промпта в коде нет (`Z-72`): файла нет или
    в нём нет промпта, реплики или хотя бы двух иконок с файлами —
    `MissingConfig`."""
    from ..compose.icon import assets_root
    from ..config import missing

    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise missing(CONFIG_NAME, f"файла нет или он не читается ({exc.__class__.__name__})") from exc
    root = icon_root or os.path.join(assets_root(), "tabler")
    listed = raw.get("icons") if isinstance(raw.get("icons"), dict) else {}
    icons = {str(k): str(v) for k, v in listed.items()
             if os.path.isfile(os.path.join(root, f"{k}.svg"))}
    system = str(raw.get("system") or "").strip()
    user = str(raw.get("user") or "")
    if not system or "{catalog}" not in user or "{items}" not in user:
        raise missing(CONFIG_NAME, "нет промпта выбора пиктограмм (system, user с {catalog} и {items})")
    if len(icons) < 2:
        raise missing(CONFIG_NAME, "в словаре меньше двух иконок, чьи файлы есть в assets/icons/tabler/")
    base = IconConfig()
    aspect, gap = raw.get("max_aspect"), raw.get("max_gap")
    return IconConfig(
        system=system, user=user, icons=icons,
        max_aspect=float(aspect) if isinstance(aspect, (int, float)) and aspect >= 1 else base.max_aspect,
        max_gap=float(gap) if isinstance(gap, (int, float)) and gap > 0 else base.max_gap,
        version=str(raw.get("version", "")),
    )


# --- цвет значка донора ------------------------------------------------------


def dominant_color(data: bytes) -> str | None:
    """Цвет значка: самый частый цвет его непрозрачных пикселей, `#RRGGBB`.

    Частый, а не средний: сглаженный край иконки смешан с прозрачным фоном и
    увёл бы среднее. PNG с альфой (RGBA, серый с альфой) и с палитрой; без
    прозрачности цвет значка от плашки не отличить — `None`."""
    from ..analyze.raster import _PNG, _chunks, _unfilter_plane

    if not data.startswith(_PNG):
        return None
    header, idat, palette, trns = None, [], None, None
    for kind, payload in _chunks(data):
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload[:13])
        elif kind == b"IDAT":
            idat.append(payload)
        elif kind == b"PLTE":
            palette = payload
        elif kind == b"tRNS":
            trns = payload
    if header is None:
        return None
    width, height, depth, ctype, _, _, interlace = header
    if depth != 8 or interlace or width * height > 4_000_000:
        return None
    channels = {6: 4, 4: 2, 3: 1}.get(ctype)
    if channels is None or (ctype == 3 and (palette is None or trns is None)):
        return None
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error:
        return None
    planes = [_unfilter_plane(raw, width, height, channels, k) for k in range(channels)]
    if any(p is None for p in planes):
        return None
    step = max(1, max(width, height) // 128)
    counts: dict[tuple[int, int, int], list[int]] = {}
    alpha_of = bytes(trns).ljust(256, b"\xff") if ctype == 3 else None
    for y in range(0, height, step):
        for x in range(0, width, step):
            if ctype == 3:
                i = planes[0][y][x]
                a = alpha_of[i]
                r, g, b = palette[3 * i:3 * i + 3] if 3 * i + 3 <= len(palette) else (0, 0, 0)
            elif ctype == 6:
                r, g, b, a = (p[y][x] for p in planes)
            else:
                r = g = b = planes[0][y][x]
                a = planes[1][y][x]
            if a < 128:
                continue
            key = (r >> 4, g >> 4, b >> 4)
            acc = counts.setdefault(key, [0, 0, 0, 0])
            acc[0] += 1
            acc[1] += r
            acc[2] += g
            acc[3] += b
    if not counts:
        return None
    n, r, g, b = max(counts.values(), key=lambda acc: (acc[0], acc[1:]))
    return "#{:02X}{:02X}{:02X}".format(round(r / n), round(g / n), round(b / n))


# --- где стоят значки --------------------------------------------------------


@dataclass(frozen=True)
class Spot:
    """Значок донора, который станет пиктограммой."""

    slide: int                  # PlannedSlide.index
    shape_id: str               # p:cNvPr/@id картинки донора
    text: str                   # наш текст у значка
    title: str                  # заголовок слайда — для смысла
    color: str | None


def _gap(a, b) -> int:
    dx = max(0, max(a.x, b.x) - min(a.x + a.cx, b.x + b.cx))
    dy = max(0, max(a.y, b.y) - min(a.y + a.cy, b.y + b.cy))
    return max(dx, dy)


def _fill_text(fill) -> str:
    if fill.kind == "list":
        text = "; ".join(i for i in (fill.items or ()) if i)
    else:
        text = fill.text or ""
    return " ".join(text.split())[:_TEXT_LIMIT]


def spots(plan, library, template: str, cfg: IconConfig) -> list[Spot]:
    """Значки донора на слайдах плана, которые станут пиктограммами."""
    from ..analyze.picture import ICON
    from ..opc.package import Package
    from .donor import _pictures_of

    patterns = {p.id: p for p in library.patterns}
    out: list[Spot] = []
    with Package(template) as pkg:
        layouts = [n for n in pkg.part_names if n.endswith(".xml")
                   and n.startswith(("/ppt/slideLayouts/slideLayout", "/ppt/slideMasters/slideMaster"))]
        brand: set[str] = set()
        for name in layouts:
            for _sid, media, _cx, _cy in _pictures_of(pkg, name, pkg.xml(name)):
                brand.add(hashlib.sha256(pkg.read(media)).hexdigest())
        trees: dict[str, object] = {}
        for slide in plan.slides:
            pattern = patterns.get(slide.pattern_id)
            if pattern is None or pattern.source != "slides" or not pkg.has_part(pattern.donor_part):
                continue
            slots = {s.id: s for s in pattern.slots}
            texts = [(slots[f.slot_id], _fill_text(f)) for f in slide.fills
                     if f.kind in ("text", "list", "number") and f.slot_id in slots]
            texts = [(s, t) for s, t in texts if t]
            if not texts:
                continue
            title = next((t for s, t in texts if s.role == "title"), "")
            used = {f.slot_id for f in slide.fills}
            dropped = set(getattr(slide, "dropped_pictures", ()))
            if pattern.donor_part not in trees:
                trees[pattern.donor_part] = pkg.xml(pattern.donor_part)
            media = {sid: m for sid, m, _cx, _cy
                     in _pictures_of(pkg, pattern.donor_part, trees[pattern.donor_part])}
            for s in pattern.slots:
                if s.content_type != "image" or s.picture_kind != ICON or s.id in used:
                    continue
                r = s.rect
                if not (r.cx and r.cy) or max(r.cx / r.cy, r.cy / r.cx) > cfg.max_aspect:
                    continue
                part = media.get(s.shape_id)
                if part is None or s.shape_id in dropped:
                    continue
                data = pkg.read(part)
                if hashlib.sha256(data).hexdigest() in brand:
                    continue
                side = max(r.cx, r.cy)
                gap, near = min(((_gap(r, t.rect), text) for t, text in texts),
                                key=lambda q: q[0])
                if gap > cfg.max_gap * side:
                    continue
                out.append(Spot(slide.index, s.shape_id, near,
                                title if title != near else "", dominant_color(data)))
    return out


# --- выбор модели ------------------------------------------------------------


def _schema(names: list[str], n: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "icons": {"type": "array", "items": {"type": "string", "enum": names},
                      "minItems": n, "maxItems": n},
        },
        "required": ["icons"],
    }


def _key(spot: Spot) -> tuple[str, str]:
    return spot.title, spot.text


@dataclass
class Picker:
    """Выбор пиктограмм одной сборки. Клиент к модели — один на сборку, и
    отказ сервера он помнит вместе с остальными вызовами (`client.Outage`)."""

    cfg: IconConfig
    model_config: object
    template: str
    outage: object = None
    chosen: dict = None
    asked: int = 0
    cached: int = 0
    failure: str | None = None
    _client: object = None

    def __post_init__(self):
        self.chosen = {}

    def client(self):
        from .client import ModelClient

        if self._client is None:
            self._client = ModelClient(self.model_config, inputs=(self.template,), outage=self.outage)
        return self._client

    def _ask(self, keys: list[tuple[str, str]]) -> None:
        from .prompt import Mode, Request
        from .validate import extract_json

        names = list(self.cfg.icons)
        catalog = "\n".join(f"{k} — {v}" for k, v in self.cfg.icons.items())
        for start in range(0, len(keys), _BATCH):
            batch = keys[start:start + _BATCH]
            items = "\n".join(f"{n}. " + (f"[{title}] " if title else "") + text
                              for n, (title, text) in enumerate(batch, 1))
            schema = _schema(names, len(batch))
            user = self.cfg.user.replace("{catalog}", catalog).replace("{items}", items)
            if self.model_config.mode is Mode.FREE_TEXT:
                user += "\n\nОтветь одним JSON-объектом по схеме:\n" + json.dumps(schema, ensure_ascii=False)
            request = Request(mode=self.model_config.mode, system=self.cfg.system, user=user,
                              schema=schema, section_id="пиктограммы", candidates=(),
                              name="icons", tool="pick_icons",
                              purpose="Подобрать пиктограмму к каждому пункту слайда.")
            answer = self.client().complete(request)
            if not answer:
                self.failure = answer.note or "ответа нет"
                return
            parsed = extract_json(answer.text)
            got = parsed.get("icons") if isinstance(parsed, dict) else None
            if not (isinstance(got, list) and len(got) == len(batch)
                    and all(g in self.cfg.icons for g in got)):
                self.failure = "ответ не по форме: нужен список имён из набора той же длины"
                return
            if answer.source == "cache":
                self.cached += 1
            else:
                self.asked += 1
            self.chosen.update(zip(batch, got))

    def mark(self, plan, library):
        """План, где у слайдов помечены значки и какая пиктограмма встанет
        на место каждого. Модель выключена (`--llm off`) — ни вопроса, ни
        слова: от модели отказались явно."""
        from ..model import PlacedIcon
        from .client import Access

        if self.model_config.access is Access.OFF:
            return plan
        found = spots(plan, library, self.template, self.cfg)
        todo = list(dict.fromkeys(_key(s) for s in found if _key(s) not in self.chosen))
        if todo and self.failure is None:
            self._ask(todo)
        per_slide: dict[int, list] = {}
        for s in found:
            name = self.chosen.get(_key(s))
            if name:
                per_slide.setdefault(s.slide, []).append(PlacedIcon(s.shape_id, name, s.color))
        if not per_slide:
            return plan
        slides = tuple(replace(s, icons=tuple(per_slide[s.index])) if s.index in per_slide else s
                       for s in plan.slides)
        return replace(plan, slides=slides)

    def note(self) -> str | None:
        if not (self.chosen or self.failure):
            return None
        if self.failure and not self.chosen:
            return (f"Пиктограммы не подобраны: {self.failure} — значки шаблона оставлены (Z-32).")
        line = (f"Пиктограммы: модель подобрала {len(set(self.chosen.values()))} разных к "
                f"{len(self.chosen)} пунктам (из кэша запросов {self.cached}); значки шаблона "
                f"заменены нативными фигурами (Z-32).")
        if self.failure:
            line += f" Часть не подобрана: {self.failure} — там значки шаблона оставлены."
        return line
