"""Проверки моделью по картинке слайда — контекстуальная половина аудита
(`Z-34`, `PLAN-11.0`, шаг 3).

Вход — **картинка готового слайда**, как велит Приложение 1 ТЗ («на вход —
картинка слайда»): её рисует PowerPoint (`raster.py`). Спрашивает та же
Gemma 4 с проектором `mmproj`, что судит картинки донора (`plan/donor.py`,
`ADR-0025`); один запрос на слайд, ответ — JSON по схеме ниже.

**Вопросы предметные, а не «найди недостатки».** Замер 17 сентября: вопрос
«есть ли обрезанный заголовок» провалили все семь моделей, а тот же вопрос с
указанием, на что смотреть, прошёл на той же картинке. Поэтому каждый вопрос
называет, что проверить, и ответ «да» у всех означает «хорошо».

Промпт — `config/audit.json`, раздел `slides` (ТЗ: промпты не зашиты в код);
схема ответа — контракт, в коде (`ADR-0022`). Ответ идёт в кэш модели по
картинке: та же колода спрашивается один раз.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass

#: Вопросы Приложения 1 ТЗ, которые смотрит модель: поле ответа, номер вопроса
#: в Приложении (0 — вёрстка, группа «Вёрстка»), что проверяли словами и чем
#: чинить. «Да» — хорошо.
QUESTIONS = (
    ("title_is_conclusion", 1, "заголовок — вывод, а не тема", "model"),
    ("content_matches_title", 2, "содержимое отвечает заголовку", "model"),
    ("has_content", 5, "кроме заголовка есть содержание", "model"),
    ("pictures_on_topic", 6, "картинки относятся к теме слайда", "model"),
    ("no_service_text", 7, "нет служебного текста", "model"),
    ("one_language", 9, "текст на одном языке", "model"),
)
#: Вёрстку глазами модель не судит: замер 27 сентября — на целом слайде наезд
#: заголовка на подпись (Education, вариант 1, слайд 9) не увиден даже вопросом
#: с названными надписями, а на вырезке этого места «да» дано и на двух чистых
#: из двух. Наезд и выход за край считает код по замеру PowerPoint
#: (`checks.collisions`).

SCHEMA = {
    "type": "object",
    "properties": {
        **{name: {"type": "boolean"} for name, *_ in QUESTIONS if name != "pictures_on_topic"},
        "pictures_on_topic": {"type": ["boolean", "null"]},
        "problem": {"type": "string"},
    },
    "required": [name for name, *_ in QUESTIONS] + ["problem"],
}

#: Где вопрос не задаётся по замыслу слайда: у обложки, раздела, финала и
#: оглавления заголовок — название, а не вывод, и содержания под ним нет.
SKIP = {
    "title_is_conclusion": ("cover", "section", "closing", "agenda"),
    "content_matches_title": ("cover", "section", "closing"),
    "has_content": ("cover", "section", "closing"),
}

CONFIG_NAME = "audit.json"


@dataclass(frozen=True)
class VisionConfig:
    #: Промпт, вопросы о слайде и просьба правки — только из `config/audit.json`,
    #: блок `slides` (`Z-72`, ТЗ, раздел 4): копии в коде нет.
    system: str = ""
    question: str = ""
    #: Что сказать модели при исправлении смысла (`build --fix`): `{findings}` —
    #: выбранные находки строками. Путь — `plan/outline.run`, `revise`.
    revise: str = ""
    loaded: bool = False


def config_path() -> str:
    from ..plan.cache import repo_root

    return os.path.join(repo_root(), "config", CONFIG_NAME)


def load_config(path: str | None = None) -> VisionConfig:
    """Промпт зрения о готовом слайде и просьба правки. Чего-то нет —
    `MissingConfig` (`Z-72`)."""
    from ..config import missing

    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = (json.load(fh) or {}).get("slides") or {}
    except (OSError, ValueError, AttributeError) as exc:
        raise missing(CONFIG_NAME, f"файла нет или он не читается ({exc.__class__.__name__})") from exc
    absent = [k for k in ("system", "question", "revise") if not raw.get(k)]
    if absent:
        raise missing(CONFIG_NAME, f"нет промпта аудита слайдов (slides: {', '.join(absent)})")
    return VisionConfig(system=str(raw["system"]), question=str(raw["question"]),
                        revise=str(raw["revise"]), loaded=True)


@dataclass
class Eye:
    """Зрение модели на одну колоду: клиент один, отказ сервера — общий со
    сборкой (`client.Outage`), лежащий сервер узнаётся первым слайдом."""

    cfg: VisionConfig
    model_config: object
    inputs: tuple[str, ...]
    outage: object = None
    asked: int = 0
    cached: int = 0
    failure: str | None = None
    _client: object = None

    def client(self):
        from ..plan.client import ModelClient

        if self._client is None:
            self._client = ModelClient(self.model_config, inputs=self.inputs, outage=self.outage)
        return self._client

    def look(self, png: str, n: int, total: int) -> dict | None:
        """Ответ модели о слайде — словарь по `SCHEMA` или `None`: не спросили."""
        from ..plan.donor import flatten
        from ..plan.prompt import Mode, Request
        from ..plan.validate import extract_json

        if self.failure:
            return None
        with open(png, "rb") as fh:
            data = fh.read()
        url = "data:image/png;base64," + base64.b64encode(flatten(data)).decode("ascii")
        request = Request(mode=Mode.JSON_SCHEMA, system=self.cfg.system,
                          user=self.cfg.question.format(n=n, total=total), schema=SCHEMA,
                          section_id=f"слайд {n}", candidates=(), name="slide_audit",
                          tool="audit_slide", purpose="Проверить готовый слайд по его картинке.",
                          images=(url,))
        answer = self.client().complete(request)
        if not answer:
            self.failure = answer.note
            return None
        if answer.source == "cache":
            self.cached += 1
        else:
            self.asked += 1
        parsed = extract_json(answer.text)
        return parsed if isinstance(parsed, dict) else None


def findings(answer: dict, view) -> list[dict]:
    """Находки из ответа о слайде `view` (`checks.SlideView`)."""
    out = []
    problem = str(answer.get("problem") or "").strip()
    for name, number, label, fix in QUESTIONS:
        if view.kind in SKIP.get(name, ()):
            continue
        if answer.get(name) is False:
            where = f"Приложение 1, вопрос {number}"
            out.append({"slide": view.position, "title": view.title, "check": name,
                        "kind": "contextual", "detail": f"нет: {label} ({where})"
                        + (f". Модель: {problem}" if problem else ""),
                        "fix": fix})
    return out
