"""Стадия PLAN: контент + артефакты разбора -> план колоды.

Единственное место, где участвует языковая модель (`ADR-0002`). При этом путь
без модели существует и полноценен (`ADR-0009`): `deterministic.plan_deck`
строит план целиком офлайн.

Этот пакет не знает про OOXML: на вход приходят артефакты стадии ANALYZE в виде
моделей, на выход уходит план. См. таблицу границ модулей в `ARCH`.

`load_content` — вход стадии: разбор разметки (`content.py`) и, если вход
оказался сплошной прозой, сегментация (`prose.py`, `Z-25`). Разбор без
сегментации — `load_markdown`.
"""

from __future__ import annotations

from ..model import DeckPlan
from .content import ContentDoc, parse_markdown
from .content import load as load_markdown
from .deterministic import plan_deck
from .matching import Match, rank
from .prose import ProseConfig, load_content, restructure

__all__ = [
    "ContentDoc",
    "DeckPlan",
    "Match",
    "ProseConfig",
    "load_content",
    "load_markdown",
    "parse_markdown",
    "plan_deck",
    "rank",
    "restructure",
]
