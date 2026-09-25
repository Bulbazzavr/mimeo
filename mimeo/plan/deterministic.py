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

import os

from dataclasses import replace

from .. import config as cfg
from ..model import DeckPlan, Pattern, PatternLibrary, PlannedSlide, PlanSource
from .content import ContentBlock, ContentDoc, ContentSection
from .matching import _REPEAT_CAP as MATCHING_REPEAT_CAP
from .matching import DEFAULT_TUNING, Match, Tuning, rank

#: На сколько частей максимум дробится один раздел. Дальше честнее признать, что
#: контент не лёг, чем размазать его по десятку слайдов.
_MAX_PARTS = 6

#: Позиционная надбавка: первый слайд хочет быть обложкой, последний — финалом.
_POSITION_BONUS = 0.5

#: И обратное: обложка и финал в середине колоды неуместны по смыслу, каким бы
#: удачным ни оказалось их геометрическое совпадение. Тот же штраф платит макет
#: оглавления под обычным текстом — на любом месте: оглавление встаёт, только
#: если его заказали (`Z-58`, `PLAN-9.0`, часть Б), а путь без модели его не
#: заказывает никогда. Штраф, а не запрет: на бедном шаблоне такой макет может
#: оказаться единственным, куда раздел влезает, и потерять раздел хуже.
#:
#: **Привязан к штрафу за повтор** (`Z-58`) — как штраф за чужую гарнитуру
#: (`Z-43`) и по той же причине: `config/variants.json` гоняет `repeat` до 0.8,
#: и голые 0.6 тонули в политиках, из которых собираются варианты сдаточных
#: колод. Замер на VK Tech, вариант 3 (политика `r0.8-s0.45-o0.9`): хорошие
#: раскладки уже стояли в колоде и платили по 0.8 за повтор, и макеты
#: «Содержание» (0.10) и «Спасибо» (0.13) обходили их посреди колоды. Правило то
#: же, что у гарнитуры: **неуместный макет хуже любого повтора, который
#: политика способна назначить.**
_POSITION_PENALTY = 0.6
_POSITIONAL_KINDS = ("cover", "closing")


def position_penalty(tuning: Tuning) -> float:
    return _POSITION_PENALTY + tuning.repeat * _REPEAT_CAP

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
#: Значение живёт в `matching`, рядом с самим штрафом и со штрафом за чужую
#: гарнитуру, который от него считается (`Z-43`).
_REPEAT_CAP = MATCHING_REPEAT_CAP

#: Как называть человеку чужую гарнитуру слота и то, чем она оборачивается на
#: слайде. Это читает эксперт, а не лог (`Z-43`).
_FOREIGN_WORDS = {
    "mono": "моноширинным шрифтом",
    "icon": "пиктограммным шрифтом",
}
_FOREIGN_LOOKS = {
    "mono": "фрагментом кода, а не текстом доклада",
    "icon": "набором значков вместо букв",
}


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
    # `replace`, а не сборка руками: тип слайда и идея картинки от модели
    # (`PLAN-9.0`, Ш3) обязаны пережить дробление — иначе вторая часть
    # заказанного оглавления потеряла бы вид и получила бы штраф.
    return replace(section, blocks=tuple(blocks))


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
    # `replace`, а не сборка руками: поля `Match` прибавляются, и перечисленный
    # руками список молча теряет новое. Так и вышло с `foreign` (`Z-43`):
    # предупреждение не выводилось ни разу, а поймал это замер, не тест.
    adjusted = [
        replace(
            m,
            score=round(
                m.score - tuning.repeat * min(used.get(m.pattern_id, 0), _REPEAT_CAP), 4
            ),
        )
        for m in matches
    ]
    return sorted(adjusted, key=lambda m: (-m.score, m.pattern_id))


def _positional(
    matches: list[Match],
    first: bool,
    last: bool,
    tuning: Tuning = DEFAULT_TUNING,
    agenda: bool = False,
) -> list[Match]:
    """Место в колоде — тоже довод. Обложка уместна только первой, финал только
    последним, и в середине оба неуместны, как бы ни совпала геометрия.
    Оглавление неуместно под обычным текстом нигде (`Z-58`) — кроме раздела,
    который и есть оглавление: его заказала модель, пункты собрал код
    (`agenda`, `PLAN-9.0`, Ш3). Без этого штраф 1.35 при умолчании хоронил бы
    заказанное оглавление против бонуса вида 0.40
    (`WORKLOG/2026-09-25-z57-sh3-baseline.md`, § 6)."""
    wanted = "cover" if first else ("closing" if last else None)
    penalty = position_penalty(tuning)
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
                score -= penalty
        elif m.kind == "agenda" and not agenda:
            score -= penalty
            reason = f"{m.reason}; это макет оглавления, а оглавления не заказывали"
        adjusted.append(replace(m, score=round(score, 4), reason=reason))
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

    **Картинки в условие остановки не входят, и это проверено, а не забыто**
    (`Z-51`, `PLAN-7.11`). Раздел с картинками можно было бы дробить дальше,
    пока каждой не найдётся слот подходящей пропорции, — и по числам это
    выигрывало крупно: картинок по корпусу 7 → 18, заслонений на девяти
    сдаточных колодах 16 → 5. **Растр правку отверг**: на выданном WorkSpace
    картинки уезжали в повёрнутые рамки за край слайда, на VK Education
    сжимались до пиктограммы под значком донора. Причина названа и лежит не
    здесь: `Slot.picture_kind` описывает место голым прямоугольником, а живой
    шаблон уточняет его поворотом, вложенностью и тем, что нарисовано сверху.
    Пока это не учтено, выбор раскладки по пропорции слота находит места хуже,
    а не лучше.
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
                tuning=tuning,
                agenda=chunk.kind == "agenda",
            )
            if chunk.kind == "agenda":
                ranked = [m for m in ranked if _whole_agenda(m, patterns)]
            ranked = _avoid_repeat(ranked, tally, tuning, exclusive, fired)
            if not ranked:
                placed = []
                break
            placed.append((chunk, ranked[0]))
            tally[ranked[0].pattern_id] = tally.get(ranked[0].pattern_id, 0) + 1
        if placed:
            return placed, len(chunks)
    return [], 0


def _whole_agenda(match: Match, patterns: tuple[Pattern, ...]) -> bool:
    """Макет оглавления, принявший заказанное оглавление **целиком и без пустых
    мест** (`PLAN-9.0`, Ш3).

    Растр Ш3 показал оба способа испортить слайд. Оглавление из двенадцати
    пунктов на обычной раскладке — карточках — читается как иерархия: пункт
    встаёт подзаголовком соседнего, а два пункта — заголовками групп. А макет
    оглавления, заполненный не до конца, оставляет пустые номерные плашки:
    у WorkSpace пять пунктов с «01…05» (`WORKLOG/2026-09-25-z57-sh3-result.md`).
    Лучше оглавления нет, чем такое.
    """
    if match.kind != "agenda" or not match.fits:
        return False
    pattern = next((p for p in patterns if p.id == match.pattern_id), None)
    if pattern is None:
        return False
    filled = {f.slot_id for f in match.fills}
    return not any(
        s.required and s.content_type != "image" and s.id not in filled for s in pattern.slots
    )


def _drop_unplaceable_agenda(
    sections: list[ContentSection], patterns: tuple[Pattern, ...], tuning: Tuning
) -> tuple[list[ContentSection], list[str]]:
    """Заказанное оглавление, которому нет макета по `_whole_agenda`, в колоду не
    идёт — и об этом сказано. Иначе `_place` не нашёл бы ему места, и отчёт
    назвал бы оглавление потерянным содержанием, а оно не содержание текста:
    его пункты собрал код из заголовков колоды."""
    kept: list[ContentSection] = []
    notes: list[str] = []
    for section in sections:
        if section.kind == "agenda" and not any(
            _whole_agenda(m, patterns) for m in rank(section, patterns, tuning)
        ):
            items = sum(b.units for b in section.blocks)
            why = (
                "ни один макет оглавления этого шаблона не принимает их целиком и без пустых мест"
                if any(p.kind == "agenda" for p in patterns)
                else "в шаблоне нет макета оглавления"
            )
            notes.append(
                f"Оглавление, заказанное моделью (пунктов: {items}), в колоду не вошло: {why}. "
                f"На обычной раскладке пункты читались бы как иерархия (PLAN-9.0, Ш3)."
            )
            continue
        kept.append(section)
    return kept, notes


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
            if section.kind == "agenda":
                # Оглавление добором не делится: у колоды модели оно часто самый
                # крупный раздел (до двенадцати пунктов), и добор делил бы его
                # первым — два слайда «Содержание» подряд хуже недобора
                # (`PLAN-9.0`, Ш3, проверка 1, п. 4).
                continue
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
    # Паспорт конфигов снимается один раз на план, а не на каждый возврат:
    # два выхода из функции не должны давать разный `source` (`Z-33`).
    configs = cfg.stamps()
    slides: list[PlannedSlide] = []
    unplaced: list[str] = []
    # Что сделал сегментатор прозы, читается из документа: «вход распознан как
    # проза» должно быть видно в отчёте, а не подразумеваться (`PLAN-2.2`).
    warnings: list[str] = list(doc.notes)

    if not patterns:
        return DeckPlan(
            source=PlanSource(doc.name, design_system_sha256, library.source.sha256, configs),
            slides=(),
            planner=doc.planner,
            unplaced=tuple(s.id for s in doc.sections),
            warnings=tuple(warnings) + ("В библиотеке нет ни одного паттерна: планировать не на что.",),
        )

    sections = _with_cover(doc)
    sections, agenda_notes = _drop_unplaceable_agenda(sections, patterns, tuning)
    warnings.extend(agenda_notes)
    # Доноры, которые нельзя ставить в колоду дважды (`Z-44`). Множество
    # считается один раз и передаётся вниз: пересчитывать его на каждом
    # разделе значило бы звать `pkg.rels` в цикле.
    exclusive = frozenset(p.id for p in patterns if getattr(p, "exclusive", False))
    #: Слайды, где текст всё-таки лёг в слот с чужой гарнитурой (`Z-43`).
    foreign_used: list[tuple[int, str, str]] = []
    #: Картинки, которым не нашлось слота-иллюстрации (`Z-28a`).
    dropped_images: list[tuple[int, str, str]] = []
    #: Картинки, вставшие в слот с заметно другой пропорцией (`Z-28a`).
    squeezed_images: list[tuple[int, str, float]] = []
    #: Слайды, где у раскладки остались слоты без содержимого (`Z-49`).
    #: Считаются **все** слоты, а не только текстовые: на растре пустая
    #: карточка под картинку читается как брак ровно так же, а ранг её не
    #: видит — `empty_required` в `matching.py` исключает `image`.
    blank_used: list[tuple[int, str, int, int]] = []
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
            # Раскладку со слотом под код или пиктограммы ранг штрафует, но не
            # запрещает: она бывает единственной пригодной. Раз уж взяли —
            # сказать вслух, какой слайд и почему (`Z-43`, `PLAN-7.3`).
            for kind in sorted(set(getattr(m, "foreign", ()))):
                foreign_used.append((len(slides), m.pattern_id, kind))
            for ref in getattr(m, "dropped_images", ()):
                dropped_images.append((len(slides), m.pattern_id, ref))
            for ref, off in getattr(m, "squeezed_images", ()):
                squeezed_images.append((len(slides), m.pattern_id, off))
            pattern = next((p for p in patterns if p.id == m.pattern_id), None)
            if pattern is not None:
                filled = {f.slot_id for f in m.fills}
                blank = sum(1 for s in pattern.slots if s.id not in filled)
                if blank:
                    blank_used.append(
                        (len(slides), m.pattern_id, blank, len(pattern.slots))
                    )
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

    if foreign_used:
        for kind in sorted({k for _, _, k in foreign_used}):
            hits = [(n, pid) for n, pid, k in foreign_used if k == kind]
            where = ", ".join(f"слайд {n} ({pid})" for n, pid in hits)
            warnings.append(
                f"Текст лёг в слот, набранный {_FOREIGN_WORDS[kind]}: {where}. "
                f"Правила шаблона это не нарушает — гарнитура его собственная, —"
                f" но на слайде так выглядит {_FOREIGN_LOOKS[kind]}. Раскладка "
                f"взята потому, что лучшей пригодной не нашлось (Z-43)."
            )

    # Картинка, которой не нашлось места, исчезает бесследно: `Match.leftover`
    # до `Z-28a` не читал никто. А исчезает она часто и по понятной причине —
    # слот под картинку у раскладки может быть, но быть иконкой, подложкой
    # карточки или фоном: по корпусу таких четыре из пяти (`PLAN-7.10`, шаг 3).
    # Молчать об этом нельзя: встраивание изображений — пункт критерия 3, и
    # «картинка не вставилась» обязано быть видно в отчёте, а не на растре.
    if dropped_images:
        where = ", ".join(
            f"слайд {n} ({pid}): {os.path.basename(ref)}"
            for n, pid, ref in dropped_images
        )
        warnings.append(
            f"Картинок не вставлено: {len(dropped_images)} — {where}. "
            f"У раскладки не нашлось слота под иллюстрацию: слоты под картинку "
            f"в ней есть, но это пиктограммы, подложки карточек или фон, и наша "
            f"картинка в них испортила бы слайд (Z-28a)."
        )

    # Габариты фигуры донора мы не трогаем (`ADR-0004`), поэтому картинка в
    # слоте с чужой пропорцией растягивается. Обычно расхождение мало — слот
    # выбирается по пропорции, — но бывает, что выбирать не из чего: на
    # выданном VK Education единственный слот, куда ложится этот раздел, —
    # круглый узел блок-схемы, а картинка баннерная. Отказ там означал бы «нет
    # встраивания» на трети сдачи, поэтому ставим и **называем**, а не выдаём
    # сплющенное за задуманное (`Z-28a`, `PLAN-7.10`, шаг 5).
    if squeezed_images:
        where = ", ".join(
            f"слайд {n} ({pid}): на {off:.0%}"
            for n, pid, off in squeezed_images
        )
        warnings.append(
            f"Картинок растянуто: {len(squeezed_images)} — {where}. Пропорция "
            f"слота расходится с пропорцией файла, а габариты фигуры донора мы "
            f"не меняем: вёрстка шаблона его. Слот выбран самый близкий по "
            f"пропорции из тех, что раскладка предлагает (Z-28a)."
        )

    # Слот без содержимого COMPOSE очищает от текста донора (`PLAN-3.1`), и
    # на слайде остаётся оформление без подписи: карточка с иконкой и пустым
    # телом, пустая плашка под картинку. Ранг за это платит
    # (`_PENALTY_EMPTY_SLOT`), но платы **не всегда хватает**, и до `Z-49`
    # остаток не назывался вообще ничем: ни числом, ни списком.
    #
    # Молчать нельзя по той же причине, по которой не молчит `Z-43`: раскладку
    # взяли потому, что лучшей пригодной не нашлось, и человек должен узнать
    # об этом из отчёта, а не с растра. Это же половина того, чего требует
    # критерий 3 — «аудит показывает проблемы» (`Z-34`).
    if blank_used:
        where = ", ".join(
            f"слайд {n} ({pid}): {blank} из {total}"
            for n, pid, blank, total in blank_used
        )
        total_blank = sum(b for _, _, b, _ in blank_used)
        warnings.append(
            f"Слотов без содержимого {total_blank} на {len(blank_used)} слайдах — "
            f"{where}. Текст донора из них убран, оформление осталось: на слайде "
            f"это карточка или плашка без подписи. Содержание не потеряно, оно "
            f"легло в другие слоты; раскладка взята потому, что лучшей пригодной "
            f"не нашлось (Z-49)."
        )

    if target:
        warnings.append(_volume_note(len(slides), target, forced, len(patterns)))

    return DeckPlan(
        source=PlanSource(doc.name, design_system_sha256, library.source.sha256, configs),
        slides=tuple(slides),
        planner=doc.planner,
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
