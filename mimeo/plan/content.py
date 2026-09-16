"""Модель контента и нормализация входа. Шаг Ш1 плана `PLAN-2.0`.

Здесь только структура, без семантики: разбить вход на типизированные блоки и
сгруппировать их по разделам. Что из этого во что превратится на слайде —
дело `matching.py`.

Формат входа окончательно неизвестен (`OQ-05`), поэтому парсер отделён от
модели: если 15 сентября окажется, что контент приходит иначе, меняется только
`parse_markdown`, а `ContentDoc` и всё за ним остаются на месте.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Строка, состоящая из числа с обрамлением. Та же логика, что в DOM-TEXT §8,
#: но здесь применяется к строке входного текста, а не к тексту фигуры.
_NUMERIC = re.compile(r"^[\s\d.,%+\-–—×xX/№$€₽]+$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")

#: Длиннее этого числовая строка перестаёт быть метрикой и становится текстом.
_METRIC_MAX_LEN = 14


def is_numeric_line(text: str) -> bool:
    t = text.strip()
    return bool(t) and len(t) <= _METRIC_MAX_LEN and bool(_NUMERIC.match(t)) and any(
        c.isdigit() for c in t
    )


@dataclass(frozen=True)
class ContentBlock:
    id: str
    kind: str            # paragraph | list | metric | quote | table | image
    text: str = ""
    items: tuple[str, ...] = ()
    value: str | None = None      # metric: само число
    label: str | None = None      # metric: подпись под числом
    ref: str | None = None        # image: путь

    @property
    def units(self) -> int:
        """Сколько содержательных единиц несёт блок."""
        return len(self.items) if self.kind == "list" else 1

    @property
    def length(self) -> int:
        if self.kind == "list":
            return sum(len(i) for i in self.items)
        if self.kind == "metric":
            return len(self.value or "") + len(self.label or "")
        return len(self.text)

    def preview(self, limit: int = 48) -> str:
        raw = self.text or (self.items[0] if self.items else "") or (self.value or "") or (self.ref or "")
        raw = " ".join(raw.split())
        return raw[:limit]


@dataclass(frozen=True)
class ContentSection:
    id: str
    heading: str | None
    blocks: tuple[ContentBlock, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.blocks and not self.heading

    def kinds(self) -> set[str]:
        return {b.kind for b in self.blocks}


@dataclass
class ContentDoc:
    name: str
    title: str | None = None
    sections: list[ContentSection] = field(default_factory=list)
    #: Исходный текст до сегментации прозы (`prose.py`). Нужен `Z-08`: модели
    #: отдаётся проза, а не наша нарезка. У размеченного входа пуст.
    origin: str = ""
    #: Что сегментатор сделал с входом. Уходит в предупреждения плана, чтобы
    #: «вход распознан как проза» было видно, а не подразумевалось.
    notes: tuple[str, ...] = ()

    @property
    def block_count(self) -> int:
        return sum(len(s.blocks) for s in self.sections)


# --- разбор ------------------------------------------------------------


class _Builder:
    def __init__(self, name: str) -> None:
        self.name = name
        self.title: str | None = None
        self.sections: list[ContentSection] = []
        self._heading: str | None = None
        self._blocks: list[ContentBlock] = []
        self._n = 0

    def _next_id(self) -> str:
        self._n += 1
        return f"b{self._n:02d}"

    def add(self, **kwargs) -> None:
        self._blocks.append(ContentBlock(id=self._next_id(), **kwargs))

    def close_section(self) -> None:
        if self._heading is not None or self._blocks:
            self.sections.append(
                ContentSection(
                    id=f"sec{len(self.sections) + 1:02d}",
                    heading=self._heading,
                    blocks=_attach_metric_labels(tuple(self._blocks)),
                )
            )
        self._heading, self._blocks = None, []

    def open_section(self, heading: str) -> None:
        self.close_section()
        self._heading = heading

    def build(self) -> ContentDoc:
        self.close_section()
        return ContentDoc(name=self.name, title=self.title, sections=self.sections)


#: Длиннее этого абзац под числом — самостоятельный текст, а не подпись.
_METRIC_LABEL_MAX = 60


def _attach_metric_labels(blocks: tuple[ContentBlock, ...]) -> tuple[ContentBlock, ...]:
    """Прицепляет короткий абзац под голым числом как подпись к метрике.

    Люди пишут плашку с числом через пустую строку:

        0.82

        Точность прогноза

    Формально это два блока, по смыслу — один. Абзац длиннее 60 знаков уже
    самостоятельный текст и не поглощается.
    """
    out: list[ContentBlock] = []
    skip = -1
    for i, block in enumerate(blocks):
        if i == skip:
            continue
        following = blocks[i + 1] if i + 1 < len(blocks) else None
        if (
            block.kind == "metric"
            and block.label is None
            and following is not None
            and following.kind == "paragraph"
            and len(following.text) <= _METRIC_LABEL_MAX
        ):
            out.append(
                ContentBlock(
                    id=block.id,
                    kind="metric",
                    text=f"{block.value} {following.text}",
                    value=block.value,
                    label=following.text,
                )
            )
            skip = i + 1
            continue
        out.append(block)
    return tuple(out)


def _flush_paragraph(builder: _Builder, lines: list[str]) -> None:
    """Абзац из накопленных строк. Отдельно ловится метрика: короткая числовая
    строка плюс подпись под ней — это не абзац, а плашка с числом."""
    if not lines:
        return
    text = " ".join(" ".join(lines).split())
    if not text:
        lines.clear()
        return
    if is_numeric_line(lines[0]) and len(lines) <= 2:
        label = " ".join(lines[1].split()) if len(lines) > 1 else None
        builder.add(kind="metric", value=lines[0].strip(), label=label, text=text)
    else:
        builder.add(kind="paragraph", text=text)
    lines.clear()


def parse_markdown(source: str, name: str = "content") -> ContentDoc:
    """Разбирает markdown или простой текст. Простой текст — частный случай:
    без заголовков он станет одним безымянным разделом из абзацев."""
    builder = _Builder(name)
    paragraph: list[str] = []
    items: list[str] = []
    quote: list[str] = []
    table: list[str] = []

    def flush_all() -> None:
        _flush_paragraph(builder, paragraph)
        if items:
            builder.add(kind="list", items=tuple(items), text=" ".join(items))
            items.clear()
        if quote:
            builder.add(kind="quote", text=" ".join(" ".join(quote).split()))
            quote.clear()
        if table:
            builder.add(kind="table", text=" ".join(table), items=tuple(table))
            table.clear()

    for raw in source.splitlines():
        line = raw.rstrip()

        if not line.strip():
            flush_all()
            continue

        heading = _HEADING.match(line)
        if heading:
            flush_all()
            level, text = len(heading.group(1)), heading.group(2).strip()
            if level == 1 and builder.title is None and not builder.sections:
                builder.title = text
                builder.open_section(text)
            else:
                builder.open_section(text)
            continue

        image = _IMAGE.match(line)
        if image:
            flush_all()
            builder.add(kind="image", ref=image.group(2), text=image.group(1))
            continue

        if _TABLE_ROW.match(line):
            _flush_paragraph(builder, paragraph)
            table.append(line.strip())
            continue

        quoted = _QUOTE.match(line)
        if quoted:
            _flush_paragraph(builder, paragraph)
            quote.append(quoted.group(1))
            continue

        bullet = _BULLET.match(line) or _ORDERED.match(line)
        if bullet:
            _flush_paragraph(builder, paragraph)
            items.append(" ".join(bullet.group(1).split()))
            continue

        if items or quote or table:
            flush_all()
        paragraph.append(line)

    flush_all()
    return builder.build()


def load(path: str, name: str | None = None) -> ContentDoc:
    with open(path, encoding="utf-8") as fh:
        return parse_markdown(fh.read(), name or path)
