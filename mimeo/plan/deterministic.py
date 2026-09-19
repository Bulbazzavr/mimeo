"""Планировщик без модели. Шаг Ш4 плана `PLAN-2.0`, обоснование в `ADR-0009`.

Строит полный `deck-plan.json`, ни разу не обратившись к сети. Нужен по трём
причинам, и все три проверяемые: без него нечем тестировать стадию COMPOSE до
15 сентября; он запасной путь, когда inference API недоступен на питче; он
эталон, которым сверяется план, построенный моделью.

Алгоритм простой намеренно: раздел контента → ранжированный список пригодных
паттернов → лучший. Если целиком не влезает, раздел дробится на части, пока не
влезет.
"""

from __future__ import annotations

from ..model import DeckPlan, Pattern, PatternLibrary, PlannedSlide, PlanSource
from .content import ContentBlock, ContentDoc, ContentSection
from .matching import DEFAULT_TUNING, Match, Tuning, rank

#: На сколько частей максимум дробится один раздел. Дальше честнее признать, что
#: контент не лёг, чем размазать его по десятку слайдов.
_MAX_PARTS = 6

#: Позиционная надбавка: первый слайд хочет быть обложкой, последний — финалом.
_POSITION_BONUS = 0.5

#: И обратное: обложка и финал в середине колоды неуместны по смыслу, каким бы
#: удачным ни оказалось их геометрическое совпадение.
_POSITION_PENALTY = 0.6
_POSITIONAL_KINDS = ("cover", "closing")

#: Штраф за каждое предыдущее использование раскладки в колоде. Замер: без него
#: на дизайнерском шаблоне все пять содержательных слайдов брали один и тот же
#: паттерн — колода читается как машинная, а это прямо критерий «не показать
#: пальцем». Штраф мягкий: если повтор действительно лучший, он всё равно
#: победит.
#: Значение живёт в `matching.Tuning`, чтобы все три настраиваемые величины
#: ранга лежали в одном объекте (`ADR-0020`, политики). Здесь оставлено имя
#: для читаемости этого модуля.
_REPEAT_PENALTY = DEFAULT_TUNING.repeat

#: Потолок кратности, с которой штраф перестаёт расти. Без потолка раскладка,
#: использованная пять раз на длинной колоде, проигрывала бы заведомо негодной.
#: Три — это «повтор уже бросается в глаза», дальше усиливать нечего.
_REPEAT_CAP = 3


# --- дробление раздела -------------------------------------------------


def _atoms(section: ContentSection) -> list[tuple[str, object]]:
    """Раскладывает раздел на неделимые кусочки. Пункт списка — тоже кусочек."""
    out: list[tuple[str, object]] = []
    for block in section.blocks:
        if block.kind == "list":
            for item in block.items:
                out.append((block.id, item))
        else:
            out.append((block.id, block))
    return out


def _rebuild(section: ContentSection, chunk: list[tuple[str, object]]) -> ContentSection:
    """Собирает раздел обратно, склеивая подряд идущие пункты одного списка."""
    blocks: list[ContentBlock] = []
    pending_id: str | None = None
    pending: list[str] = []

    def flush() -> None:
        nonlocal pending_id, pending
        if pending:
            blocks.append(
                ContentBlock(
                    id=pending_id or "b00",
                    kind="list",
                    items=tuple(pending),
                    text=" ".join(pending),
                )
            )
        pending_id, pending = None, []

    for block_id, payload in chunk:
        if isinstance(payload, str):
            if pending_id not in (None, block_id):
                flush()
            pending_id = block_id
            pending.append(payload)
        else:
            flush()
            blocks.append(payload)  # type: ignore[arg-type]
    flush()
    return ContentSection(id=section.id, heading=section.heading, blocks=tuple(blocks))


def split(section: ContentSection, parts: int) -> list[ContentSection]:
    atoms = _atoms(section)
    if parts <= 1 or len(atoms) <= 1:
        return [section]
    size = -(-len(atoms) // parts)  # округление вверх
    chunks = [atoms[i:i + size] for i in range(0, len(atoms), size)]
    return [_rebuild(section, c) for c in chunks if c]


# --- планирование ------------------------------------------------------


def _avoid_repeat(
    matches: list[Match],
    used: dict[str, int],
    tuning: Tuning = DEFAULT_TUNING,
    exclusive: frozenset[str] = frozenset(),
    fired: set[str] | None = None,
) -> list[Match]:
    """Отодвигает раскладки по частоте их использования во всей колоде.

    Раньше сравнивалось только с предыдущим слайдом, и повтор через слайд не
    виделся вовсе. Замер (`PLAN-2.1`): на дизайнерском шаблоне одна раскладка
    брала три слайда из шести, причём соседними были лишь два из трёх.

    Штраф растёт с числом использований и упирается в потолок: это штраф, а не
    запрет. Если раскладка одна на весь шаблон, она и будет выбираться — на
    двухслайдовом шаблоне повторять больше нечего.

    **Для доноров из `exclusive` это запрет, а не штраф** (`Z-44`). Такой донор
    несёт диаграмму или внедрённый объект, клоны разделяют одну часть, и
    PowerPoint отказывается открывать файл **целиком**. Замер: `chart` ×2 и ×3,
    `oleObject` ×2 и ×3 — `open_failed`; картинки ×3 — цел
    (`WORKLOG/2026-09-19-z38-baseline.md`).

    Почему запрет, а не дублирование части: дублировать правильнее, но это
    новые части, новые связи, внедрённая книга у диаграммы и привязанные к
    слайду идентификаторы у VML. Отложено сознательно — `PLAN-6.1`.
    """
    banned = [m for m in matches if m.pattern_id in exclusive and used.get(m.pattern_id)]
    if banned:
        matches = [m for m in matches if m not in banned]
        if fired is not None:
            # Запрет **сработал**: кандидат был и был отброшен. Предупреждать
            # о самом наличии исключительного донора незачем — он мог и не
            # пригодиться дважды, а лишнее предупреждение обесценивает нужное.
            fired.update(m.pattern_id for m in banned)
    if not used:
        return matches
    adjusted = [
        Match(
            pattern_id=m.pattern_id,
            kind=m.kind,
            fills=m.fills,
            score=round(
                m.score - tuning.repeat * min(used.get(m.pattern_id, 0), _REPEAT_CAP), 4
            ),
            reason=m.reason,
            leftover=m.leftover,
        )
        for m in matches
    ]
    return sorted(adjusted, key=lambda m: (-m.score, m.pattern_id))


def _positional(matches: list[Match], first: bool, last: bool) -> list[Match]:
    """Место в колоде — тоже довод. Обложка уместна только первой, финал только
    последним, и в середине оба неуместны, как бы ни совпала геометрия."""
    wanted = "cover" if first else ("closing" if last else None)
    adjusted = []
    for m in matches:
        score, reason = m.score, m.reason
        if wanted and m.kind == wanted:
            score += _POSITION_BONUS
            place = "первый" if first else "последний"
            reason = f"{m.reason}; это {place} слайд колоды"
        elif m.kind in _POSITIONAL_KINDS:
            # Обложка неуместна везде, кроме первого слайда; финал — везде,
            # кроме последнего. Раньше штраф ловил только середину, и обложка
            # спокойно вставала в конец колоды.
            misplaced = (m.kind == "cover" and not first) or (m.kind == "closing" and not last)
            if misplaced:
                score -= _POSITION_PENALTY
        adjusted.append(
            Match(
                pattern_id=m.pattern_id,
                kind=m.kind,
                fills=m.fills,
                score=round(score, 4),
                reason=reason,
                leftover=m.leftover,
            )
        )
    return sorted(adjusted, key=lambda m: (-m.score, m.pattern_id))


def _place(
    section: ContentSection,
    patterns: tuple[Pattern, ...],
    first: bool,
    last: bool,
    used: dict[str, int] | None = None,
    min_parts: int = 1,
    tuning: Tuning = DEFAULT_TUNING,
    exclusive: frozenset[str] = frozenset(),
    fired: set[str] | None = None,
) -> tuple[list[tuple[ContentSection, Match]], int]:
    """Раскладывает раздел, дробя его при необходимости. Возвращает (пары, частей).

    `min_parts` — нижняя граница дробления. Обычно единица: дробим, только когда
    целиком не влезает. Подгонка объёма (`Z-35`, `PLAN-2.3`) поднимает её, чтобы
    добрать слайдов, когда их вышло меньше заданного.
    """
    for parts in range(max(1, min_parts), max(_MAX_PARTS, min_parts) + 1):
        chunks = split(section, parts)
        if len(chunks) < parts and parts > 1:
            break  # дробить дальше нечего
        placed = []
        # Счётчик свой на каждую попытку дробления: неудачная попытка не должна
        # оставлять следов в статистике следующей.
        tally = dict(used or {})
        for n, chunk in enumerate(chunks):
            ranked = _positional(
                rank(chunk, patterns, tuning),
                first=first and n == 0,
                last=last and n == len(chunks) - 1,
            )
            ranked = _avoid_repeat(ranked, tally, tuning, exclusive, fired)
            if not ranked:
                placed = []
                break
            placed.append((chunk, ranked[0]))
            tally[ranked[0].pattern_id] = tally.get(ranked[0].pattern_id, 0) + 1
        if placed:
            return placed, len(chunks)
    return [], 0


def _with_cover(doc: ContentDoc) -> list[ContentSection]:
    """Выделяет титульный слайд из заголовка колоды.

    Без этого первый раздел вместе с вводным абзацем не помещался в обложку,
    у которой обычно один слот, и колода начиналась с обычного слайда.
    Заголовок уезжает на титул, его содержимое — на следующий слайд.
    """
    sections = [s for s in doc.sections if not s.is_empty]
    if not doc.title or not sections or sections[0].heading != doc.title:
        return sections
    first = sections[0]
    cover = ContentSection(id=f"{first.id}:cover", heading=doc.title, blocks=())
    rest = ContentSection(id=first.id, heading=None, blocks=first.blocks)
    return [cover] + ([rest] if rest.blocks else []) + sections[1:]


def _count_slides(
    sections: list[ContentSection],
    patterns: tuple[Pattern, ...],
    forced: dict[str, int],
    tuning: Tuning = DEFAULT_TUNING,
    exclusive: frozenset[str] = frozenset(),
) -> int:
    """Сколько слайдов даст такая раскладка. Планирование стоит доли секунды,
    поэтому подгонка считает результат, а не предсказывает его."""
    total = 0
    used: dict[str, int] = {}
    for n, section in enumerate(sections):
        placed, _ = _place(
            section, patterns,
            first=(n == 0), last=(n == len(sections) - 1),
            used=used, min_parts=forced.get(section.id, 1), tuning=tuning,
            exclusive=exclusive,
        )
        for _, m in placed:
            used[m.pattern_id] = used.get(m.pattern_id, 0) + 1
        total += len(placed)
    return total


def _elective_limit(section: ContentSection) -> int:
    """На сколько частей раздел можно разбить **по своей воле**, ради объёма.

    Пол — две единицы на часть. Он выведен замером, а не назначен: с делением до
    одной единицы демо-контент давал четыре слайда подряд с заголовком «Что
    умеет система» и одним пунктом на каждом
    (`WORKLOG/2026-09-15-deck-volume.md`).

    Вынужденное дробление, когда раздел не влезает в раскладку целиком, этим
    пределом не связано: там выбора нет, и решает `_place`.
    """
    return len(_atoms(section)) // 2


def _forced_parts(
    sections: list[ContentSection],
    patterns: tuple[Pattern, ...],
    target: tuple[int, int] | None,
    ceiling: int | None = None,
    tuning: Tuning = DEFAULT_TUNING,
    exclusive: frozenset[str] = frozenset(),
) -> dict[str, int]:
    """Кому из разделов велено разойтись на несколько слайдов.

    Добираем только вверх по числу слайдов и только до двух пределов:
    содержательного и шаблонного (слайдов не больше, чем раскладок — иначе «два
    слайда дублируют друг друга» из Приложения 1 ТЗ).

    Содержательный предел — **две единицы на часть**. Он выведен замером, а не
    назначен: с делением до одной единицы демо-контент давал четыре слайда
    подряд с заголовком «Что умеет система» и одним пунктом на каждом
    (`WORKLOG/2026-09-15-deck-volume.md`). Дробление ради объёма — дело
    добровольное, и платить за него одинаковыми слайдами незачем. Вынужденное
    дробление, когда раздел не влезает в раскладку, этим пределом не связано:
    там выбора нет.
    """
    if not target:
        return {}
    low = target[0]
    forced: dict[str, int] = {}
    slides = _count_slides(sections, patterns, forced, tuning, exclusive)
    ceiling = len(patterns) if ceiling is None else ceiling
    while slides < low and slides < ceiling:
        # Самый крупный из тех, кого ещё можно разделить; при равенстве — первый
        # по порядку колоды.
        best = None
        for n, section in enumerate(sections):
            atoms = len(_atoms(section))
            elective_max = _elective_limit(section)
            if elective_max >= 2 and forced.get(section.id, 1) < elective_max:
                key = (-atoms, n)
                if best is None or key < best[0]:
                    best = (key, section)
        if best is None:
            break
        section = best[1]
        forced[section.id] = forced.get(section.id, 1) + 1
        grown = _count_slides(sections, patterns, forced, tuning, exclusive)
        if grown <= slides:
            # Дробление не дало прироста — дальше по этому разделу смысла нет.
            del forced[section.id]
            break
        slides = grown
    return forced


def plan_deck(
    doc: ContentDoc,
    library: PatternLibrary,
    design_system_sha256: str,
    target: tuple[int, int] | None = None,
    tuning: Tuning = DEFAULT_TUNING,
) -> DeckPlan:
    """План колоды. `target` — желаемое число слайдов (`Z-35`, `PLAN-2.3`).

    Цель достигается **только доборот вниз**: если слайдов вышло меньше нижней
    границы, разделы с двумя и более атомами переразмещаются с принудительным
    дроблением. Перебор сверху лечится раньше — подгонкой числа тем в
    `prose.py`, до планирования (`PLAN-2.3`, дыра 2).
    """
    patterns = library.patterns
    slides: list[PlannedSlide] = []
    unplaced: list[str] = []
    # Что сделал сегментатор прозы, читается из документа: «вход распознан как
    # проза» должно быть видно в отчёте, а не подразумеваться (`PLAN-2.2`).
    warnings: list[str] = list(doc.notes)

    if not patterns:
        return DeckPlan(
            source=PlanSource(doc.name, design_system_sha256, library.source.sha256),
            slides=(),
            unplaced=tuple(s.id for s in doc.sections),
            warnings=tuple(warnings) + ("В библиотеке нет ни одного паттерна: планировать не на что.",),
        )

    sections = _with_cover(doc)
    # Доноры, которые нельзя ставить в колоду дважды (`Z-44`). Множество
    # считается один раз и передаётся вниз: пересчитывать его на каждом
    # разделе значило бы звать `pkg.rels` в цикле.
    exclusive = frozenset(p.id for p in patterns if getattr(p, "exclusive", False))
    fired: set[str] = set()
    forced = _forced_parts(sections, patterns, target, tuning=tuning, exclusive=exclusive)
    used: dict[str, int] = {}
    for n, section in enumerate(sections):
        placed, parts = _place(
            section,
            patterns,
            first=(n == 0),
            last=(n == len(sections) - 1),
            used=used,
            min_parts=forced.get(section.id, 1),
            tuning=tuning,
            exclusive=exclusive,
            fired=fired,
        )
        if not placed:
            unplaced.append(section.id)
            warnings.append(
                f"Разделу «{section.heading or section.id}» не нашлось пригодной "
                f"раскладки даже после дробления на {_MAX_PARTS} частей."
            )
            continue
        if parts > 1:
            warnings.append(
                f"Раздел «{section.heading or section.id}» разбит на {parts} слайда: "
                f"целиком он не помещался ни в одну раскладку."
            )
        for part, (chunk, m) in enumerate(placed, 1):
            used[m.pattern_id] = used.get(m.pattern_id, 0) + 1
            slides.append(
                PlannedSlide(
                    index=len(slides),
                    pattern_id=m.pattern_id,
                    fills=m.fills,
                    reason=m.reason,
                    origin_section=section.id,
                    origin_part=part if parts > 1 else None,
                    origin_of=parts if parts > 1 else None,
                )
            )

    # Донор с исключительными частями занят — и это надо сказать, а не
    # подразумевать: иначе «почему тут другая раскладка» останется загадкой
    # (`PLAN-6.1`, Ш1.3).
    if fired:
        names = ", ".join(
            f"{pid} ({next((p.kind for p in patterns if p.id == pid), '?')})"
            for pid in sorted(fired)
        )
        warnings.append(
            f"Повтор запрещён для раскладок: {names}. Их доноры несут диаграмму "
            f"или внедрённый объект, два клона делят одну часть, и PowerPoint "
            f"отказывается открывать файл целиком (Z-44). Разделам, которым эти "
            f"раскладки подошли бы, подобраны другие."
        )

    # Донор несёт часть, которой нет ни в списке разделяемых, ни в списке
    # исключительных, и при этом повторён. Список исключительных типов собран
    # из встреченных файлов и **неполон по построению**, поэтому здесь не
    # запрет, а предупреждение: неизвестный тип должен проявляться словами, а
    # не неоткрывающимся файлом у эксперта (`PLAN-6.1`, обратный план, п. 7).
    for pattern in patterns:
        unknown = getattr(pattern, "unknown_parts", ())
        if unknown and used.get(pattern.id, 0) > 1:
            warnings.append(
                f"Раскладка {pattern.id} повторена {used[pattern.id]} раза, а её "
                f"донор несёт части незнакомого нам вида: {', '.join(unknown)}. "
                f"Разделяются ли они между клонами, мы не знаем — если файл не "
                f"откроется, начинать искать надо отсюда (Z-44)."
            )

    if target:
        warnings.append(_volume_note(len(slides), target, forced, len(patterns)))

    return DeckPlan(
        source=PlanSource(doc.name, design_system_sha256, library.source.sha256),
        slides=tuple(slides),
        planner="deterministic",
        unplaced=tuple(unplaced),
        warnings=tuple(warnings),
    )


def _volume_note(
    got: int, target: tuple[int, int], forced: dict[str, int], patterns: int
) -> str:
    """Что вышло с объёмом и почему.

    Недобор и перебор — разные новости, и причины у них разные: мало контента,
    бедный шаблон или раздел, не влезший в раскладку целиком. Одинаковое «цель не
    достигнута» на все случаи было бы отговоркой (`PLAN-2.3`, второй проход).
    """
    low, high = target
    want = f"{low}" if low == high else f"{low}–{high}"
    if low <= got <= high:
        note = f"Целевой объём {want} слайдов выдержан: вышло {got}."
        if got > patterns:
            # Слайдов больше, чем раскладок, — значит какие-то повторились.
            # Приложение 1 ТЗ считает дефектом «два слайда дублируют друг друга»,
            # и молчать об этом нельзя, даже когда объём попал в цель.
            note += (
                f" Но раскладок в шаблоне всего {patterns}, так что часть из них"
                f" повторяется — объём здесь дороже разнообразия."
            )
        return note
    if got < low:
        if got >= patterns:
            return (
                f"Целевой объём {want} слайдов не достигнут: вышло {got}. "
                f"В шаблоне всего {patterns} раскладок, и добор дальше означал бы "
                f"повторять их — «два слайда дублируют друг друга» хуже недобора."
            )
        return (
            f"Целевой объём {want} слайдов не достигнут: вышло {got}. "
            f"Контента хватает только на столько: дробить дальше нечего, "
            f"слайд из половины тезиса был бы пустым."
        )
    return (
        f"Целевой объём {want} слайдов превышен: вышло {got}. "
        f"Разделы не поместились в раскладки шаблона целиком и разошлись на части; "
        f"резать текст ради числа мы не будем."
    )
