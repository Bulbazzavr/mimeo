"""Модель строит колоду из текста до вёрстки — `ADR-0023`, `PLAN-9.0`, `Z-57`.

Пока здесь то, что даёт шаг Ш1: формулировки промпта из `config/outline.json`
и схема ответа. Схема — контракт, она в коде; формулировки — настройка, они в
конфиге (`ADR-0022`). Промпт снят замером дословно
(`WORKLOG/2026-09-24-z57-prompt.md`) и заморожен до замера Ш9: любая его правка —
перемер на трёх текстах корпуса (`ADR-0023`, п. 7).
"""

from __future__ import annotations

import json
import os

CONFIG_NAME = "outline.json"

#: Режимы текста (`ADR-0023`, п. 4): «оставить мой текст» и «доработать текст».
MODES = ("keep", "improve")

#: Словарь типов слайда — словарь кода, а не шаблона (`ADR-0023`, п. 2). Имена те
#: же, что у kind'ов макетов (`contracts/pattern-library.schema.json`), но без
#: chart, image_full, timeline и other: промпт с ними не мерился.
KINDS = (
    "cover", "agenda", "section", "text", "bullets", "cards", "two_column",
    "metric", "quote", "table", "image_text", "closing",
)

#: Роли слайда пишутся только в `outline.json` рядом с колодой: омоним «модель»
#: их путает (замер 23 сентября, часть 4), сверка с эталоном — дело `Z-37`.
ROLES = (
    "обложка", "проблема", "решение", "продукт", "рынок", "тяга", "бизнес-модель",
    "конкуренты", "финансы", "команда", "риски", "планы", "просьба", "прочее",
)

#: Что считается годным ответом по форме. Смысл — числа, картинки, латиницу,
#: дословность — проверяет код поверх схемы (`ADR-0023`, п. 5).
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "role": {"type": "string", "enum": list(ROLES)},
                    "theses": {"type": "array", "items": {"type": "string"}},
                    "image_idea": {"type": "string"},
                    "images": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["heading", "kind", "role", "theses", "image_idea", "images"],
            },
        },
        "missing_roles": {"type": "array", "items": {"type": "string", "enum": list(ROLES)}},
    },
    "required": ["slides", "missing_roles"],
}

#: Запасные формулировки — байт в байт те, что в `config/outline.json`; сверяет
#: тест. Без файла движок обязан работать, как с `config/prose.json`.
_SYSTEM = (
    "Ты раскладываешь текст автора по слайдам презентации. Отвечай строго JSON.\n"
    "Реши, сколько нужно слайдов (от {slides_min} до {slides_max}; если содержания меньше"
    " — меньше, ничего не выдумывая), в каком порядке они идут и что на каждом.\n"
    "Для каждого слайда:\n"
    "- heading — заголовок от 2 до 6 слов, в именительном падеже;\n"
    "{theses}- kind — тип слайда: cover (обложка), agenda (оглавление), section"
    " (разделитель), text (абзац), bullets (список), cards (равные пункты), two_column"
    " (сравнение), metric (крупное число), quote (цитата), table (таблица), image_text"
    " (картинка с текстом), closing (финал); оглавление и финал — только из того, что"
    " есть в тексте и на слайдах колоды;\n"
    "- role — роль слайда, одна из: обложка, проблема, решение, продукт, рынок, тяга,"
    " бизнес-модель, конкуренты, финансы, команда, риски, планы, просьба, прочее;\n"
    "- image_idea — если слайду по смыслу нужна иллюстрация, одной фразой опиши, что на"
    " ней изображено, без указаний стиля; иначе пустая строка;\n"
    "- images — пути к картинкам из текста, которые относятся к этому слайду, дословно"
    " как в тексте; иначе пустой список.\n"
    "Правила: не добавляй фактов, чисел и названий, которых нет в тексте. Каждое число из"
    " текста должно попасть на какой-нибудь слайд. Каждый путь к картинке — ровно на один"
    " слайд. В missing_roles перечисли роли, которых в тексте нет."
)
_THESES = {
    "keep": "- theses — от 1 до 4 тезисов: ДОСЛОВНЫЕ фразы или части фраз из текста"
    " автора, без перефразирования;\n",
    "improve": "- theses — от 1 до 4 тезисов: короткие законченные фразы до 90 знаков;"
    " можно переформулировать для ясности, но только то, что есть в тексте;\n",
}


def config_path() -> str:
    """`config/outline.json` рядом с пакетом: `mimeo/` лежит в корне репозитория."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(os.path.dirname(here)), "config", CONFIG_NAME)


def load_config(path: str | None = None) -> tuple[str, dict[str, str], bool]:
    """Читает `config/outline.json`: общий текст, блоки тезисов по режимам и
    признак «прочитан». Нет файла — встроенные значения и `False`: отличать
    «прочитано» от «работают запасные» обязан сам загрузчик, а не молчание."""
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return _SYSTEM, dict(_THESES), False
    return "".join(data["system"]), dict(data["theses"]), True


def system_prompt(mode: str, slides_min: int, slides_max: int, path: str | None = None) -> str:
    """Системный промпт режима `mode` с рамками объёма."""
    if mode not in MODES:
        raise ValueError(f"режим текста {mode!r}: ждём один из {MODES}")
    common, theses, _ = load_config(path)
    return (
        common.replace("{slides_min}", str(slides_min))
        .replace("{slides_max}", str(slides_max))
        .replace("{theses}", theses[mode])
    )
