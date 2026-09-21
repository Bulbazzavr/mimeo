"""Модель контента и нормализация входа. Шаг Ш1 плана `PLAN-2.0`.

Здесь только структура, без семантики: разбить вход на типизированные блоки и
сгруппировать их по разделам. Что из этого во что превратится на слайде —
дело `matching.py`.

Формат входа окончательно неизвестен (`OQ-05`), поэтому парсер отделён от
модели: если 15 сентября окажется, что контент приходит иначе, меняется только
`parse_markdown`, а `ContentDoc` и всё за ним остаются на месте.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace

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


# --- картинка, названная прозой (`Z-28a`, `PLAN-7.10`) -----------------

#: Расширения, по которым токен в тексте считается ссылкой на картинку.
#: Список тот же, что у `compose.substitute._IMAGE_TYPES`, минус `svg` и
#: `tiff`: их `replace_picture` принимает, но в прозе такой токен почти
#: наверняка не картинка доклада.
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

#: Токен, похожий на путь к файлу изображения, внутри обычного текста.
#: Ограничители — пробел и типографские знаки, потому что в живой речи путь
#: стоит в скобках, кавычках или перед запятой: «приложу схему
#: (examples/img/pipeline-stages.png), а ещё…».
_PATH_IN_PROSE = re.compile(
    r"""[^\s,;:()«»"'\[\]]+\.(?:png|jpe?g|gif|bmp|webp)""",
    re.IGNORECASE | re.VERBOSE,
)

#: Осколки, остающиеся в тексте после выдирания пути: «приложу  — и столбики».
#: Порядок правил важен, и он проверен растром, а не рассуждением. Первая же
#: сборка дала на слайде «Схему четырёх стадий— и столбики про переполнения,»:
#: путь стоял между двух тире, одиночное правило сняло первое и прилепило
#: второе к слову, а висячая запятая осталась от второго пути в конце фразы
#: (`Z-28a`, журнал `PLAN-7.10`). Поэтому **пара тире снимается раньше
#: одиночного**, а хвостовая пунктуация — последней.
_LEFTOVER_GAPS = (
    # Путь стоял между тире: «стадий — путь — и столбики» → «стадий и столбики».
    (re.compile(r"\s*[—–]\s*[—–]\s*"), " "),
    # Путь был в скобках или кавычках — осталась пустая пара.
    (re.compile(r"[(\[«]\s*[)\]»]"), ""),
    (re.compile(r"\s+([,.;:!?])"), r"\1"),
    (re.compile(r",\s*,"), ","),
    (re.compile(r"\s{2,}"), " "),
    # Путь был в конце фразы, после двоеточия, тире или запятой.
    (re.compile(r"\s*[—–:,]\s*$"), ""),
)


def _tidy(text: str) -> str:
    """Убирает следы вырезанного пути, не трогая сам текст."""
    for pattern, repl in _LEFTOVER_GAPS:
        text = pattern.sub(repl, text)
    return text.strip(" \t—–-").strip()


def resolve_image(ref: str, *bases: str) -> str | None:
    """Где на диске лежит `ref`. `None` — нигде, и это факт, а не догадка.

    Порядок баз фиксирован, потому что от него зависит результат сборки, а
    сборка обязана быть побайтово одинаковой (`ADR-0003`). Проверка именно
    здесь, а не в COMPOSE: иначе PLAN говорит «картинка есть», а COMPOSE
    молча оставляет донорскую — это разные ответы на один вопрос
    (`PLAN-7.10`, проверка 1, находка 2).
    """
    if os.path.isabs(ref):
        return _portable(ref) if os.path.isfile(ref) else None
    for base in bases:
        if not base:
            continue
        candidate = os.path.normpath(os.path.join(base, ref))
        if os.path.isfile(candidate):
            return _portable(candidate)
    return None


def _portable(path: str) -> str:
    """Путь в том виде, в каком его не стыдно положить в `deck-plan.json`.

    Найденный путь абсолютен, а `ref` уезжает в артефакт плана — значит на
    другой машине артефакт вышел бы другим, и побайтовая воспроизводимость
    (`ADR-0003`, критерий 2 ТЗ) сломалась бы на ровном месте. Поэтому путь
    приводится к относительному от текущего каталога и к прямым слэшам:
    `examples/img/pipeline-stages.png` на любой машине.

    Ушёл выше текущего каталога — оставляем абсолютным: соврать про
    расположение хуже, чем признать машинный путь.
    """
    try:
        relative = os.path.relpath(path, os.getcwd())
    except ValueError:                      # другой диск на Windows
        return path.replace(os.sep, "/")
    if relative.startswith(".."):
        return path.replace(os.sep, "/")
    return relative.replace(os.sep, "/")


def extract_images(doc: ContentDoc, *bases: str) -> tuple[ContentDoc, list[str]]:
    """Путь к картинке, названный внутри прозы, становится блоком `image`.

    Зачем это вообще нужно: ТЗ говорит, что вход — **сплошная
    неструктурированная проза**, а разметку `![](…)` понимал только
    размеченный вход. Замер: по корпусу ноль блоков `image` на шести входах и
    ноль заливок на 84 парах вход × шаблон — то есть на том входе, который
    заявлен в ТЗ, встраивания изображений у нас не было вовсе
    (`WORKLOG/2026-09-21-z28a-baseline.md`).

    **Признак — расширение плюс файл на диске.** Одного расширения мало:
    «смотри в `config/prose.json`» тоже похоже на путь. Существование файла —
    это факт, а не правдоподобие, и оно же не даёт выдумать содержание
    (`CTX-NARRATIVE`).

    **Упомянутый, но не найденный путь остаётся текстом** и попадает в
    предупреждения: вырезать его значит потерять содержание ради красоты.
    """
    notes: list[str] = []
    sections: list[ContentSection] = []
    counter = 0
    for section in doc.sections:
        blocks: list[ContentBlock] = []
        for block in section.blocks:
            if block.kind != "paragraph" or not block.text:
                blocks.append(block)
                continue
            found: list[tuple[str, str]] = []
            missing: list[str] = []
            for match in _PATH_IN_PROSE.finditer(block.text):
                ref = match.group(0)
                where = resolve_image(ref, *bases)
                if where:
                    found.append((ref, where))
                else:
                    missing.append(ref)
            for ref in missing:
                notes.append(
                    f"Упомянут файл «{ref}», но его нет на диске — "
                    f"оставлен текстом, картинка не вставлена."
                )
            if not found:
                blocks.append(block)
                continue
            text = block.text
            for ref, _where in found:
                text = text.replace(ref, " ")
            text = _tidy(text)
            if text:
                blocks.append(replace(block, text=text))
            for _ref, where in found:
                counter += 1
                blocks.append(
                    ContentBlock(
                        id=f"{block.id}i{counter:02d}",
                        kind="image",
                        ref=where,
                        text="",
                    )
                )
            notes.append(
                f"Картинок, названных прозой: {len(found)} "
                f"({', '.join(os.path.basename(w) for _r, w in found)})."
                if len(found) > 1
                else f"Картинка, названная прозой: {os.path.basename(found[0][1])}."
            )
        sections.append(ContentSection(id=section.id, heading=section.heading,
                                       blocks=tuple(blocks)))
    if counter == 0 and not notes:
        return doc, []
    return (
        ContentDoc(name=doc.name, title=doc.title, sections=sections,
                   origin=doc.origin, notes=doc.notes),
        notes,
    )
