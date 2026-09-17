"""Весы для колоды: дешёвый ярус (`ADR-0019`, `PLAN-2.5`).

Четыре счётные величины, все считаются **по плану, без PowerPoint**. Сравнение
**по старшинству**, а не взвешенной суммой: сумма потребовала бы весов, а веса —
это те же подбираемые константы этажом выше, от которых мы и лечимся.

| Ступень | Направление |
|---|---|
| потеряно единиц контента | ноль, это запрет |
| слотов сломано безвозвратно | меньше |
| слотов переполнено, но поправимо | меньше |
| уникальных раскладок в колоде | больше |
| заливок «на донышке» | меньше |

**Почему величины счётные, а не доли.** Порог значимости («разница меньше
такой-то — ничья») брать неоткуда: движок детерминирован, шума в нём нет. У
целых чисел вопрос порога не возникает вовсе — разница в один сломанный слот
реальна по определению. Это же лишает весы последней возможности быть
подогнанными (`PLAN-2.5`, логическая проверка 1).

**Ступень «переполнено, но поправимо» добавлена 16 сентября после сверки с
глазом** (`WORKLOG/2026-09-16-scales-vs-eye.md`). Без неё весы дали колоде VK
WorkSpace ноль поломок, а на растре три заголовка карточек уезжали под плашку.
Замечательно здесь то, **чья** мерка оказалась права: наша грубая оценка
«знаков к ёмкости» переполнение видела (r до 1.91), а точный замер PowerPoint
через доступное место — нет. Вопросы разные: оценка спрашивает «влезает ли текст
в слот, каким его задумал дизайнер», детектор — «сталкивается ли текст с
чем-нибудь». Зритель ближе к первому.

**На чём стоит ступень «сломано».** Ёмкость слота — это оценка по геометрии и
кеглю (`DOM-TEXT §9`), а не замер. Она ошибается в обе стороны: находит одно
переполнение из трёх и в четырёх тревогах из десяти ошибается. Строить на ней
приговор «здесь дефект» нельзя, и `DOM-TEXT §9` это прямо запрещает.

Здесь она употребляется иначе, и разница существенна: весы **сравнивают варианты
между собой**, а систематическая ошибка формулы у всех вариантов одна и при
сравнении сокращается. Плюс к отношению добавлен пол читаемости из `repair.py` —
то есть ступень отвечает на вопрос «спасёт ли ремонт», а не «сломано ли сейчас».
Разбор — в `DOM-TEXT §9`, дополнение от 17 сентября.

**Чего весы не видят и видеть не могут:** осмысленности заголовка, порядка
повествования, попадания в стиль. Они меряют вёрстку, а не презентацию. Поэтому
каждое улучшение весов проверяется растром, и при расхождении правы глаза.

**Слепое пятно, которое сейчас спит:** порядок слайдов. Он задан входным текстом
и не варьируется, поэтому колоду с верным содержанием в бессмысленной
последовательности весы не отличат от хорошей. Как только появится `Z-37`
(«назначение» как ось порядка), это станет главной дырой.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..model import DeckPlan, Fill, PatternLibrary, Slot

# Пол читаемости принадлежит стадии VERIFY: она владеет ремонтом и знает, докуда
# может ужать. PLAN спрашивает её «а это ты вытянешь?» — это запрос, а не
# нарушение слоёв, и обратной зависимости нет (`verify` не импортирует `plan`).
from ..verify.repair import HARD_MIN_SCALE, MIN_VISIBLE_PT, TARGET_RATIO

# Доля от ориентира, ниже которой слот считается заполненным «на донышке».
# Та же величина, что в ранге: одно определение на весь PLAN.
from .matching import _SLACK_RATIO

#: Кегль донора записан словами в обосновании ёмкости: «… при 10 pt, …».
#: Отдельного поля у `Capacity` нет, а заводить его ради весов — менять контракт
#: артефакта ради измерителя. Разбор строки честнее.
_PT = re.compile(r"при\s+([\d.]+)\s*pt")


@dataclass(frozen=True)
class DeckScore:
    """Оценка колоды. Сравнивать через `key`, а не поля по отдельности."""

    lost: int       # ступень 1 — потеряно единиц контента
    broken: int     # ступень 2 — слотов сломано безвозвратно
    tight: int      # ступень 3 — переполнено, но ремонт вытянет
    layouts: int    # ступень 4 — уникальных раскладок
    thin: int       # ступень 5 — заливок «на донышке»
    slides: int     # справочно
    fills: int      # справочно

    @property
    def key(self) -> tuple[int, int, int, int, int]:
        """Ключ сравнения по старшинству. **Меньше — лучше.**

        Разнообразие входит со знаком минус: оно единственное, чего хочется
        больше. Ничья по всем четырём разрешается снаружи — идентификатором
        варианта, потому что воспроизводимость обязательна.
        """
        return (self.lost, self.broken, self.tight, -self.layouts, self.thin)

    @property
    def admissible(self) -> bool:
        """Потеря контента — запрет, а не «хуже». Такой вариант выбывает.

        Если выбывают все, выбор делается среди наименьших потерь и об этом
        говорится словами: молча отдать колоду с потерянным разделом нельзя.
        """
        return self.lost == 0

    def to_json(self) -> dict:
        return {
            "lost": self.lost,
            "broken": self.broken,
            "tight": self.tight,
            "layouts": self.layouts,
            "thin": self.thin,
            "slides": self.slides,
            "fills": self.fills,
        }


def nominal_pt(slot: Slot) -> float | None:
    """Кегль донора для этого слота, из обоснования ёмкости. `None` — неизвестен."""
    if slot.capacity is None or not slot.capacity.basis:
        return None
    found = _PT.search(slot.capacity.basis)
    return float(found.group(1)) if found else None


def max_repairable_ratio(pt: float) -> float:
    """Наибольшее отношение «знаков к ёмкости», которое VERIFY ещё вытянет.

    Высота набранного текста падает примерно как квадрат шкалы кегля
    (`repair.py`), целимся с запасом `TARGET_RATIO`, ниже пола читаемости не
    опускаемся. Отсюда предел — и он **зависит от кегля донора**, а не один на
    все слоты: слот на 10 pt уже стоит на полу, и ужать его нельзя вовсе.

    Замер по корпусу — `WORKLOG/2026-09-16-broken-slot-threshold.md`: сломанных
    заливок 22 из 472, и **все в слотах 14 pt и мельче**.
    """
    smallest = max(HARD_MIN_SCALE / 100.0, MIN_VISIBLE_PT / pt)
    return TARGET_RATIO / (smallest * smallest)


def fill_length(fill: Fill) -> int:
    """Сколько знаков несёт заливка. Список считается вместе с разделителями."""
    if fill.text:
        return len(fill.text)
    items = fill.items or ()
    return sum(len(i) for i in items) + max(0, len(items) - 1)


def is_broken(slot: Slot, fill: Fill) -> bool:
    """Сломан ли слот безвозвратно: ужать до читаемого уже не выйдет.

    Неизвестный кегль **не считается поломкой**: молчать о том, чего не знаем,
    честнее, чем записывать в дефекты. Измеритель обязан отличать «проверено и
    чисто» от «проверить не смог», и здесь это второе.
    """
    if slot.capacity is None or not slot.capacity.max_chars:
        return False
    pt = nominal_pt(slot)
    if pt is None or pt <= 0:
        return False
    return fill_length(fill) / slot.capacity.max_chars > max_repairable_ratio(pt)


def is_tight(slot: Slot, fill: Fill) -> bool:
    """Переполнен ли слот по оценке — текста больше, чем задумано дизайнером.

    Ступень ниже поломки и **не пересекается** с ней: сломанные считаются
    отдельно, иначе один слот попал бы в обе и вес его удвоился бы молча.

    Переполнение поправимо: ремонт ужмёт кегль. Но ужатый текст мельче соседнего
    и заметен, а иногда ремонт до него просто не доходит — на слайде 2 VK
    WorkSpace три таких слота остались нетронутыми, потому что детектор их не
    увидел (`OQ-18`).
    """
    if slot.capacity is None or not slot.capacity.max_chars:
        return False
    if is_broken(slot, fill):
        return False
    return fill_length(fill) / slot.capacity.max_chars > 1.0


def _is_thin(slot: Slot, fill: Fill) -> bool:
    """Заполнен ли слот «на донышке» — заметно меньше своего ориентира."""
    target = slot.capacity.target_chars if slot.capacity else None
    if not target:
        return False
    return fill_length(fill) / target < _SLACK_RATIO


def score_deck(plan: DeckPlan, patterns: PatternLibrary) -> DeckScore:
    """Оценить колоду дешёвым ярусом весов. PowerPoint не нужен."""
    by_id = {p.id: p for p in patterns.patterns}
    broken = tight = thin = fills = 0
    for slide in plan.slides:
        pattern = by_id.get(slide.pattern_id)
        if pattern is None:
            continue
        slots = {s.id: s for s in pattern.slots}
        for fill in slide.fills:
            slot = slots.get(fill.slot_id)
            if slot is None or slot.capacity is None or not slot.capacity.max_chars:
                continue
            fills += 1
            if is_broken(slot, fill):
                broken += 1
            elif is_tight(slot, fill):
                tight += 1
            if _is_thin(slot, fill):
                thin += 1
    return DeckScore(
        lost=len(plan.unplaced),
        broken=broken,
        tight=tight,
        layouts=len({s.pattern_id for s in plan.slides}),
        thin=thin,
        slides=len(plan.slides),
        fills=fills,
    )


def better(left: DeckScore, right: DeckScore) -> bool:
    """Левая колода строго лучше правой по старшинству ступеней."""
    return left.key < right.key
