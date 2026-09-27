"""Таблицы и диаграммы из текста — `Z-32`, `PLAN-10.0`, Ш2–Ш3; `ADR-0026`.

Какой ряд чисел просится в диаграмму и что сравнить таблицей — смысл, и его
находит модель (`ADR-0023`): она кладёт данные в поля `table` и `chart` ответа.
Здесь — форма: разобрать эти поля, проверить, что их можно нарисовать, и
перевести числа текста («41 200», «3,9», «14%») в числа диаграммы. Нативный
объект PowerPoint из этих данных строит сборка (`compose/visual.py`).

Потолки — из Приложения 1 ТЗ, группа «Плотность»: «таблица больше 7 строк или
5 колонок», «больше 5 серий на диаграмме». Это правила читаемости заказчика, а
не настройка, поэтому они в коде, как схема ответа (`ADR-0022`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

#: Таблица: колонок — от двух (одна колонка — это список) до пяти; строк вместе
#: с шапкой — до семи.
TABLE_MIN_COLS = 2
TABLE_MAX_COLS = 5
TABLE_MAX_ROWS = 7

#: Диаграмма: типы, которые умеет сборка, и границы ряда.
CHART_TYPES = ("column", "bar", "line", "pie")
CHART_MIN_POINTS = 2
CHART_MAX_POINTS = 12
CHART_MAX_SERIES = 5

#: Множитель словом после числа («1,2 млн»): число берётся как есть, слово — в
#: подпись значения. Умножать нельзя: подпись на слайде обязана совпасть с
#: текстом автора (Приложение 1 ТЗ, вопрос 4).
_MULTIPLIERS = ("тыс", "млн", "млрд", "трлн")
_NUMBER = re.compile(r"^[-+]?\d+(?:\.\d+)?$")
_SPACES = re.compile(r"[\s   ]")


def number(text: str) -> float | None:
    """Число из записи текста: «41 200» → 41200, «3,9» → 3.9, «14%» → 14,
    «1,2 млн» → 1.2. Не число — `None`: «около 40», «пять».

    Запятая — десятичная (текст русский); разряды — пробелом любого вида."""
    if not isinstance(text, str):
        return None
    s = text.strip().lower().replace("−", "-").replace("–", "-")
    for word in _MULTIPLIERS:
        s = re.sub(rf"\s*{word}\.?$", "", s)
    s = s.rstrip("%₽$€ ").lstrip("$€₽ ")
    s = _SPACES.sub("", s)
    if s.count(",") == 1 and "." not in s:
        s = s.replace(",", ".")
    if not _NUMBER.match(s):
        return None
    return float(s)


@dataclass(frozen=True)
class TableData:
    """Таблица: шапка и строки, ячейки — строками, как в тексте."""

    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def strings(self) -> tuple[str, ...]:
        """Всё, что окажется на слайде текстом, — для проверок чисел и слов."""
        return self.header + tuple(c for row in self.rows for c in row)

    def as_items(self) -> tuple[str, ...]:
        """Та же таблица списком — когда места под неё в шаблоне нет."""
        items = []
        for row in self.rows:
            cells = [f"{h}: {c}" if h and c and i else c for i, (h, c) in enumerate(zip(self.header, row))]
            items.append(" — ".join(c for c in cells if c))
        return tuple(i for i in items if i)

    def to_json(self) -> dict:
        return {"header": list(self.header), "rows": [list(r) for r in self.rows]}


@dataclass(frozen=True)
class Series:
    name: str
    values: tuple[float, ...]
    #: Значения, как они записаны в тексте: «41 200». Подписи данных на слайде
    #: берут отсюда формат, а проверки — сами числа.
    labels: tuple[str, ...]


@dataclass(frozen=True)
class ChartData:
    type: str
    unit: str
    categories: tuple[str, ...]
    series: tuple[Series, ...]

    def strings(self) -> tuple[str, ...]:
        out = [self.unit] if self.unit else []
        out += list(self.categories)
        for s in self.series:
            out.append(s.name)
            out += list(s.labels)
        return tuple(out)

    def as_items(self) -> tuple[str, ...]:
        unit = f" {self.unit}" if self.unit else ""
        if len(self.series) == 1:
            return tuple(f"{c} — {v}{unit}" for c, v in zip(self.categories, self.series[0].labels))
        return tuple(
            f"{c}: " + "; ".join(f"{s.name} — {s.labels[i]}{unit}" for s in self.series)
            for i, c in enumerate(self.categories)
        )

    def to_json(self) -> dict:
        return {
            "type": self.type,
            "unit": self.unit,
            "categories": list(self.categories),
            "series": [{"name": s.name, "values": list(s.values), "labels": list(s.labels)}
                       for s in self.series],
        }


def _strings(raw) -> list[str] | None:
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        return None
    return [x.strip() for x in raw]


def table_from(raw) -> tuple[TableData | None, str | None]:
    """Таблица из поля ответа: (данные, None) или (None, что не так — словами)."""
    if not isinstance(raw, dict):
        return None, "таблица — не объект"
    header = _strings(raw.get("header"))
    rows_raw = raw.get("rows")
    if header is None or not isinstance(rows_raw, list):
        return None, "у таблицы нет шапки или строк"
    if not TABLE_MIN_COLS <= len(header) <= TABLE_MAX_COLS:
        return None, f"колонок {len(header)}, а нужно от {TABLE_MIN_COLS} до {TABLE_MAX_COLS}"
    if not rows_raw or len(rows_raw) + 1 > TABLE_MAX_ROWS:
        return None, f"строк с шапкой {len(rows_raw) + 1}, а нужно от 2 до {TABLE_MAX_ROWS}"
    rows = []
    for n, row in enumerate(rows_raw, 1):
        cells = _strings(row)
        if cells is None:
            return None, f"строка {n} — не список строк"
        if len(cells) > len(header):
            return None, f"в строке {n} ячеек {len(cells)} при {len(header)} колонках"
        # Короче шапки — недостающие ячейки пустые: пустая ячейка ничего не
        # выдумывает, а отвергнуть таблицу за неё — потерять её числа.
        rows.append(tuple(cells + [""] * (len(header) - len(cells))))
    if not any(c for row in rows for c in row):
        return None, "таблица пустая"
    return TableData(tuple(header), tuple(rows)), None


def chart_from(raw) -> tuple[ChartData | None, str | None]:
    """Диаграмма из поля ответа: (данные, None) или (None, что не так)."""
    if not isinstance(raw, dict):
        return None, "диаграмма — не объект"
    kind = raw.get("type")
    if kind not in CHART_TYPES:
        return None, f"тип диаграммы {kind!r} не из {', '.join(CHART_TYPES)}"
    categories = _strings(raw.get("categories"))
    if categories is None or not CHART_MIN_POINTS <= len(categories) <= CHART_MAX_POINTS:
        n = len(categories) if categories is not None else 0
        return None, f"подписей {n}, а нужно от {CHART_MIN_POINTS} до {CHART_MAX_POINTS}"
    series_raw = raw.get("series")
    if not isinstance(series_raw, list) or not 1 <= len(series_raw) <= CHART_MAX_SERIES:
        return None, f"рядов должно быть от 1 до {CHART_MAX_SERIES}"
    if kind == "pie" and len(series_raw) != 1:
        return None, "у круговой диаграммы ряд один"
    series = []
    for n, item in enumerate(series_raw, 1):
        if not isinstance(item, dict):
            return None, f"ряд {n} — не объект"
        labels = _strings(item.get("values"))
        if labels is None or len(labels) != len(categories):
            got = len(labels) if labels is not None else 0
            return None, f"в ряду {n} значений {got} при {len(categories)} подписях"
        values = [number(v) for v in labels]
        bad = [lab for lab, v in zip(labels, values) if v is None]
        if bad:
            return None, f"в ряду {n} не числа: {bad}"
        if kind == "pie" and (min(values) < 0 or sum(values) <= 0):
            return None, "у круговой диаграммы доли неотрицательны и не все нули"
        name = item.get("name") if isinstance(item.get("name"), str) else ""
        series.append(Series(name.strip(), tuple(values), tuple(labels)))
    unit = raw.get("unit") if isinstance(raw.get("unit"), str) else ""
    return ChartData(kind, unit.strip(), tuple(categories), tuple(series)), None


def from_slide(slide: dict) -> tuple[object | None, list[str]]:
    """Таблица или диаграмма слайда ответа модели и что с ними не так.
    Берётся только та, что совпала с типом слайда: место под них одно."""
    problems: list[str] = []
    table = chart = None
    kind = slide.get("kind")
    # Таблица — только у слайда table, диаграмма — у chart: так велит и схема
    # ответа. У чужого типа данные не берутся — страховка на случай сервера,
    # который схему держит нестрого (у брифа модель заполняла их у всех
    # слайдов выдуманными рядами, `PLAN-10.0`, Ш7).
    for field, own, what in (("table", "table", "таблица"), ("chart", "chart", "диаграмма")):
        if slide.get(field) is not None and kind != own:
            problems.append(f"{what} у слайда типа {kind} — не берётся")
    if slide.get("table") is not None and kind == "table":
        table, why = table_from(slide["table"])
        if why:
            problems.append(f"таблица: {why}")
    if slide.get("chart") is not None and kind == "chart":
        chart, why = chart_from(slide["chart"])
        if why:
            problems.append(f"диаграмма: {why}")
    return table or chart, problems


# --- Markdown-таблица входа -------------------------------------------------

_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_RULE = re.compile(r"^:?-{3,}:?$")


def markdown_table(lines: list[str]) -> TableData | None:
    """Таблица из строк `| a | b |` размеченного входа. Не складывается в
    таблицу, которую можно нарисовать, — `None`: разбор сделает из неё список."""
    rows = []
    for line in lines:
        cells = [c.strip() for c in _CELL_SPLIT.split(line.strip().strip("|"))]
        if all(_RULE.match(c) for c in cells if c) and any(cells):
            continue                        # строка-разделитель под шапкой
        rows.append(cells)
    if len(rows) < 2:
        return None
    data, why = table_from({"header": rows[0], "rows": rows[1:]})
    return data if why is None else None


# --- Место под таблицу и диаграмму ------------------------------------------

def floor(slide_size: tuple[int, int] | None, ratio: float) -> int | None:
    """Меньшая сторона места под таблицу или диаграмму не короче этого, EMU."""
    return int(ratio * min(slide_size)) if slide_size else None


def with_floor(doc, min_side: int | None):
    """Документ, где у блоков таблиц и диаграмм задана наименьшая сторона места."""
    if min_side is None:
        return doc
    sections = []
    for s in doc.sections:
        if any(b.kind in ("table", "chart") and b.data is not None for b in s.blocks):
            s = replace(s, blocks=tuple(
                replace(b, min_side=min_side) if b.kind in ("table", "chart") and b.data is not None else b
                for b in s.blocks))
        sections.append(s)
    return replace(doc, sections=sections)


def without_hosts(doc, has_host) -> tuple[object, list[str]]:
    """Таблицы и диаграммы, которым в шаблоне нет места ни в одной раскладке,
    становятся списком: содержание не теряется, и об этом сказано.

    `has_host(block)` — есть ли у шаблона хоть одно место под этот блок."""
    from .content import ContentBlock

    notes: list[str] = []
    sections = []
    for s in doc.sections:
        blocks = []
        for b in s.blocks:
            if b.kind in ("table", "chart") and b.data is not None and not has_host(b):
                items = b.data.as_items()
                what = "Таблица" if b.kind == "table" else "Диаграмма"
                notes.append(f"{what} «{s.heading or s.id}»: в раскладках шаблона нет места под неё "
                             f"— данные поставлены списком (Z-32).")
                blocks.append(ContentBlock(id=b.id, kind="list", items=items, text=" ".join(items)))
                continue
            blocks.append(b)
        sections.append(replace(s, blocks=tuple(blocks)))
    return replace(doc, sections=sections), notes
