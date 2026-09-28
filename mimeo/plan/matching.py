"""Подбор паттернов под кусок контента. Шаг Ш3 плана `PLAN-2.0`.

Разделение труда с моделью — главное решение этого модуля (`ADR-0009`):

* **пригодность считает код.** Хватает ли слотов, влезает ли текст, есть ли
  материал под слот изображения — это арифметика, и модели тут делать нечего;
* **выбор из пригодных делает модель.** Что «сравнение выручки по годам»
  просится в две колонки, а не в список, — это смысл, и его код не понимает.

Детерминированный планировщик (`deterministic.py`) берёт первый по рангу
пригодный. Модель, когда появится, выберет из тех же пригодных — но список ей
готовит этот модуль.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

from ..analyze.picture import FRAME
from ..analyze.picture import _overlap as _rect_overlap
from ..analyze.picture import load_config as load_picture_config
from ..model import Fill, Pattern, Slot
from .content import ContentBlock, ContentSection
from .imagesize import image_size

#: Роли слотов по назначению.
_TITLE_ROLES = ("title",)
_SUBTITLE_ROLES = ("subtitle",)
_TEXT_ROLES = ("body", "bullet_list", "caption", "label", "quote", "attribution")
_METRIC_VALUE_ROLES = ("metric_value",)
_METRIC_LABEL_ROLES = ("metric_label", "caption", "label")

#: Насколько поднимает ранг совпадение вида паттерна с ожидаемым.
_BONUS_BEST_KIND = 0.40
_BONUS_ANY_KIND = 0.18

#: Штраф за незакрытый обязательный слот. (За слот «на донышке» платит
#: `_PENALTY_SLACK` ниже — это разные величины.)
#:
#: **0.15 оставлено замером, а не по умолчанию** (`Z-49`, `PLAN-7.9`,
#: `WORKLOG/2026-09-21-z49-result.md`). Величину подняли по всей сетке, и
#: числа выглядели победой: пустых обязательных по 14 шаблонам 223 → 185, на
#: девяти сдаточных колодах 138 → 81, переполнения не выросли, размещённых
#: знаков ровно столько же. **Опроверг это растр.** Ранг уходил на раскладку в
#: два слота, у которой «обязательных пустых» ноль, — а на слайде остаётся
#: крупная пустая карточка под картинку, которой мерка не видит вовсе, и одна
#: и та же раскладка встаёт трижды подряд. Мерка считает текстовые слоты, а
#: зритель видит любое пустое место.
#:
#: Потолок сверху тоже измерен и жёсткий: при 0.35 у **выданного** VK
#: WorkSpace тройка вариантов рассыпается до пары, то есть сдаточных колод
#: становится восемь вместо девяти (требование ТЗ). Поднимать эту величину,
#: не посмотрев на растр и на тройку вариантов, нельзя.
_PENALTY_EMPTY_SLOT = 0.15
_PENALTY_SLACK = 0.10

#: Место под таблицу или диаграмму — чужие числа на слайде, чем бы его ни
#: заполнили: данные таблиц и диаграмм движок не подменяет (`Z-12`), а сборка
#: такую фигуру убирает (`compose/builder.py`), оставляя дыру. Штраф крупнее,
#: чем за пустое место: числа донора хуже пустой карточки (`Z-62`; Приложение 1
#: ТЗ, вопрос 4 — «все цифры и факты — из исходных материалов?»). Девятка
#: 26 сентября: таблица «Акцент 15 10» на двух колодах Education.
_PENALTY_DONOR_DATA = 0.5

#: Штраф за рамку под фото (`Z-55`), которая останется пустой: после плана её
#: некому залить — генератор выключен или колоду строила не модель
#: (`Tuning.frames_later`). Вместе с обычным штрафом пустого места — столько же,
#: сколько чужие числа донора: белая карточка во весь угол слайда — такая же
#: дыра. Сборка пользователя 28 сентября, генератор выключен: VK Tech, 6 слайдов
#: из 11 с пустыми белыми рамками. Штраф, а не запрет: раскладка без рамки
#: бывает хуже по тексту, и раздел не должен пропасть.
_PENALTY_EMPTY_FRAME = 0.5

#: Ниже этой доли от ориентира слот считается заполненным «на донышке».
_SLACK_RATIO = 0.25

#: Переполнение слота допускается и штрафуется, а не запрещается.
#:
#: Правило «влезает точно либо паттерн непригоден» на реальных шаблонах
#: отвергало всё: слоты подогнаны под собственные короткие тексты дизайнера, и
#: в обложечный заголовок шаблона на холсте 26.7 дюйма при кегле 138 pt влезает
#: девять знаков. Замер: 4 раздела демо-контента из 5 не размещались вовсе.
#:
#: Гарантировать, что ничего не торчит, — работа стадии VERIFY (`ADR-0005`):
#: она ужимает кегль по растру. Дублировать её здесь значит делать систему
#: хрупкой ради чужой ответственности. Здесь мы стремимся уложиться, а не
#: обязаны: переполнение до порога штрафуется и помечается, сверх порога —
#: слот действительно не годится.
_OVERFLOW_HARD = 2.5
_PENALTY_OVERFLOW = 0.35

#: Штраф за повтор раскладки. Живёт здесь, а не в `deterministic`, чтобы все
#: три настраиваемые величины ранга лежали в одном месте и в одном объекте.
_REPEAT_PENALTY = 0.25

#: Потолок кратности, с которой штраф за повтор перестаёт расти. Переехал сюда
#: из `deterministic` по той же причине, что и сам штраф, и ещё по одной: от
#: него считается штраф за чужую гарнитуру ниже.
_REPEAT_CAP = 3

#: Штраф за слот, чья гарнитура не предназначена для прозы: моноширинная — у
#: донора там был код — или пиктограммная (`Z-43`, `PLAN-7.3`). Начисляется за
#: **каждый** такой слот, куда действительно лёг текст; слоту изображения
#: гарнитура безразлична.
#:
#: Штраф, а не запрет, и по той же причине, что у переполнения выше: слайд
#: читается и правила шаблона соблюдает — Consolas есть в самом шаблоне VK Tech,
#: — он просто выглядит фрагментом кода. Запрет отнял бы раскладку целиком там,
#: где она единственная пригодная.
#:
#: **В `Tuning` этой величины нет намеренно.** `ADR-0020` перебирает три
#: величины сеткой 3×3×3 = 27 политик; четвёртая превратила бы её в 81 и
#: обесценила бы замеры `Z-26`, включая отбор девяти сдаточных колод. Гарнитура
#: не ось различия вариантов: вариант, где проза набрана как код, — не вариант,
#: а дефект, и он дефект во всех политиках сразу.
#:
#: Единица здесь не круглое число, а граница: `coverage` не бывает больше 1.0,
#: значит штраф ровно съедает **всё покрытие** раскладки. Раскладка, уложившая
#: весь раздел до последнего блока, но в слот под код, стоит столько же, сколько
#: не уложившая ничего. Замер: на полку выходит как раз на единице — по всем 27
#: политикам и всему корпусу чужих слайдов ноль везде, кроме самого бедного
#: шаблона (`WORKLOG/2026-09-20-z43-result.md`, замер 13).
_PENALTY_TYPEFACE = 1.0

#: **Но к штрафу за повтор эта величина обязана быть привязана, а не назначена
#: числом.** Замер показал, почему: при обоих штрафах по 0.25 они гасят друг
#: друга, и свежая раскладка с кодовым слотом обыгрывает уже использованную
#: обычную. Из 38 чужих слайдов по корпусу оставалось 20. А `config/variants.json`
#: гоняет `repeat` до 0.8, то есть до 2.4 с учётом потолка, — любое
#: назначенное число утонуло бы в трети политик, и как раз в тех, из которых
#: собираются варианты 2 и 3 сдаточных колод.
#:
#: Отсюда правило, одинаковое во всех политиках: **проза, набранная как код,
#: хуже любого повтора, который эта политика способна назначить.**
def typeface_penalty(tuning: Tuning) -> float:
    return _PENALTY_TYPEFACE + tuning.repeat * _REPEAT_CAP


#: Штраф за картинку, которой в этой раскладке негде встать (`Z-28a`,
#: `PLAN-7.10`). Привязан к штрафу за повтор по той же причине, что и штраф за
#: гарнитуру: `config/variants.json` гоняет `repeat` до 0.8, и назначенное
#: голым числом утонуло бы в трети политик — как раз в тех, из которых
#: собираются варианты 2 и 3 сдаточных колод.
#:
#: **Величина взята перебором эффективного значения, и она минимальная
#: работающая.** По всем четырнадцати шаблонам корпуса: при 0.0 картинок
#: вставляется 6, при 0.25 — семь, и дальше до 3.0 **ничего не меняется**.
#: Решает он ровно один случай — выданный VK Education, где без него картинка
#: не встаёт вовсе, а это треть сдачи. Размещённых знаков 32 186 при любом
#: значении: рычаг переставляет картинку, а не текст.
#:
#: Первая развёртка этого штрафа была **неверной** и чуть не привела к его
#: удалению: она подменяла базу, забыв про слагаемое от повтора, и «ноль» в
#: ней означал 0.75 — то есть всю измеренную полку разом. Отсюда правило:
#: перебирать **эффективное** значение, а не то, что написано в константе.
#:
#: **Почему штраф, а не пригодность.** Пока невставленная картинка попадала в
#: `leftover`, `Match.fits` объявлял раскладку непригодной — и на шаблоне без
#: слотов-иллюстраций непригодными становились **все**, а раздел пропадал
#: целиком вместе с текстом: минус 484 и 468 знаков на `60042` и
#: `prostoj-shablon`. Штраф этого не делает: где слота нет ни у кого, он
#: одинаков у всех и ранга не меняет.
#:
#: **Радиус действия — один раздел.** У разделов без картинок `dropped_images`
#: пуст, и штраф равен нулю: в отличие от правок, которые мерил `Z-49`, эта не
#: может сдвинуть колоду там, где картинок нет.
_PENALTY_DROPPED_IMAGE = 0.25


def dropped_image_penalty(tuning: Tuning) -> float:
    return _PENALTY_DROPPED_IMAGE + tuning.repeat * _REPEAT_CAP


#: Премия раскладке, где встала картинка генератора (`Z-28`, заготовка с
#: `min_side`). Картинка — украшение: штрафа за невставку у неё нет, в полноту
#: она не входит, премия малая. Замер 26 сентября на девятке (основной текст,
#: три шаблона, проверка PowerPoint): со штрафом, как у картинки автора, —
#: картинка в 7 колодах из 9, но 5 переполнений у предела читаемости на слайде с
#: ней (VK Tech) и картинка поверх заголовка в повёрнутой рамке (WorkSpace);
#: без премии и без довода вида — 0 переполнений и 0 картинок; премия 0.1 — 2
#: колоды, 0.2 — 3, все на Education, 0 переполнений.
_BONUS_GENERATED_IMAGE = 0.2



#: Штраф за то, что тезисы легли не в порядке чтения (`Z-31`, `PLAN-7.6`).
#: Умножается на долю переставленных пар: слайд, прочитанный задом наперёд,
#: платит полную величину, слайд с одной перестановкой из шести — шестую часть.
#:
#: **Назначение при этом не меняется: полнота важнее порядка.** На бедной
#: раскладке они несовместимы — на `p09` шаблона VK Tech ёмкости по чтению идут
#: 287, 18, 28, 46, а тезисы 25, 86, 50, 32, и расстановки, которая и влезает, и
#: сохраняет порядок, не существует. Поэтому штраф не переставляет тезисы, а
#: **уводит выбор** на раскладку, где противоречия нет.
#:
#: Величина взята перебором: полка начинается на 0.8, нарушений по корпусу
#: 32 → 19, и при этом не меняется ничего постороннего — ни число слайдов
#: (761), ни чужие гарнитуры (10), ни объём вне диапазона
#: (`WORKLOG/2026-09-20-z31-baseline.md`, замер 9).
#:
#: Меньше штрафа за чужую гарнитуру (1.0), и это осознанный порядок: слайд,
#: прочитанный задом наперёд, читается — просто не по порядку; проза,
#: набранная как код, выглядит не тем, чем является.
_PENALTY_DISORDER = 0.80

#: Виды гарнитур, которые штрафуются. `prose` и `None` — нет: первое значит
#: «смотрели, годится», второе — «судить не по чему» (слоты из макетов,
#: `ADR-0006`), и наказывать за несостоявшуюся проверку нечестно.
_FOREIGN_TYPEFACES = ("mono", "icon")


@dataclass(frozen=True)
class Tuning:
    """Три величины ранга, которые `ADR-0020` перебирает политиками.

    **Умолчания равны тому, что стоит в продукте**, поэтому вызов без `tuning`
    ведёт себя ровно как раньше. Это не вежливость к старому коду: девять
    сдаточных колод собираются той же командой, и молчаливая смена умолчания
    испортила бы их незаметно.

    Что сюда **не входит и не должно**: пороги весов (`quality.py`). Весы — это
    линейка, и если каждый вариант мерить своей, сравнение вариантов теряет
    смысл. Линейка одна на всех, политика меняет только ранг.
    """

    repeat: float = _REPEAT_PENALTY
    slack: float = _SLACK_RATIO
    over: float = _PENALTY_OVERFLOW
    #: Не величина политики, а обстоятельство сборки: зальёт ли генератор пустые
    #: рамки под фото после плана (`Z-55`). Нет — пустая рамка стоит
    #: `_PENALTY_EMPTY_FRAME` (`cli.py`, 28 сентября). Умолчание — как было.
    frames_later: bool = True


#: Настройка «как в продукте». Отдельным именем, чтобы в коде было видно, что
#: умолчание выбрано, а не забыто.
DEFAULT_TUNING = Tuning()


@dataclass(frozen=True)
class Match:
    pattern_id: str
    kind: str
    fills: tuple[Fill, ...]
    score: float
    reason: str
    leftover: tuple[str, ...]
    #: Доля переставленных пар: 0 — тезисы идут в порядке чтения, 1 — задом
    #: наперёд (`Z-31`). Порядок чтения брать неоткуда не надо: слоты в
    #: раскладке уже лежат в нём (`_shapes_of`), значит ранг слота — его номер.
    disorder: float = 0.0
    #: Виды чужих гарнитур слотов, в которые всё-таки лёг текст (`Z-43`).
    #: Непусто — раскладку взяли, зная, что проза там будет выглядеть кодом или
    #: пиктограммами; планировщик обязан сказать об этом вслух, а не промолчать.
    foreign: tuple[str, ...] = ()
    #: Картинки, которым в этой раскладке не нашлось слота-иллюстрации
    #: (`Z-28a`, `PLAN-7.10`). Держатся **отдельно от `leftover`**, и это не
    #: косметика, а исправление потери содержания.
    #:
    #: `leftover` означает «контент не влез, попробуй раздробить раздел», и
    #: `fits` по нему судит о пригодности раскладки. Для картинки это неверно:
    #: дробление не создаст слот под иллюстрацию там, где его нет во всём
    #: шаблоне. Замер: стоило картинке попасть в `leftover`, как на `60042` и
    #: `prostoj-shablon` **ни одна раскладка не признавалась пригодной**, и
    #: раздел «Картинки» пропадал целиком вместе со своим текстом — минус 484 и
    #: 468 знаков (`WORKLOG/2026-09-21-z28a-result.md`).
    #:
    #: Разница по смыслу: текст, который некуда положить, — потеря содержания;
    #: иллюстрация, которой негде стоять, — несостоявшееся украшение. Второе
    #: называется вслух в предупреждениях плана, но раскладку не отвергает.
    dropped_images: tuple[str, ...] = ()
    #: Картинки, поставленные в слот с заметно другой пропорцией: (путь, во
    #: сколько раз разошлось). Порог — `aspect_tolerance` в
    #: `config/images.json`. Не отказ, а именование: на выданном VK Education
    #: единственный слот-иллюстрация, куда ложится этот раздел, — круглый узел
    #: блок-схемы 1.00, а баннер 3.69, и других мест шаблон не предлагает.
    #: Отказаться значило бы не выполнить «встраивание изображений» на трети
    #: сдачи; промолчать — выдать сплющенную схему за задуманную.
    squeezed_images: tuple[tuple[str, float], ...] = ()

    @property
    def fits(self) -> bool:
        return not self.leftover


# --- ожидаемый вид раскладки -------------------------------------------


def preferred_kinds(section: ContentSection) -> tuple[str, ...]:
    """Какие раскладки просит этот кусок контента. Первая — самая подходящая.

    **Тип слайда от модели — довод, а не приказ** (`ADR-0023`, п. 3; `PLAN-9.0`,
    Ш3). Он встаёт первым, только если он уже среди видов, которые содержание
    раздела способно заполнить: под «metric» и «table» модель содержимого не
    даёт — тезисы у неё текстом, — и бонус увёл бы текст на макет с пустой
    таблицей или числами донора. Замер: так было бы у 18 слайдов из 106
    (`WORKLOG/2026-09-25-z57-sh3-baseline.md`, § 6). Новой величины нет:
    та же лестница 0.40 / 0.18. Оглавление код собирает сам, и у заказанного
    оглавления его вид — первый всегда. У пути без модели `kind` пуст, и ответ
    тот же, что до Ш3.
    """
    derived = _derived_kinds(section)
    ordered = section.kind
    if ordered == "agenda" or (ordered and ordered in derived):
        return (ordered,) + tuple(k for k in derived if k != ordered)
    return derived


def _derived_kinds(section: ContentSection) -> tuple[str, ...]:
    """Виды раскладок, которые просит само содержание раздела. Заготовка
    генератора (`Z-28`) здесь считается картинкой намеренно: раздел с идеей
    просит «картинку с текстом». Замер 26 сентября на девятке: без этого довода
    картинка не встала ни в одну колоду из девяти, с ним — в три колоды
    Education, удачно, при нуле переполнений."""
    kinds = section.kinds()
    lists = [b for b in section.blocks if b.kind == "list"]

    # Таблица и диаграмма с данными (`Z-32`) встают на место таблицы или
    # диаграммы донора, иллюстрации или крупного текста (`visual_host`):
    # раскладки этих видов такие места и несут.
    if "chart" in kinds:
        return ("chart", "table", "two_column", "image_text", "text")
    if "table" in kinds:
        return ("table", "chart", "two_column", "text", "image_text")
    if "metric" in kinds:
        return ("metric", "cards", "text")
    if "quote" in kinds:
        return ("quote", "text")
    if "image" in kinds and ("paragraph" in kinds or "list" in kinds):
        return ("image_text", "image_full", "text")
    if "image" in kinds:
        return ("image_full", "image_text")
    if lists:
        items = lists[0].items
        short = items and max(len(i) for i in items) <= 80
        if 2 <= len(items) <= 5 and short:
            return ("cards", "bullets", "two_column", "text")
        return ("bullets", "text", "cards")
    paragraphs = [b for b in section.blocks if b.kind == "paragraph"]
    if len(paragraphs) == 2:
        a, b = (len(p.text) for p in paragraphs)
        if max(a, b) <= min(a, b) * 1.6:
            return ("two_column", "text", "bullets")
    if not section.blocks:
        return ("section", "cover", "closing", "text")
    return ("text", "bullets", "two_column")


# --- назначение --------------------------------------------------------


def _by_role(slots: tuple[Slot, ...], roles: tuple[str, ...]) -> list[Slot]:
    return [s for s in slots if s.role in roles]


def _limit(slot: Slot) -> int | None:
    return slot.capacity.max_chars if slot.capacity else None


def _target(slot: Slot) -> int | None:
    return slot.capacity.target_chars if slot.capacity else None


def _overflow(slot: Slot, text: str) -> float:
    """На сколько текст превышает ёмкость слота. 0 — влезает."""
    cap = _limit(slot)
    if not cap:
        return 0.0
    return max(0.0, len(text) / cap - 1.0)


def _hard_limit(slot: Slot) -> float:
    """Во сколько раз текст может превысить ёмкость и слот всё ещё годится.

    Переполнение терпят потому, что его **чинит** стадия VERIFY: ужала кегль —
    стало меньше строк, текст влез (`ADR-0005`). У заслонённого слота этого
    запаса нет: текст в боксе **переносится**, и при меньшем кегле строка
    по-прежнему тянется до правого края бокса, то есть под картинку. Меньше
    кегль — меньше строк, а не у́же строка (`Z-47`).

    Замер, из-за которого правило появилось: сузить ёмкость мало. На VK Tech
    ёмкость слота `p25/s06` упала с 39 знаков до 32, а PLAN положил туда 49 —
    полтора потолка, потому что «переполнение поправимо». На растре у этого
    слайда обрезано «переполне[ний], стало 0»
    (`WORKLOG/2026-09-21-z48-result.md`).

    **Единица здесь не с потолка, и диапазон до 2.5 промерен целиком**
    (`Z-49`, `PLAN-7.9`). При 1.0, 1.1, 1.2, 1.3 и 1.5 пустых обязательных
    слотов по 14 шаблонам 223, 223, 221, 218, 220 — ответ **не монотонен**, то
    есть это шум перевыбора раскладок, а не рычаг. На сдаточном VK Tech
    ровно 12 при всех пяти значениях; прежние 9 возвращаются только при 2.5,
    то есть при полной отмене `Z-48`. Размещённых знаков 32 970 при каждом
    значении. Заводить задачу «подобрать значение между 1 и 2.5» второй раз
    не надо: между ними ничего нет.
    """
    if slot.occluded:
        return 1.0
    cap = _limit(slot)
    if cap and cap <= _CAPTION_CHARS and slot.role != "title":
        return _CAPTION_HARD
    return _OVERFLOW_HARD


#: Мелкое место — не заголовок, ёмкость до `_CAPTION_CHARS` знаков — принимает
#: текст не длиннее ёмкости (`Z-55`). Ремонт кеглем там не спасает: межстрочный
#: мелких подписей шаблона — под одну-две строки (на VK Tech 0.52–0.62 кегля), и
#: перенесённая фраза ложится строками друг на друга. Растр Ш9: на VK Tech фраза
#: в 23–43 знака в месте на 18 — на 21 слайде из 39 у колод модели. То место было
#: подсказкой «Вставить фото» в рамке под фото; с `Z-55` её узнаёт `find_frames`,
#: и текст туда не идёт вовсе. Правило осталось для прочих мелких мест —
#: номерных карточек, ячеек, подписей: на VK Tech их около трёхсот.
_CAPTION_CHARS = 20
_CAPTION_HARD = 1.0


def _fits(slot: Slot, text: str) -> bool:
    """Годится ли слот вообще. Умеренное переполнение — годится."""
    return _overflow(slot, text) <= _hard_limit(slot) - 1.0


def empty_places(pattern: Pattern, used) -> list[Slot]:
    """Обязательные места раскладки, оставшиеся пустыми: текстовые слоты и рамка
    под фото (`Z-55`).

    Рамка пуста, пока в неё не встала заготовка: картинку в неё дорисует
    генератор после плана, но выключенный генератор оставит белую карточку.
    Считается так же, как до `Z-55` считалась подсказка «Вставить фото» в ней, —
    ранг и планы прежние. Прочие слоты картинок необязательны."""
    return [s for s in pattern.slots if s.required and s.id not in used
            and (s.content_type != "image" or s.picture_kind == FRAME)]


def _max_items(slot: Slot) -> int | None:
    return slot.capacity.max_items if slot.capacity else None


#: Вид слота под картинку, в который мы кладём своё (`Z-28a`).
_ILLUSTRATION = "illustration"


def _by_aspect(slots: list[Slot], ref: str | None) -> list[Slot]:
    """Слоты-иллюстрации в порядке близости их пропорции к пропорции картинки.

    **Выбираем место под картинку, а не картинку под место** — обрезать и
    вписывать оказалось нельзя, и это показал замер (`PLAN-7.10`, правка после
    замера пропорций). Обрезка баннера 3.69 под слот 2.68 срезает 27% ширины,
    и у схемы из четырёх стадий исчезает одна: потеря содержания хуже лёгкого
    растяжения, ровно как в `Z-42`. Вписывание требует менять габариты фигуры
    донора, а вёрстка шаблона — его (`ADR-0004`).

    Подходящие слоты есть: на выданных шаблонах лучшее расхождение 1%, 10%,
    17%, 21% и 27%. Габариты фигуры при этом не трогаются вовсе.

    Размеры картинки не прочитались — порядок оставляем прежний: «проверить не
    смог» не повод переставлять слоты наугад. Сортировка **устойчивая**, так
    что при равных пропорциях порядок слотов остаётся порядком чтения (`Z-31`).
    """
    if not slots or not ref:
        return slots
    size = image_size(ref)
    if size is None:
        return slots
    width, height = size
    if not height:
        return slots
    want = width / height
    return sorted(
        slots,
        key=lambda s: abs((s.rect.cx / s.rect.cy if s.rect.cy else want) - want),
    )


def _crop(slot: Slot, ref: str | None) -> float | None:
    """Какую долю одной стороны картинки срежет рамка под фото, если залить её
    картинкой по центру без растяжения (`compose/substitute.py`, `a:srcRect`).
    `None` — размеры картинки не прочитались: «проверить не смог», и в рамку
    такая картинка не идёт."""
    if not ref or not slot.rect.cx or not slot.rect.cy:
        return None
    size = image_size(ref)
    if size is None or not size[0] or not size[1]:
        return None
    want = size[0] / size[1]
    have = slot.rect.cx / slot.rect.cy
    return 1 - min(want, have) / max(want, have)


def _clear_of_taken(slots: list[Slot], pattern: Pattern, used: set[str]) -> list[Slot]:
    """Слоты, не налезающие на уже занятый слот картинки или таблицы.

    **Нашёл это растр, а не числа** (`Z-51`, `PLAN-7.11`). У живых шаблонов
    слоты под картинку бывают **вложены друг в друга**: на `p20` выданного
    VK Tech `s04` 4.60×1.74 лежит целиком внутри `s03` 4.69×1.82 — рамка и
    фотография в ней, — а `s07` 0.79×0.60 внутри обоих. Планировщик видел три
    независимых места и клал в них три картинки; на растре они наезжали друг
    на друга. По корпусу таких **64 слота из 127** — считано уже после того,
    как вид слота научился отбрасывать прячущие подпись; до того было 55
    из 179.

    `Z-28a` чинил однофамильный дефект **в пакете**: две картинки на слайде
    делили одну часть `/ppt/media/` и вторая затирала первую. Здесь части
    разные и файл цел — рядом кладутся сами изображения. Числовая проверка
    этого поймать не может по построению: заливок столько, сколько задумано.

    Спрашивается только про занятые слоты **картинок и таблиц**. Наползание на
    текст — вопрос другой и решается раньше, видом слота
    (`analyze/picture.py`): туда наша картинка не попадёт вовсе.
    """
    taken = [
        s.rect for s in pattern.slots
        if s.id in used and s.content_type in ("image", "table", "chart")
    ]
    if not taken:
        return slots
    limit = load_picture_config().backdrop_overlap
    return [
        s for s in slots
        if not any(
            _rect_overlap(s.rect, t) > limit or _rect_overlap(t, s.rect) > limit
            for t in taken
        )
    ]


#: Где встаёт своя таблица или диаграмма, по старшинству (`Z-32`, `ADR-0026`):
#: место таблицы или диаграммы донора — оно и задумано под данные, а его числа
#: чужие (`Z-12`); место под иллюстрацию — крупное и свободное от текста;
#: крупный текстовый слот — тело слайда. Роли текста — только тело и список:
#: подпись и заголовок под таблицу не годятся.
_HOST_TEXT_ROLES = ("body", "bullet_list")


def _host_classes(slot: Slot) -> int | None:
    """Старшинство слота как места под таблицу или диаграмму; `None` — не место."""
    if slot.content_type in ("table", "chart"):
        return 0
    if slot.content_type == "image" and slot.picture_kind == _ILLUSTRATION:
        return 1
    if slot.content_type in ("text", "list") and slot.role in _HOST_TEXT_ROLES:
        return 2
    return None


def visual_host(pattern: Pattern, block: ContentBlock, used=frozenset()) -> Slot | None:
    """Слот, на место которого встанет таблица или диаграмма `block`.

    Слот свободен, не заслонён (`Z-48`: поверх него лежит непрозрачное) и не
    мельче `block.min_side`; не налезает на уже занятое место картинки или
    таблицы (`_clear_of_taken`). Из годных старшего класса — самый крупный: у
    таблицы и диаграммы площадь решает, прочтут ли их; при равной площади —
    первый в порядке чтения."""
    free = [s for s in pattern.slots
            if s.id not in used and not s.occluded and _host_classes(s) is not None
            and (block.min_side is None or min(s.rect.cx, s.rect.cy) >= block.min_side)]
    free = _clear_of_taken(free, pattern, set(used))
    # И не накрывает уже занятое текстом — заголовок прежде всего: таблица
    # встаёт поверх в порядке фигур хозяина и спрятала бы его.
    limit = load_picture_config().backdrop_overlap
    taken = [s.rect for s in pattern.slots if s.id in used]
    free = [s for s in free if not any(_rect_overlap(t, s.rect) > limit for t in taken)]
    if not free:
        return None
    best = min(_host_classes(s) for s in free)
    order = {s.id: i for i, s in enumerate(pattern.slots)}
    return max((s for s in free if _host_classes(s) == best),
               key=lambda s: (s.rect.cx * s.rect.cy, -order[s.id]))


def _aspect_gap(slot: Slot, ref: str | None) -> float | None:
    """Во сколько раз пропорция слота расходится с пропорцией картинки.

    `None` — размеры прочесть не удалось, и это ответ «не смог», а не «сошлось».
    """
    if not ref or not slot.rect.cy:
        return None
    size = image_size(ref)
    if size is None or not size[1]:
        return None
    want = size[0] / size[1]
    got = slot.rect.cx / slot.rect.cy
    if not want:
        return None
    return abs(got - want) / want


@functools.lru_cache(maxsize=1)
def _aspect_tolerance() -> float:
    """Порог из `config/images.json`. Читается один раз на прогон."""
    return load_picture_config().aspect_tolerance


def _pick(slots: list[Slot], text: str) -> Slot | None:
    """Первый по порядку слот, в который текст действительно влезает.

    Раньше брался просто `slots[0]`, и раскладка объявлялась непригодной, если
    её первый текстовый слот мал. Замер (`PLAN-2.1`, шаг 1): у раскладки `p03`
    десять текстовых слотов — ёмкости 5, 5, 65, 5, 5, 65, 5, 5, 5, 65, — где
    пятёрки это номерные бейджи. Абзац в 142 знака влезал в любой из слотов на
    65, но проверялся только бейдж, и весь паттерн отсеивался. Так пул
    пригодных раскладок сужался до двух-трёх, а из узкого пула шли повторы.

    Порядок обхода сохраняется: берётся первый подходящий, а не самый крупный.
    Иначе текст уезжал бы в случайное место раскладки и ломал чтение.
    """
    for slot in slots:
        if _fits(slot, text):
            return slot
    return None


def match(
    section: ContentSection, pattern: Pattern, tuning: Tuning = DEFAULT_TUNING
) -> Match | None:
    """Пробует уложить раздел в паттерн. None — если не годится в принципе."""
    used: set[str] = set()
    fills: list[Fill] = []
    leftover: list[str] = []
    dropped_images: list[str] = []
    squeezed_images: list[tuple[str, float]] = []
    slack: list[float] = []
    overflows: list[float] = []
    foreign: list[str] = []

    def take(slot: Slot, text_for_fit: str = "", **kwargs) -> None:
        used.add(slot.id)
        over = _overflow(slot, text_for_fit) if text_for_fit else 0.0
        overflows.append(over)
        # Гарнитура важна только там, куда ложится текст: `take` зовётся и для
        # слотов изображения и таблицы (`PLAN-7.3`, проверка первая, находка 5).
        if kwargs.get("kind") in ("text", "list", "number")                 and slot.typeface_kind in _FOREIGN_TYPEFACES:
            foreign.append(slot.typeface_kind)
        fills.append(Fill(slot_id=slot.id, over_capacity=over > 0, **kwargs))

    def note_slack(slot: Slot, length: int) -> None:
        target = _target(slot)
        if target:
            slack.append(min(1.0, length / target))

    free = lambda roles: [s for s in _by_role(pattern.slots, roles) if s.id not in used]  # noqa: E731

    # 1. Заголовок раздела.
    if section.heading:
        head_slots = (
            free(_TITLE_ROLES) or free(_SUBTITLE_ROLES)
            or [s for s in free(("body", "label", "caption")) if s.content_type == "text"]
        )
        head_slot = _pick(head_slots, section.heading)
        if head_slot is not None:
            take(head_slot, text_for_fit=section.heading, kind="text", text=section.heading)
            note_slack(head_slot, len(section.heading))
        else:
            leftover.append(f"{section.id}:heading")

    # 2. Метрики: число в свой слот, подпись — в соседний.
    #
    #    Если плашки под число в шаблоне нет вовсе, метрика становится обычным
    #    текстом. Замер показал, зачем: слот `metric_value` был всего у трёх
    #    паттернов из четырнадцати, самых плотных, и требование «только туда»
    #    загоняло планировщик в раскладку на двадцать слотов ради двух блоков
    #    контента. Терять содержимое из-за отсутствия плашки нельзя.
    for block in (b for b in section.blocks if b.kind == "metric"):
        value_slots = free(_METRIC_VALUE_ROLES)
        if value_slots and _fits(value_slots[0], block.value or ""):
            take(value_slots[0], text_for_fit=block.value or "", kind="number", text=block.value)
            note_slack(value_slots[0], len(block.value or ""))
            if block.label:
                label_slots = free(_METRIC_LABEL_ROLES)
                if label_slots and _fits(label_slots[0], block.label):
                    take(label_slots[0], text_for_fit=block.label, kind="text", text=block.label)
            continue

        merged = block.value or ""
        if block.label:
            merged = f"{merged} \u2014 {block.label}"
        text_slots = [s for s in free(_TEXT_ROLES) if s.content_type == "text"]
        slot = _pick(text_slots, merged)
        if slot is not None:
            take(slot, text_for_fit=merged, kind="text", text=merged)
            note_slack(slot, len(merged))
        else:
            leftover.append(block.id)

    # 3. Изображения, таблицы, диаграммы — только если слот под них есть.
    for block in (b for b in section.blocks if b.kind in ("image", "table", "chart")):
        if block.kind in ("table", "chart") and block.data is not None:
            # Своя таблица или диаграмма (`Z-32`, `ADR-0026`): встаёт на место
            # фигуры-хозяина. Нет хозяина — раскладка разделу не годится, и
            # раздел дробится, как всегда; нет его во всём шаблоне — данные
            # заранее стали списком (`visual.without_hosts`).
            host = visual_host(pattern, block, used)
            if host is None:
                leftover.append(block.id)
                continue
            take(host, kind=block.kind, data=block.data)
            # Текстовые места, которые таблица накроет, текста не получают:
            # сборка их очистит, как любой незаполненный слот. Порог мал
            # намеренно: растр 26 сентября (WorkSpace, `p23`) — карточка с
            # тезисом накрывала место диаграммы на 48 % своей площади, прошла
            # прежний порог в половину, и рамке после отступа от текста
            # (`compose/visual.clear_of`) остался угол.
            limit = load_picture_config().visual_text_overlap
            for s in pattern.slots:
                if (s.id not in used and s.content_type in ("text", "list")
                        and _rect_overlap(s.rect, host.rect) > limit):
                    used.add(s.id)
            continue
        if block.kind == "chart":
            leftover.append(block.id)
            continue
        role = "image" if block.kind == "image" else "table"
        slots = free((role,))
        if role == "image":
            # Не всякий слот под картинку — место под иллюстрацию. Иконка,
            # подложка карточки и фон во весь слайд имеют тот же
            # `content_type`, и наша схема в них портит слайд: замер дал 688
            # слотов по корпусу и 135 годных (`Z-28a`, `PLAN-7.10`, шаг 3).
            #
            # Здесь **запрет, а не штраф**, и это отличает случай от `Z-43`:
            # там раскладку со слотом под код бывает не на что заменить, и
            # текст всё равно надо куда-то положить. Здесь класть необязательно
            # — картинка не потеряется, она просто не встанет, и об этом
            # скажет предупреждение плана.
            #
            # Рамка под фото (`Z-55`) — место и для заготовки генератора (та
            # рисуется в пропорции рамки), и с 28 сентября для картинки автора:
            # её сборка обрезает по центру под пропорцию рамки, не растягивая,
            # если срезать нужно не больше `frame_crop_max` стороны
            # (`config/images.json`) — у схемы край несёт содержание.
            slots = [s for s in slots if s.picture_kind in (_ILLUSTRATION, FRAME)]
            if block.min_side:
                # Не мельче порога: у заготовки — `min_place_side` генератора,
                # у картинки автора — `author_min_side` (миниатюра на фигуре
                # донора хуже слайда без неё; проверка готовыми фото 28 сентября).
                slots = [s for s in slots if min(s.rect.cx, s.rect.cy) >= block.min_side]
            if not block.generated:
                limit = load_picture_config().frame_crop_max
                slots = [s for s in slots if s.picture_kind != FRAME
                         or (_crop(s, block.ref) is not None and _crop(s, block.ref) <= limit)]
            slots = _clear_of_taken(slots, pattern, used)
            slots = _by_aspect(slots, block.ref)
            if not block.generated:
                # Рамки — первыми, с меньшей обрезкой впереди: их дизайнер задумал
                # под фото. Сортировка устойчивая — прочее в прежнем порядке.
                def frames_first(s: Slot) -> tuple[int, float]:
                    if s.picture_kind != FRAME:
                        return 1, 0.0
                    return 0, _crop(s, block.ref) or 0.0

                slots = sorted(slots, key=frames_first)
            if not slots:
                # Не `leftover`: см. `Match.dropped_images`. Дробление раздела
                # слота под иллюстрацию не создаст, а отвергнутая раскладка
                # унесла бы с собой весь текст раздела.
                dropped_images.append(block.ref or block.id)
                continue
        if not slots:
            leftover.append(block.id)
            continue
        if role == "image" and slots[0].picture_kind != FRAME:
            # В рамке картинка обрезается под её пропорцию, а не растягивается.
            off = _aspect_gap(slots[0], block.ref)
            if off is not None and off > _aspect_tolerance():
                squeezed_images.append((block.ref or block.id, off))
        take(slots[0], kind=role, ref=block.ref, text=block.text or None)

    # 4. Списки, абзацы и цитаты — **одним проходом в порядке документа**.
    #
    #    Раньше проходов было два: сначала все списки, потом все абзацы. Порядок
    #    документа между видами при этом терялся целиком, и раздел «В чём вообще
    #    беда» выходил на слайд задом наперёд: абзац, стоявший в тексте первым,
    #    занимал третью карточку, а список — первую и вторую. Замер: четырнадцать
    #    слайдов корпуса из сорока испорченных — этот механизм
    #    (`Z-31`, `PLAN-7.6`, замер 3).
    #
    #    Метрики и картинки остаются **раньше** и в общую очередь не идут: они
    #    занимают свои особые слоты, и пущенная по порядку метрика заняла бы
    #    обычный текстовый, оставив плашку под число пустой (`PLAN-2.0`).
    for block in section.blocks:
        if block.kind == "list":
            bullet_slots = [s for s in free(_TEXT_ROLES) if s.content_type == "list"]
            whole = next(
                (
                    s for s in bullet_slots
                    if len(block.items) <= (_max_items(s) or len(block.items))
                    and all(_fits(s, i) for i in block.items)
                ),
                None,
            )
            if whole is not None:
                take(whole, text_for_fit=max(block.items, key=len),
                     kind="list", items=tuple(block.items))
                note_slack(whole, block.length)
                continue

            # По слоту на пункт. Слоты перебираются по порядку, но негодные по
            # ёмкости пропускаются (`PLAN-2.1`, шаг 1): раньше пункт жёстко
            # ложился в слот с тем же номером, и раскладка с номерными бейджами
            # впереди отсеивалась целиком.
            text_slots = [s for s in free(_TEXT_ROLES) if s.content_type == "text"]
            chosen: list[Slot] = []
            rest = list(text_slots)
            for item in block.items:
                slot = _pick(rest, item)
                if slot is None:
                    chosen = []
                    break
                chosen.append(slot)
                rest = [s for s in rest if s.id != slot.id]
            if chosen:
                for slot, item in zip(chosen, block.items):
                    take(slot, text_for_fit=item, kind="text", text=item)
                    note_slack(slot, len(item))
            else:
                leftover.append(block.id)

        elif block.kind in ("paragraph", "quote"):
            # Если текстовых слотов не осталось, годится и слот-список: абзац
            # станет одним пунктом. На реальных шаблонах слоты-списки самые
            # вместительные.
            text_slots = [s for s in free(_TEXT_ROLES) if s.content_type == "text"]
            list_slots = [s for s in free(_TEXT_ROLES) if s.content_type == "list"]
            as_text = _pick(text_slots, block.text)
            as_list = _pick(list_slots, block.text)
            if as_text is not None:
                take(as_text, text_for_fit=block.text, kind="text", text=block.text)
                note_slack(as_text, len(block.text))
            elif as_list is not None:
                take(as_list, text_for_fit=block.text, kind="list", items=(block.text,))
                note_slack(as_list, len(block.text))
            else:
                leftover.append(block.id)

    if not fills:
        return None

    # Насколько порядок тезисов разошёлся с порядком чтения (`Z-31`,
    # `PLAN-7.6`). Слоты раскладки уже лежат в порядке чтения, поэтому ранг
    # слота — просто его номер, и геометрия сюда не тянется: граница слоёв
    # цела, а порог полосы остаётся один, в `analyze`.
    rank_of = {s.id: i for i, s in enumerate(pattern.slots)}
    body_order = [
        rank_of[f.slot_id]
        for f in fills
        if f.kind in ("text", "list", "number")
        and rank_of.get(f.slot_id) is not None
        and next((s.role for s in pattern.slots if s.id == f.slot_id), "") not in
        ("title", "subtitle")
    ]
    pairs = len(body_order) * (len(body_order) - 1) // 2
    inversions = sum(
        1
        for i in range(len(body_order))
        for j in range(i + 1, len(body_order))
        if body_order[i] > body_order[j]
    )
    disorder = inversions / pairs if pairs else 0.0

    # --- ранг ---
    # Заготовка генератора (`Z-28`, `min_side`) — украшение: выбор раскладки она
    # не решает — ни полнотой, ни штрафом за невставку. Встаёт, только если у
    # лучшей по тексту раскладки есть крупное место. Замер 26 сентября: со штрафом
    # ранг брал ради картинки тесную раскладку, и на двух колодах VK Tech текст
    # слайда с картинкой остался переполнен у предела читаемости (5 из 5 остатков).
    generated = {b.ref for b in section.blocks if b.kind == "image" and b.generated}
    total_units = sum(b.units for b in section.blocks
                      if not (b.kind == "image" and b.generated)) + (1 if section.heading else 0)
    placed_units = sum(len(f.items or ()) if f.kind == "list" else 1 for f in fills
                       if not (f.kind == "image" and f.ref in generated))
    coverage = placed_units / total_units if total_units else 1.0

    wanted = preferred_kinds(section)
    if wanted and pattern.kind == wanted[0]:
        affinity = _BONUS_BEST_KIND
    elif pattern.kind in wanted:
        affinity = _BONUS_ANY_KIND
    else:
        affinity = 0.0

    empty = empty_places(pattern, used)
    empty_required = len(empty)
    # Рамку, которую после плана некому залить (генератор выключен или колоду
    # строила не модель), ранг обходит: белая карточка во весь угол слайда —
    # дыра, как чужие числа донора (`_PENALTY_EMPTY_FRAME`).
    dead_frames = 0 if tuning.frames_later else sum(1 for s in empty if s.picture_kind == FRAME)
    thin = sum(1 for ratio in slack if ratio < tuning.slack)
    over = sum(overflows) / len(overflows) if overflows else 0.0
    # Таблица или диаграмма донора, на место которой встала наша (`Z-32`), —
    # уже не чужие числа: штраф только за оставшиеся.
    donor_data = sum(1 for s in pattern.slots
                     if s.content_type in ("table", "chart") and s.id not in used)

    score = (
        coverage
        + affinity
        - _PENALTY_EMPTY_SLOT * empty_required
        - (_PENALTY_EMPTY_FRAME - _PENALTY_EMPTY_SLOT) * dead_frames
        - _PENALTY_DONOR_DATA * donor_data
        - _PENALTY_SLACK * (thin / max(1, len(slack)))
        - tuning.over * over
        - typeface_penalty(tuning) * len(foreign)
        - dropped_image_penalty(tuning) * sum(1 for r in dropped_images if r not in generated)
        + _BONUS_GENERATED_IMAGE * sum(1 for f in fills if f.kind == "image" and f.ref in generated)
        - _PENALTY_DISORDER * disorder
    )

    return Match(
        pattern_id=pattern.id,
        kind=pattern.kind,
        fills=tuple(fills),
        score=round(score, 4),
        reason=_reason(section, pattern, placed_units, empty_required, wanted, over,
                       tuple(foreign)),
        leftover=tuple(leftover),
        dropped_images=tuple(dropped_images),
        squeezed_images=tuple(squeezed_images),
        foreign=tuple(foreign),
        disorder=round(disorder, 4),
    )


#: Как называть чужую гарнитуру человеку. Это показывают эксперту, а не пишут
#: в лог, поэтому по-русски и без наших внутренних имён.
_FOREIGN_WORDS = {
    "mono": "моноширинным шрифтом, как код",
    "icon": "пиктограммным шрифтом",
}


def _reason(
    section: ContentSection,
    pattern: Pattern,
    placed: int,
    empty: int,
    wanted: tuple[str, ...],
    over: float = 0.0,
    foreign: tuple[str, ...] = (),
) -> str:
    """Человекочитаемое обоснование. Это показывают эксперту, а не пишут в лог."""
    shape = []
    for block in section.blocks:
        if block.kind in ("table", "chart") and block.data is not None:
            shape.append("таблица" if block.kind == "table" else "диаграмма")
    lists = [b for b in section.blocks if b.kind == "list"]
    if lists:
        shape.append(f"{len(lists[0].items)} равноправных пункта")
    metrics = [b for b in section.blocks if b.kind == "metric"]
    if metrics:
        shape.append(f"{len(metrics)} числовых показателя")
    paragraphs = [b for b in section.blocks if b.kind == "paragraph"]
    if paragraphs:
        shape.append(f"{len(paragraphs)} абзаца текста")
    if not shape:
        shape.append("только заголовок")

    head = ", ".join(shape)
    fit = "вид совпал" if wanted and pattern.kind == wanted[0] else f"вид «{pattern.kind}»"
    tail = f", {empty} слот(ов) осталось пустыми" if empty else ""
    if over > 0.02:
        tail += f", текст плотнее ёмкости на {over:.0%} — ужмётся на проверке вёрстки"
    for kind in sorted(set(foreign)):
        n = sum(1 for f in foreign if f == kind)
        tail += f", {n} слот(ов) набраны {_FOREIGN_WORDS.get(kind, kind)}"
    return f"{head} — {fit}, заполнено {placed} мест{tail}"


def rank(
    section: ContentSection,
    patterns: tuple[Pattern, ...],
    tuning: Tuning = DEFAULT_TUNING,
) -> list[Match]:
    """Пригодные паттерны, от лучшего к худшему. Порядок детерминирован."""
    found = []
    for pattern in patterns:
        m = match(section, pattern, tuning)
        if m is not None and m.fits:
            found.append(m)
    return sorted(found, key=lambda m: (-m.score, m.pattern_id))
