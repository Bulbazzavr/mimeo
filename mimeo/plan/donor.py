"""Картинки донора с чужим содержимым — `Z-62`, вторая половина.

Незаполненное место под картинку сохраняет картинку донора. Часто это
оформление шаблона — 3D-фигуры VK Tech, рамка телефона, — и его надо
оставить: это стиль. Но бывает и содержимое: кольцевая диаграмма с городами и
процентами (Education), промо-экран чужого приложения (VK Tech). Это числа и
факты, которых нет во входе (Приложение 1 ТЗ, вопрос 4), и чужой продукт на
нашей колоде.

Отличить одно от другого по байтам нельзя — это смысл, и его решает зрение
модели: та же Gemma 4 с проектором `mmproj`. Каждая картинка спрашивается
один раз — ответ идёт в кэш ответов модели по сумме картинки, а кэш выбирается
по пути шаблона: суждение о картинке выданного шаблона — производное данных
ТЗ и уходит в закрытый `tz/cache/llm/` (`ADR-0021`). Картинки, признанные
содержимым, план помечает (`PlannedSlide.dropped_pictures`), сборка убирает.
Модели нет или она не ответила — картинка остаётся, и отчёт это называет.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, replace

CONFIG_NAME = "audit.json"

#: Схема ответа — контракт, в коде (`ADR-0022`).
SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["content", "decor"]},
        "what": {"type": "string"},
    },
    "required": ["verdict", "what"],
}

#: Форматы, которые модель умеет принять картинкой. EMF, WMF и SVG — нет: такие
#: картинки не спрашиваются и остаются (сказано в отчёте).
_MIME = ((b"\x89PNG", "image/png"), (b"\xff\xd8", "image/jpeg"), (b"GIF8", "image/gif"))


@dataclass(frozen=True)
class AuditConfig:
    #: Промпт и вопрос — только из `config/audit.json`, `donor_pictures`
    #: (`Z-72`, ТЗ, раздел 4): копии в коде нет, `load_config` без них падает.
    system: str = ""
    question: str = ""
    #: Картинка, у которой и большая сторона короче этой доли меньшей стороны
    #: слайда, — значок, не спрашиваем (тот же порог, что делит слоты на иконки и
    #: иллюстрации, `config/images.json`). По большей стороне, а не по меньшей:
    #: полоска текста картинкой узкая, но видна.
    min_side: float = 0.08
    #: Картинка, стоящая на стольких слайдах, макетах и мастерах шаблона и
    #: больше, — фирменный знак или оформление: не спрашиваем. Иначе зрение
    #: назвало бы логотип бренда «содержимым», и он исчез бы со слайда.
    repeated: int = 3
    loaded: bool = False


def config_path() -> str:
    from .cache import repo_root

    return os.path.join(repo_root(), "config", CONFIG_NAME)


def load_config(path: str | None = None) -> AuditConfig:
    """Промпт зрения о картинках донора и пороги. Промпта нет — `MissingConfig`
    (`Z-72`); пороги — числа, негодный берётся встроенным."""
    from ..config import missing

    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = (json.load(fh) or {}).get("donor_pictures") or {}
    except (OSError, ValueError, AttributeError) as exc:
        raise missing(CONFIG_NAME, f"файла нет или он не читается ({exc.__class__.__name__})") from exc
    if not (raw.get("system") and raw.get("question")):
        raise missing(CONFIG_NAME, "нет промпта зрения о картинках донора (donor_pictures: system, question)")
    base = AuditConfig()
    min_side, repeated = raw.get("min_side"), raw.get("repeated")
    return AuditConfig(
        system=str(raw["system"]),
        question=str(raw["question"]),
        min_side=float(min_side) if isinstance(min_side, (int, float)) else base.min_side,
        repeated=int(repeated) if isinstance(repeated, int) and repeated > 0 else base.repeated,
        loaded=True,
    )


def _mime(data: bytes) -> str | None:
    return next((m for magic, m in _MIME if data.startswith(magic)), None)


#: Фон, на который кладётся прозрачная картинка перед показом модели. Сервер
#: модели прозрачность отбрасывает, и прозрачное становится чёрным: чёрный
#: текст на прозрачном — «VK WorkSpace» в макете телефона VK Tech — модель не
#: видела и звала картинку оформлением (26 сентября). На средне-сером видны и
#: чёрный, и белый текст.
_BACKDROP = 128
#: Большая сторона картинки для модели: хватает, чтобы прочесть надпись, и
#: меньше токенов.
_VISION_SIDE = 768


def flatten(data: bytes) -> bytes:
    """PNG с прозрачностью — на средне-серый фон и не крупнее `_VISION_SIDE`.
    Чего разбор не берёт (чересстрочный, палитра, 16 бит), отдаётся как есть."""
    import struct
    import zlib

    from ..analyze.raster import _PNG, _chunks, _unfilter_plane

    if not data.startswith(_PNG):
        return data
    header, idat = None, []
    for kind, payload in _chunks(data):
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload[:13])
        elif kind == b"IDAT":
            idat.append(payload)
    if header is None:
        return data
    width, height, depth, ctype, _, _, interlace = header
    channels = {6: 4, 4: 2}.get(ctype)
    if channels is None or depth != 8 or interlace:
        return data
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error:
        return data
    planes = [_unfilter_plane(raw, width, height, channels, k) for k in range(channels)]
    if any(p is None for p in planes):
        return data
    step = max(1, -(-max(width, height) // _VISION_SIDE))
    alpha = planes[-1]
    colour = planes[:-1] if channels == 4 else [planes[0]] * 3
    out_rows = []
    for y in range(0, height, step):
        a_line = alpha[y][::step]
        row = bytearray(b"\x00")
        lines = [c[y][::step] for c in colour]
        for x, a in enumerate(a_line):
            for line in lines:
                row.append((line[x] * a + _BACKDROP * (255 - a)) // 255)
        out_rows.append(bytes(row))
    w2, h2 = len(alpha[0][::step]), len(out_rows)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    return (_PNG + chunk(b"IHDR", struct.pack(">IIBBBBB", w2, h2, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(out_rows), 6)) + chunk(b"IEND", b""))


def _pictures_of(pkg, part: str, tree) -> list[tuple[str, str, int, int]]:
    """Картинки слайда: (id фигуры, часть картинки, ширина, высота в EMU)."""
    from ..oxml.ns import qn

    out = []
    for pic in tree.iter(qn("p:pic")):
        nv = pic.find(f"{qn('p:nvPicPr')}/{qn('p:cNvPr')}")
        blip = pic.find(f"{qn('p:blipFill')}/{qn('a:blip')}")
        ext = pic.find(f"{qn('p:spPr')}/{qn('a:xfrm')}/{qn('a:ext')}")
        rid = blip.get(qn("r:embed")) if blip is not None else None
        media = pkg.part_for_rid(part, rid) if rid else None
        if nv is None or media is None or not pkg.has_part(media):
            continue
        cx = int(ext.get("cx", 0)) if ext is not None else 0
        cy = int(ext.get("cy", 0)) if ext is not None else 0
        out.append((nv.get("id"), media, cx, cy))
    return out


def donor_pictures(plan, library, template: str, cfg: AuditConfig,
                   slide_size: tuple[int, int] | None = None) -> dict[tuple[int, str], bytes]:
    """Картинки донора, которые останутся на слайдах плана: {(слайд, id фигуры): байты}.

    Не наши (место не заполнено нашей картинкой), не значки (мельче
    `min_side`) и не фирменные (картинка стоит на `repeated` слайдах шаблона и
    больше: логотип, общий фон). Смотрятся все картинки слайда, а не только места
    под иллюстрацию: снимок интерфейса в макете телефона VK Tech местом не был."""
    from ..opc.package import Package

    patterns = {p.id: p for p in library.patterns}
    out: dict[tuple[int, str], bytes] = {}
    with Package(template) as pkg:
        # Сколько слайдов шаблона несут каждую картинку — по содержимому, а не
        # по имени части: у WorkSpace на каждом слайде своя копия фирменного
        # знака, и по имени он выглядел единственным (растр 26 сентября —
        # знак «VK WorkSpace» исчезал со слайда).
        digest: dict[str, str] = {}

        def sha(media: str) -> str:
            if media not in digest:
                digest[media] = hashlib.sha256(pkg.read(media)).hexdigest()
            return digest[media]

        # И по макетам с мастерами: бренд живёт там — знак «VK WorkSpace» стоит
        # на 2 слайдах, но на 14 макетах.
        seen: dict[str, set[str]] = {}
        prefixes = ("/ppt/slides/slide", "/ppt/slideLayouts/slideLayout", "/ppt/slideMasters/slideMaster")
        parts = [n for n in pkg.part_names if n.startswith(prefixes) and n.endswith(".xml")]
        trees = {n: pkg.xml(n) for n in parts}
        for name, tree in trees.items():
            for _sid, media, _cx, _cy in _pictures_of(pkg, name, tree):
                seen.setdefault(sha(media), set()).add(name)
        floor = cfg.min_side * min(slide_size) if slide_size else 0
        for slide in plan.slides:
            pattern = patterns.get(slide.pattern_id)
            if pattern is None or pattern.source != "slides" or pattern.donor_part not in trees:
                continue
            slots = {s.id: s for s in pattern.slots}
            ours = {slots[f.slot_id].shape_id for f in slide.fills
                    if f.kind == "image" and f.slot_id in slots}
            for sid, media, cx, cy in _pictures_of(pkg, pattern.donor_part, trees[pattern.donor_part]):
                # Значок мал по обеим сторонам; полоска текста картинкой — нет:
                # «VK WorkSpace», «Что тестировать?» в макете телефона VK Tech —
                # полоски 1.9 × 0.25 дюйма (растр 26 сентября).
                if sid in ours or max(cx, cy) < floor or len(seen.get(sha(media), ())) >= cfg.repeated:
                    continue
                out[(slide.index, sid)] = pkg.read(media)
    return out


@dataclass
class Judge:
    """Суждения зрения об картинках одной сборки: одна картинка — один вопрос.

    Клиент к модели — **один на сборку**, и отказ сервера он помнит вместе с
    остальными вызовами сборки (`client.Outage`): лежащий сервер узнаётся
    первой картинкой, а не каждой. Бюджет времени на модель у всех картинок
    тоже общий."""

    cfg: AuditConfig
    model_config: object
    template: str
    slide_size: tuple[int, int] | None = None
    verdicts: dict[str, str | None] = None
    asked: int = 0
    cached: int = 0
    unreadable: int = 0
    failure: str | None = None
    outage: object = None
    _client: object = None

    def __post_init__(self):
        self.verdicts = {}

    def client(self):
        from .client import ModelClient

        if self._client is None:
            self._client = ModelClient(self.model_config, inputs=(self.template,), outage=self.outage)
        return self._client

    def verdict(self, data: bytes) -> str | None:
        from .prompt import Mode, Request
        from .validate import extract_json

        sha = hashlib.sha256(data).hexdigest()
        if sha in self.verdicts:
            return self.verdicts[sha]
        mime = _mime(data)
        if mime is None:
            self.unreadable += 1
            self.verdicts[sha] = None
            return None
        shown = flatten(data) if mime == "image/png" else data
        url = f"data:{mime};base64," + base64.b64encode(shown).decode("ascii")
        request = Request(mode=Mode.JSON_SCHEMA, system=self.cfg.system, user=self.cfg.question,
                          schema=SCHEMA, section_id=f"картинка донора {sha[:12]}", candidates=(),
                          name="picture", tool="judge_picture",
                          purpose="Решить, содержимое ли картинка донора или оформление.",
                          images=(url,))
        answer = self.client().complete(request)
        if not answer:
            self.failure = answer.note
            self.verdicts[sha] = None
            return None
        if answer.source == "cache":
            self.cached += 1
        else:
            self.asked += 1
        parsed = extract_json(answer.text)
        got = parsed.get("verdict") if isinstance(parsed, dict) else None
        self.verdicts[sha] = got if got in ("content", "decor") else None
        return self.verdicts[sha]

    def mark(self, plan, library):
        """План, где у слайдов помечены места с картинкой-содержимым. Модель
        выключена (`--llm off`) — не спрашиваем и не пишем ни слова: от модели
        отказались явно."""
        from .client import Access

        if self.model_config.access is Access.OFF:
            return plan
        pictures = donor_pictures(plan, library, self.template, self.cfg, self.slide_size)
        drop: dict[int, list[str]] = {}
        for (index, shape_id), data in pictures.items():
            if self.verdict(data) == "content":
                drop.setdefault(index, []).append(shape_id)
        if not drop:
            return plan
        slides = tuple(replace(s, dropped_pictures=tuple(drop.get(s.index, ()))) if s.index in drop else s
                       for s in plan.slides)
        return replace(plan, slides=slides)

    def note(self) -> str | None:
        judged = [v for v in self.verdicts.values() if v]
        if not (judged or self.failure or self.unreadable):
            return None
        content = sum(1 for v in judged if v == "content")
        line = (f"Картинки донора в незаполненных местах: спрошено зрение модели о {len(judged)} "
                f"(из кэша {self.cached}), содержимое — {content}: такие убраны со слайдов (Z-62).")
        if self.unreadable:
            line += f" Не спрошено {self.unreadable} — формат, который модель не принимает (EMF, SVG)."
        if self.failure:
            line += f" Модель не ответила: {self.failure} — картинки донора оставлены."
        return line
