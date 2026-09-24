"""Подпись шаблона: оглавление и финал по собственному тексту донора.

Задача `Z-58`, план `PLAN-9.0`, часть Б. До 24 сентября kind `agenda` не ставило
ни одно правило `_classify`, а `closing` получал только последний слайд шаблона.
Из 3 оглавлений и 10 финалов, подписанных самими шаблонами на 14 образцах, код
узнавал **0 и 0** (зонд 23 сентября насчитал финалов 9: его список слов не знал
«Thanks» и «Questions?») — и на выданном VK Tech наш текст вставал на макеты «Спасибо
за внимание!» посреди колоды, а в двух вариантах из трёх ими же открывалась
колода (`WORKLOG/2026-09-24-z58-baseline.md`).

Геометрия этого не различит: финал «Спасибо» на фоне фотографии выглядит как
`image_full`, оглавление из четырёх пунктов — как `cards`. Различает подпись,
которую дизайнер поставил сам, поэтому она проверяется **раньше** геометрии.

Подпись ищется в каждой текстовой фигуре **порознь и только в короткой**:
все 12 подписей замера стоят в фигурах длиной 3–23 знака, 10 из них — в
заголовке. Слово из абзаца подписью не считается — иначе «благодаря» в любом
тексте делало бы слайд финалом.

Слова живут в `config/kinds.json`, а не здесь: «Содержание» и «Спасибо» —
соглашение языка, а не одного шаблона (`PLAN-9.0`, проверка 2, вопрос 6).
Шаблон на другом языке или без подписи уходит в структурные правила, как
раньше.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass

from .shapes import ShapeObs

#: Встроенные значения — на случай, если конфиг не прочитан. Те же, что в
#: `config/kinds.json` версии 1.0; расхождение ловит тест.
_AGENDA = (
    r"\bсодержание\b",
    r"\bоглавление\b",
    r"\bповестка\b",
    r"\bagenda\b",
    r"\bcontents\b",
)
_CLOSING = (
    r"\bспасибо\b",
    r"\bблагодар(?!я)",
    r"\bthank\s*you\b",
    r"\bthanks\b",
    r"\bq\s*&\s*a\b",
    r"\bвопросы\s*\?",
    r"\bвопросы\s+и\s+ответы\b",
    r"\bquestions\s*\?",
)

#: Порядок проверки. Оглавление раньше финала — только ради детерминированности:
#: на 14 шаблонах нет донора, где бы стояли обе подписи.
KINDS = ("agenda", "closing")


@dataclass(frozen=True)
class CaptionConfig:
    agenda: tuple[re.Pattern[str], ...]
    closing: tuple[re.Pattern[str], ...]
    loaded: bool = False
    source: str = ""

    def patterns(self, kind: str) -> tuple[re.Pattern[str], ...]:
        return self.agenda if kind == "agenda" else self.closing


def _compile(items: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(x, re.IGNORECASE) for x in items)


def builtin() -> CaptionConfig:
    return CaptionConfig(agenda=_compile(_AGENDA), closing=_compile(_CLOSING))


def config_path() -> str:
    """`config/kinds.json` рядом с пакетом: `mimeo/` лежит в корне репозитория.
    То же допущение, что в `analyze/typeface.py` (`ADR-0022`)."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    return os.path.join(root, "config", "kinds.json")


def load_config(path: str | None = None) -> CaptionConfig:
    """Конфиг подписей. Не прочитался или выражение в нём негодно — встроенные
    значения и `loaded=False`: библиотека раскладок скажет об этом вслух."""
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        lists = {k: raw.get(k) for k in KINDS}
        if not all(isinstance(v, list) and v and all(isinstance(x, str) for x in v)
                   for v in lists.values()):
            return builtin()
        return CaptionConfig(
            agenda=_compile(lists["agenda"]),
            closing=_compile(lists["closing"]),
            loaded=True,
            source=path,
        )
    except (OSError, ValueError, AttributeError, re.error):
        return builtin()


def caption_kind(shapes: Iterable[ShapeObs], config: CaptionConfig, limit: int) -> str | None:
    """`agenda` или `closing`, если донор так подписан, иначе `None`.

    `limit` — длина, до которой текст фигуры считается подписью, а не абзацем;
    её задаёт `analyze/patterns.py` (`_SHORT_TEXT`), чтобы порог был один.
    """
    short = [s.text for s in shapes if s.has_text and len(s.text) <= limit]
    for kind in KINDS:
        if any(p.search(text) for p in config.patterns(kind) for text in short):
            return kind
    return None
