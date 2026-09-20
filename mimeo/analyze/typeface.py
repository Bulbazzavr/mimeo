"""Гарнитуры, не предназначенные для прозы. Задача `Z-43`, план `PLAN-7.3`.

Отвечает на один вопрос: **годится ли гарнитура этого слота под обычный текст
доклада.** Негодных видов два, и опознаются они по-разному.

* **Моноширинная** — только по имени. Родные признаки OOXML проверены по
  четырнадцати шаблонам и негодны: `pitchFamily` не записан у обеих найденных
  `Consolas`, а там, где записан, называет `Courier New` пропорциональным, а
  `Arial` — моноширинным (`DOM-TEXT §11`). Правило сверяет **слова** имени со
  списком, а не подстроки: иначе `Monotype Corsiva` станет моноширинной.
* **Пиктограммная** — по тексту донора, без имён вовсе. Знаки таких шрифтов
  лежат в области частного использования Unicode, и фигура из неё и состоит
  (`DOM-TEXT §12`). Имена нужны только старому классу на ASCII —
  `Wingdings`, `Webdings`.

Признак считается **по фигуре**, а не по прогону (`DOM-TEXT §8`): гарнитура
берётся у доминирующего прогона, текст — весь, какой в фигуре есть.

Списки живут в `config/typography.json`, а не здесь: это данные о мире, их
правят (`ADR-0022`, ТЗ раздел 4). Отсутствие файла — не ошибка, а работа на
встроенных значениях, и вызывающий узнаёт об этом по `loaded`, а не по молчанию.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

#: Значения поля `Slot.typeface_kind`. `None` — отдельно от них: «судить не по
#: чему», то есть у фигуры нет ни одного прогона. Правило проекта: измеритель
#: обязан отличать «проверено и чисто» от «проверить не смог».
PROSE = "prose"
MONO = "mono"
ICON = "icon"

_MONO_TOKENS = frozenset(
    ("mono", "monospace", "monospaced", "code", "courier", "console",
     "typewriter", "teletype")
)
_MONO_NAMES = frozenset(
    ("consolas", "monaco", "menlo", "inconsolata", "hack", "iosevka",
     "terminal", "fixedsys", "ocra", "ocrb")
)
_ICON_TOKENS = frozenset(
    ("wingdings", "webdings", "dingbats", "dingbat", "glyphicons", "awesome",
     "icons", "iconfont")
)
_ICON_NAMES = frozenset(("symbol", "marlett", "linecons"))
_ICON_PUA_RATIO = 0.5


@dataclass(frozen=True)
class TypefaceConfig:
    mono_tokens: frozenset[str] = _MONO_TOKENS
    mono_names: frozenset[str] = _MONO_NAMES
    icon_tokens: frozenset[str] = _ICON_TOKENS
    icon_names: frozenset[str] = _ICON_NAMES
    icon_pua_ratio: float = _ICON_PUA_RATIO
    loaded: bool = False
    source: str = "встроенные значения"


def config_path() -> str:
    """`config/typography.json` рядом с пакетом: `mimeo/` лежит в корне
    репозитория. То же допущение, что в `plan/prose.py` (`ADR-0022`)."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    return os.path.join(root, "config", "typography.json")


def load_config(path: str | None = None) -> TypefaceConfig:
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return TypefaceConfig()

    def words(key: str, fallback: frozenset[str]) -> frozenset[str]:
        value = raw.get(key)
        if not isinstance(value, list) or not value:
            return fallback
        return frozenset(str(x).lower() for x in value)

    ratio = raw.get("icon_pua_ratio")
    return TypefaceConfig(
        mono_tokens=words("mono_tokens", _MONO_TOKENS),
        mono_names=words("mono_names", _MONO_NAMES),
        icon_tokens=words("icon_tokens", _ICON_TOKENS),
        icon_names=words("icon_names", _ICON_NAMES),
        icon_pua_ratio=float(ratio) if isinstance(ratio, (int, float)) else _ICON_PUA_RATIO,
        loaded=True,
        source=path,
    )


def tokens(name: str | None) -> tuple[str, ...]:
    """Слова имени гарнитуры: по разделителям и по горбатому регистру.

    `Courier New` → `courier`, `new`. `JetBrainsMono` → `jet`, `brains`, `mono`.
    `Monotype Corsiva` → `monotype`, `corsiva`, и ни одно слово не равно `mono`:
    ради этого сверка идёт по словам, а не по подстроке.
    """
    out: list[str] = []
    cur = ""
    for ch in name or "":
        if not ch.isalnum():
            if cur:
                out.append(cur)
            cur = ""
            continue
        if ch.isupper() and cur and cur[-1].islower():
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return tuple(t.lower() for t in out)


def _pua(ch: str) -> bool:
    """Знак из области частного использования Unicode. Три диапазона:
    основной и планы 15–16 целиком."""
    o = ord(ch)
    return 0xE000 <= o <= 0xF8FF or 0xF0000 <= o <= 0xFFFFD or 0x100000 <= o <= 0x10FFFD


def pua_ratio(text: str) -> float:
    """Доля знаков области частного использования среди непробельных.

    Фигура без непробельных знаков даёт 0.0, а не деление на ноль: пустая
    фигура — не пиктограммная.
    """
    solid = [c for c in text or "" if not c.isspace()]
    if not solid:
        return 0.0
    return sum(1 for c in solid if _pua(c)) / len(solid)


def classify(
    latin: str | None, text: str = "", cfg: TypefaceConfig | None = None
) -> str:
    """Вид гарнитуры слота: `prose`, `mono` или `icon`.

    Пиктограммная проверяется первой: `linecons` есть и в списке имён, и в
    тексте, а вот `FontAwesome` в чужом шаблоне может называться как угодно —
    текст надёжнее.
    """
    cfg = cfg or load_config()
    parts = tokens(latin)
    words = set(parts)
    flat = "".join(parts)

    if pua_ratio(text) >= cfg.icon_pua_ratio:
        return ICON
    if words & cfg.icon_tokens or flat in cfg.icon_names:
        return ICON
    if words & cfg.mono_tokens or flat in cfg.mono_names:
        return MONO
    return PROSE
