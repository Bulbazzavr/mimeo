"""Структура из сплошной прозы. Задача `Z-25`, план `PLAN-2.2`, решение `ADR-0016`.

`content.py` разбирает разметку и ничего не домысливает. Здесь — единственное
место, где структура **выводится** из текста без разметки: предложения режутся
на тезисы, тезисы группируются в темы, тема получает заголовок.

Точка приложения выбрана замером (`WORKLOG/2026-09-14-prose-input.md`): механизм
дробления раздела исправен, ему нечего дробить — сплошной абзац для `split` один
неделимый атом, и четыре колоды из одиннадцати выходили пустыми. Поэтому задача
сегментатора ровно одна: **превратить прозу в атомы.** Подгонку под конкретную
раскладку делает уже написанное — `split` и `_place` в `deterministic.py`.

Модели здесь нет (`ADR-0009`): инференс дают только с топ-10, а демо обязано
работать без сети. Модельный слой поверх — `Z-08`; ради него `restructure`
сохраняет исходный текст в `ContentDoc.origin`.

Пороги и языковые подсказки лежат в `config/prose.json`, а не в коде
(`CTX-ANSWERS`, п. 10). Без файла работают встроенные значения, и это видно в
предупреждениях плана.
"""

from __future__ import annotations

import functools
import json
import os
import re
from dataclasses import dataclass

from .content import ContentBlock, ContentDoc, ContentSection
from .content import extract_images, load as load_raw

# --- конфигурация ------------------------------------------------------

#: Встроенные значения. Числа — из замера ёмкости по корпусу
#: (`WORKLOG/2026-09-14-prose-input.md`): медиана `body` 64 знака, `title` 33,
#: `max_items` 4. Это запасной набор: основной лежит в `config/prose.json`.
_LIMITS = {
    "thesis_target": 64,
    "thesis_max": 160,
    "thesis_min": 15,
    "heading_max": 48,
    "topic_min": 3,
    "topic_max": 6,
    "prose_paragraph": 200,
}

#: Сочинительные союзы. Встроенный набор остаётся и без конфига: без него
#: разрез вернулся бы к счёту знаков и снова давал бы обрывки (`Z-42`).
_CLAUSE_CONJUNCTIONS = ("а", "и", "но", "однако", "зато", "причём", "причем")

#: Сокращения нужны, чтобы «т. е.» не разрывало предложение. Встроенный минимум
#: остаётся и без конфига: без него разрез пришёлся бы на середину фразы.
_ABBREVIATIONS = frozenset(
    "т е д п др пр им см ср ок гг г в вв руб коп тыс млн млрд шт кг км рис".split()
)


@dataclass(frozen=True)
class ProseConfig:
    """Пороги и словари сегментации. `loaded` отличает «конфиг прочитан» от
    «работают встроенные значения» — правило `CLAUDE.md` про измеритель."""

    thesis_target: int = _LIMITS["thesis_target"]
    thesis_max: int = _LIMITS["thesis_max"]
    thesis_min: int = _LIMITS["thesis_min"]
    heading_max: int = _LIMITS["heading_max"]
    topic_min: int = _LIMITS["topic_min"]
    topic_max: int = _LIMITS["topic_max"]
    prose_paragraph: int = _LIMITS["prose_paragraph"]
    abbreviations: frozenset[str] = _ABBREVIATIONS
    topic_shift: tuple[str, ...] = ()
    #: Сочинительные союзы: запятая перед таким союзом — граница мысли
    #: (`Z-42`, `PLAN-7.4`).
    clause_conjunctions: tuple[str, ...] = _CLAUSE_CONJUNCTIONS
    #: Наречия и частицы о речи. Снимаются только в **нашем** разрезе заголовка.
    discourse_adverbs: tuple[str, ...] = ()
    #: Рамка перед придаточным: «про то, что», «о том, что».
    clause_frames: tuple[str, ...] = ()
    #: Первое лицо единственного числа глаголов `address_verbs`.
    self_verbs: tuple[str, ...] = ()
    lead_in: tuple[str, ...] = ()
    address_verbs: tuple[str, ...] = ()
    address_fillers: tuple[str, ...] = ()
    deck_title: tuple[str, ...] = ()
    #: Предлоги, которые остаются в заголовке: без них ломается падеж.
    deck_title_keep: tuple[str, ...] = ()
    stopwords: frozenset[str] = frozenset()
    loaded: bool = False
    source: str = "встроенные значения"


def config_path() -> str:
    """`config/prose.json` рядом с пакетом: `mimeo/` лежит в корне репозитория."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    return os.path.join(root, "config", "prose.json")


def load_config(path: str | None = None) -> ProseConfig:
    """Читает конфиг. Отсутствие файла — не ошибка, а работа на встроенных
    значениях; вызывающий узнаёт об этом по `loaded`, а не по молчанию."""
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return ProseConfig()

    limits = dict(_LIMITS)
    for key, value in (raw.get("limits") or {}).items():
        if key in limits and isinstance(value, int):
            limits[key] = value

    def words(key: str, fallback: frozenset[str] = frozenset()) -> frozenset[str]:
        value = raw.get(key)
        if not isinstance(value, list) or not value:
            return fallback
        return frozenset(str(x).lower() for x in value)

    def words_list(key: str) -> tuple[str, ...]:
        """Список слов как есть, без сортировки по длине: порядок тут не значим."""
        value = raw.get(key)
        if not isinstance(value, list):
            return ()
        return tuple(str(x).lower() for x in value)

    def phrases(key: str) -> tuple[str, ...]:
        value = raw.get(key)
        if not isinstance(value, list):
            return ()
        # Длинные впереди: «ещё важно сказать про» должно сработать раньше,
        # чем «ещё важно». Порядок фиксирован сортировкой, а не файлом.
        return tuple(sorted((str(x).lower() for x in value), key=lambda s: (-len(s), s)))

    return ProseConfig(
        abbreviations=words("abbreviations", _ABBREVIATIONS),
        stopwords=words("stopwords"),
        topic_shift=phrases("topic_shift"),
        clause_conjunctions=(words_list("clause_conjunctions")
                             or _CLAUSE_CONJUNCTIONS),
        discourse_adverbs=phrases("discourse_adverbs"),
        clause_frames=phrases("clause_frames"),
        self_verbs=words_list("self_verbs"),
        lead_in=phrases("lead_in"),
        address_verbs=phrases("address_verbs"),
        address_fillers=phrases("address_fillers"),
        deck_title=phrases("deck_title"),
        deck_title_keep=phrases("deck_title_keep"),
        loaded=True,
        source=path,
        **limits,
    )


# --- предложения -------------------------------------------------------

_SENT_END = re.compile(r"[.!?…]+(\s+)")
#: Последнее слово перед точкой — чтобы узнать сокращение и инициал.
_TAIL_WORD = re.compile(r"([^\s.]+)\.$")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _abbrev_tail(head: str, cfg: ProseConfig) -> bool:
    m = _TAIL_WORD.search(head)
    if not m:
        return False
    word = m.group(1).lower()
    return word in cfg.abbreviations or (len(word) == 1 and word.isalpha())


def _opens_sentence(rest: str) -> bool:
    """Новое предложение начинается с прописной, кавычки или числа. Строчная
    после точки означает, что точка была не концом предложения."""
    if not rest:
        return False
    ch = rest[0]
    if ch in "«\"'([":
        nxt = rest[1:2]
        return bool(nxt) and (nxt.isupper() or nxt.isdigit())
    return ch.isupper() or ch.isdigit()


def split_sentences(text: str, cfg: ProseConfig) -> tuple[str, ...]:
    """Режет абзац на предложения, не разрывая сокращения, дроби и инициалы."""
    text = " ".join(text.split())
    out: list[str] = []
    start = 0
    for m in _SENT_END.finditer(text):
        head = text[start:m.start(1)].strip()
        if not head or _abbrev_tail(head, cfg) or not _opens_sentence(text[m.end(1):]):
            continue
        out.append(head)
        start = m.end(1)
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return tuple(out)


# --- тезисы ------------------------------------------------------------

#: Сильные разделители внутри предложения. Запятая сюда не входит осознанно:
#: «у них дешевле логистика, но нет контрактов» распадается по ней на огрызки.
_STRONG = re.compile(r"\s+[—–]\s+|;\s+")
_COMMA = re.compile(r",\s+")
#: Короткая часть до двоеточия — метка темы: «Наша модель: подписка…».
_LABEL = re.compile(r"^([^:]{3,64}):\s+(.+)$")


#: Докуда от начала предложения ищем обращение к исполнителю. Дальше — уже
#: содержание: глагол в повелительном наклонении в середине длинной фразы
#: скорее часть текста, чем указание ассистенту. Замер: все десять обращений
#: по корпусу начинаются в первых 60 знаках (`Z-41`).
_ADDRESS_REACH = 60

#: Что может стоять после глагола и вводить содержание: «скажи, **что**…»,
#: «упомяни **про**…». Отдельно от заполнителей: это не мусор, а рамка.
_ADDRESS_FRAME = r"о том,? что|про|об|о|что"

#: Безличное обращение: модальное слово плюс инфинитив — «нужно рассказать
#: про», «стоит упомянуть, что». Глагола второго лица здесь нет, и прямая
#: конструкция такое не видит.
_ADDRESS_MODAL = r"нужно|надо|стоит|важно|хочу|хочется|можно|давай(?:те)?"
_ADDRESS_INF = (
    r"сказать|рассказать|упомянуть|написать|отметить|добавить|показать|"
    r"объяснить|подчеркнуть|перечислить|описать|начать|завершить|сделать"
)


def _address_re(cfg: ProseConfig) -> re.Pattern[str] | None:
    """Регулярка обращения к исполнителю, собранная из конфига (`Z-41`).

    Повторы **ограничены** `{0,3}` и `{0,2}` не для красоты: со звёздочками
    вложенные альтернативы дают катастрофический бэктрекинг, и первая же
    версия этой регулярки повесила прогон на две минуты.
    """
    if not cfg.address_verbs:
        return None
    verbs = "|".join(cfg.address_verbs)
    fillers = "|".join(cfg.address_fillers) or r"(?!)"
    # Две конструкции, а не одна. Прямая — глагол второго лица: «скажи».
    # Безличная — модальное слово плюс инфинитив: «нужно рассказать про».
    # Вторая нашлась чтением вывода: заголовок «Отдельно нужно рассказать про
    # проверку вёрстки» пережил первую редакцию правила.
    core = rf"(?:{_ADDRESS_MODAL})[ ,]+(?:{_ADDRESS_INF})|{verbs}"
    return re.compile(
        rf"(?:\b(?:{fillers})\b[ ,]+){{0,3}}"
        rf"\b(?:{core})\b"
        rf"(?:[ ,]+\b(?:{fillers})\b){{0,2}}"
        rf"(?:[ ,]*\b(?:{_ADDRESS_FRAME})\b)?"
        rf"[ ,:—–]*",
        re.IGNORECASE,
    )


@functools.lru_cache(maxsize=8)
def _address_re_cached(verbs: tuple[str, ...], fillers: tuple[str, ...]):
    return _address_re(ProseConfig(address_verbs=verbs, address_fillers=fillers))


def _strip_address(text: str, cfg: ProseConfig) -> str | None:
    """Убирает обращение к исполнителю: «Дальше скажи, что…», «В конце
    попроси…». Возвращает `None`, если обращения нет.

    **Почему не хватило списка фраз.** До 19 сентября обращения снимал только
    `lead_in` — 23 литерала, и все якорем на начало предложения. Замер по
    корпусу: обращений десять, и **в половине глагол стоит не первым** («Про
    зависимости тоже упомяни…», «В конце попроси…»). Правило «глагол в начале»,
    как оно было записано в задаче, поймало бы четыре из восьми (`Z-41`).

    **Почему нельзя просто дописать фраз в список.** Способов обратиться к
    ассистенту столько же, сколько способов говорить; словарь неполон по
    построению. Считается не фраза, а конструкция: глагол второго лица
    повелительного наклонения плюс рамка ввода содержания.
    """
    rx = _address_re_cached(cfg.address_verbs, cfg.address_fillers)
    if rx is None:
        return None
    m = rx.search(text)
    if m is None or m.start() > _ADDRESS_REACH or not m.group().strip(" ,:—–"):
        return None
    before = text[: m.start()].strip(" ,:—–")
    after = text[m.end() :].strip(" ,:—–")
    if not after:
        return None
    if not before:
        return after

    # Обращение в середине: «Про зависимости тоже упомяни, я этим горжусь: у
    # движка их ноль», «Риски тоже нужно упомянуть честно: сезонность…». То,
    # что стоит **до** глагола, — тема, и терять её жалко.
    #
    # Склеивать `before` с `after` в одну строку **нельзя**, и это не
    # придирка: `test_text_survives_any_target` поймал ровно это. Любой кусок
    # в выводе обязан находиться в исходнике дословно, иначе движок начинает
    # сочинять — то, что `CLAUDE.md` запрещает прямо. Поэтому сюда ставится
    # двоеточие, а разбирает его `_LABEL` в `_units`: тема уходит в заголовок,
    # остаток — в тезис, и **оба куска остаются подстроками исходника**.
    return f"{before}: {after}"


def has_address(text: str, cfg: ProseConfig | None = None) -> bool:
    """Есть ли в строке обращение к исполнителю — тем же признаком, которым путь
    без модели снимает его с прозы (`Z-41`). Им же проверяется ответ модели:
    «Не забудь про картинки» на слайде — отказ колоде (`ADR-0023`, п. 5)."""
    return _strip_address(text, cfg or load_config()) is not None


def _strip_lead_in(text: str, cfg: ProseConfig) -> tuple[str, int]:
    """Снимает вводный оборот («расскажи, что…»). Возвращает текст и сколько
    знаков отброшено: потеря считается, а не замалчивается (`PLAN-2.2`).

    Два прохода, и оба нужны, но **конструкция идёт первой**. Она снимает
    больше: на «Расскажи сначала, в чём вообще беда» литерал `расскажи`
    останавливается сразу за глаголом и оставляет заголовок «Сначала, в чём
    вообще беда». Порядок виден только в собранном заголовке, замером его не
    поймать (`Z-41`).

    Список `lead_in` остаётся вторым проходом и нужен для оборотов, которые
    конструкцией не описываются: «тоже нужно упомянуть», «хочу сказать, что».
    """
    rest = _strip_address(text, cfg)
    if rest is not None and len(rest) >= cfg.thesis_min:
        return _capitalize(rest), len(text) - len(rest)

    low = text.lower()
    for phrase in cfg.lead_in:
        if low.startswith(phrase):
            rest = text[len(phrase):].lstrip(" ,:—–")
            if len(rest) >= cfg.thesis_min:
                return _capitalize(rest), len(text) - len(rest)
    return text, 0


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _tidy(thesis: str) -> str:
    """Тезис на слайде: с прописной и без точки в конце. Точка остаётся у
    вопроса и восклицания — там она часть смысла."""
    thesis = thesis.strip().rstrip(",;:—– ")
    if thesis.endswith(".") and not thesis.endswith(".."):
        thesis = thesis[:-1]
    return _capitalize(thesis)


#: Подчинительные слова. Сразу после сочинительного союза они означают, что
#: дальше идёт придаточное, а не однородная часть: резать там нельзя.
_SUBORDINATORS = (
    "если", "когда", "чтобы", "хотя", "пока", "поскольку", "потому",
    "так как", "раз", "будто", "словно", "который", "которая", "которые",
)

#: Во сколько раз тезис вправе превысить предел, лишь бы не стать обрывком.
#: Выше — режем по словам: обрывок плох, но потеря раздела хуже (`Z-42`,
#: обратный план `PLAN-7.4`). На корпусе эта ступень не срабатывает ни разу.
_NO_CUT_CEILING = 2


def _split_words(text: str, limit: int) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    length = 0
    for word in text.split():
        if current and length + 1 + len(word) > limit:
            out.append(" ".join(current))
            current, length = [], 0
        length += (1 if current else 0) + len(word)
        current.append(word)
    if current:
        out.append(" ".join(current))
    return out or [text]


def _clause_split(sentence: str, cfg: ProseConfig) -> list[str]:
    """Режет по запятой, за которой стоит сочинительный союз.

    **Запятая сама по себе границей мысли не является** — в русском она
    размечает придаточные. Замер: разрез по каждой запятой со слиянием коротких
    кусков дал «Которые сейчас есть», «В зависимости от того» — новые обрывки
    вместо старых (`WORKLOG/2026-09-20-z42-baseline.md`, замер 4).

    А вот союз `а`, `и`, `но` после запятой — граница однородных частей, и она
    настоящая: на трёх проблемных предложениях корпуса вышло девять кусков,
    все короче предела, обрывков ноль (там же, замер 5).
    """
    if not cfg.clause_conjunctions:
        return [sentence]
    union = "|".join(re.escape(c) for c in cfg.clause_conjunctions)
    # Союз, за которым сразу стоит подчинительное слово, границей мысли **не**
    # является: «…, и если текст не поместился — ужимаем» разрезать после
    # запятой значит оставить условие без главной части. Сторож поставлен на
    # собственное правило: без него оно само порождало обрывок (`Z-42`).
    sub = "|".join(re.escape(w) for w in _SUBORDINATORS)
    pattern = re.compile(
        rf"(?<=[,;])\s+(?=(?:{union})\s+(?!(?:{sub})\b))", re.IGNORECASE
    )
    return [p.strip(" ,;") for p in pattern.split(sentence) if p.strip(" ,;")]


def split_theses(sentence: str, cfg: ProseConfig) -> list[str]:
    """Предложение в тезисы. Лестница из пяти ступеней (`Z-42`, `PLAN-7.4`).

    **Режется только то, что не влезает.** Первая версия резала по сильным
    разделителям всегда, и на «Основные конкуренты — большие маркетплейсы»
    выходили два огрызка вместо одной мысли (журнал `PLAN-2.2`).

    Дальше — по убыванию надёжности границы:

    1. сильные разделители (тире, точка с запятой);
    2. **запятая перед сочинительным союзом** — граница однородных частей;
    3. запятая с воротами «все куски не короче минимума»;
    4. **не резать вовсе**: длинный тезис ниже по потоку встретит подбор
       раскладки по ёмкости и петлю VERIFY, которая ужмёт кегль. Обрывок не
       встретит ничего — его не чинит ни одна стадия;
    5. по словам — **только выше потолка**. Там выбор уже не «фраза или
       обрывок», а «обрывок или потеря»: предложение, которое не влезет ни в
       один слот, уводит весь раздел в `unplaced`.

    Четвёртая ступень — не уступка, а архитектура проекта: «здесь мы стремимся
    уложиться, а не обязаны» (`matching.py`). Замер: по корпусу без границы по
    союзу остаются три предложения из 51, самый длинный кусок 197 знаков, и
    пятая ступень не срабатывает ни разу.
    """
    sentence = sentence.strip()
    if len(sentence) <= cfg.thesis_max:
        return [sentence]

    parts = [p.strip(" ,;:") for p in _STRONG.split(sentence)]
    parts = [p for p in parts if p]
    if len(parts) > 1 and all(len(p) >= cfg.thesis_min for p in parts):
        out: list[str] = []
        for part in parts:
            out.extend(split_theses(part, cfg) if len(part) > cfg.thesis_max else [part])
        return out

    clauses = _clause_split(sentence, cfg)
    if len(clauses) > 1 and all(len(p) >= cfg.thesis_min for p in clauses):
        out = []
        for clause in clauses:
            out.extend(split_theses(clause, cfg) if len(clause) > cfg.thesis_max else [clause])
        return out

    pieces = [p.strip() for p in _COMMA.split(sentence) if p.strip()]
    if len(pieces) > 1 and all(len(p) >= cfg.thesis_min for p in pieces):
        out = []
        for piece in pieces:
            out.extend(split_theses(piece, cfg) if len(piece) > cfg.thesis_max else [piece])
        return out

    if len(sentence) <= cfg.thesis_max * _NO_CUT_CEILING:
        return [sentence]
    return _split_words(sentence, cfg.thesis_max)


# --- единицы: предложение с меткой -------------------------------------


@dataclass(frozen=True)
class _Unit:
    """Предложение, разобранное на метку темы и тезисы.

    Метка — то, что автор сам вынес перед двоеточием. Она же лучший кандидат в
    заголовок слайда: человек уже назвал тему, придумывать за него не надо.
    """

    label: str | None
    theses: tuple[str, ...]
    shift: bool


def _units(sentences: list[str], cfg: ProseConfig) -> tuple[list[_Unit], int]:
    units: list[_Unit] = []
    dropped = 0
    for sentence in sentences:
        clean, lost = _strip_lead_in(sentence, cfg)
        dropped += lost
        label = None
        m = _LABEL.match(clean)
        if m and _looks_like_label(m.group(1), cfg):
            label, clean = _tidy(m.group(1)), m.group(2)
        theses = tuple(_tidy(t) for t in split_theses(clean, cfg) if _tidy(t))
        if not theses and not label:
            continue
        units.append(_Unit(label=label, theses=theses, shift=_is_shift(clean, cfg)))
    return units, dropped


# --- темы --------------------------------------------------------------

#: Слово нормализуется обрезкой до основы: морфологии в стандартной библиотеке
#: нет, а для сравнения связности хватает совпадения первых пяти букв
#: (`ADR-0001` — зависимостей не заводим).
_STEM = 5


def _stems(text: str, cfg: ProseConfig) -> list[str]:
    return [
        w[:_STEM]
        for w in (m.group(0).lower() for m in _WORD.finditer(text))
        if len(w) >= 4 and w not in cfg.stopwords
    ]


def _cohesion(before: list[str], after: list[str]) -> float:
    """Доля общих основ. Множества не покидают функцию — наружу уходит число."""
    if not before or not after:
        return 0.0
    sa, sb = set(before), set(after)
    return len(sa & sb) / min(len(sa), len(sb))


def _is_shift(text: str, cfg: ProseConfig) -> bool:
    low = text.lower()
    return any(low.startswith(p) for p in cfg.topic_shift)


#: Насколько крепка граница ПЕРЕД темой. Нужна подгонке объёма (`PLAN-2.3`):
#: сливать начинаем с самых слабых швов. Метка автора — самый крепкий шов:
#: человек сам развёл темы двоеточием, и склеивать их нам не с руки.
BOUNDARY_LABEL = 3.0
BOUNDARY_SHIFT = 2.0
BOUNDARY_COHESION = 1.0
BOUNDARY_SIZE = 0.5     # предел размера темы — шов произвольный, рвём первым


@dataclass
class _Topic:
    label: str | None
    theses: list[str]
    #: Крепость шва перед этой темой. У первой темы смысла не имеет.
    strength: float = BOUNDARY_SIZE


def group_topics(units: list[_Unit], cfg: ProseConfig) -> list[_Topic]:
    """Предложения в темы.

    Новую тему начинает то, чем автор сам её начал: метка перед двоеточием или
    оборот смены темы. Связность — третий сигнал, для текста, где ни метки, ни
    оборота нет. Размер ограничен сверху: `max_items` по корпусу равен четырём.
    """
    topics: list[_Topic] = []
    current = _Topic(label=None, theses=[])

    def close(strength: float) -> None:
        """Закрывает тему. `strength` — крепость шва, который здесь возник:
        она достаётся **следующей** теме, ведь шов стоит перед ней."""
        nonlocal current
        if current.theses or current.label:
            topics.append(current)
        current = _Topic(label=None, theses=[], strength=strength)

    for i, unit in enumerate(units):
        # Метка или оборот смены темы начинают новую тему, как только в текущей
        # есть хоть что-то. Первая версия требовала двух тезисов, и метка
        # «Команду:» тонула внутри темы «Наша модель» (журнал `PLAN-2.2`).
        named = unit.label is not None or unit.shift
        if named and current.theses:
            close(BOUNDARY_LABEL if unit.label is not None else BOUNDARY_SHIFT)
        elif len(current.theses) >= cfg.topic_max:
            close(BOUNDARY_SIZE)
        if current.label is None:
            current.label = unit.label
        current.theses.extend(unit.theses)

        if len(current.theses) < cfg.topic_min or i + 1 >= len(units):
            continue
        nxt = units[i + 1]
        if nxt.label is not None or nxt.shift:
            continue  # граница и так придётся на следующий шаг
        ahead = [t for u in units[i + 1:i + 3] for t in u.theses]
        cohesion = _cohesion(
            [s for t in current.theses[-2:] for s in _stems(t, cfg)],
            [s for t in ahead for s in _stems(t, cfg)],
        )
        if cohesion == 0.0:
            close(BOUNDARY_COHESION)
    close(BOUNDARY_SIZE)

    # Хвост короче темы прилипает к предыдущей, если там есть место: отдельный
    # слайд из одного огрызка хуже, чем лишний пункт на предыдущем.
    if len(topics) > 1 and topics[-1].label is None and len(topics[-1].theses) < cfg.topic_min:
        if len(topics[-2].theses) + len(topics[-1].theses) <= cfg.topic_max:
            topics[-2].theses.extend(topics[-1].theses)
            topics.pop()
    return topics


# --- заголовки ---------------------------------------------------------

def _looks_like_label(text: str, cfg: ProseConfig) -> bool:
    """Годится ли то, что автор вынес перед двоеточием, в заголовок.

    **Проверяется только длина** (`Z-40`, `PLAN-7.2`). Счёт слов — «не больше
    пяти» — стоял здесь до 19 сентября и отвергал готовые заголовки:
    «Доля заказов, выданных дольше пяти минут» (39 знаков, шесть слов),
    «Причина одна и она не техническая» (33 знака).

    Убран не подбором числа, а замером: при пределе 6 три метки возвращаются,
    при 7 и **без предела вовсе результат тот же самый** — работу делает
    `heading_max`. Значит лишняя константа, а не второй рубеж.

    Доказывать метке больше нечего: **двоеточие поставил человек**, он уже
    назвал тему. Не верить ему — то же, что выдумывать за него.
    """
    return len(text) <= cfg.heading_max


#: Где кончается именная часть фразы: «Доля заказов, выданных…», «По срокам мы
#: отстаём, и это…». Правило от устройства языка, а не от наших слов: словарей
#: здесь нет намеренно — они были бы подгонкой под собственный корпус
#: (`ADR-0017`).
_BOUNDARY = re.compile(r"\s+[—–]\s+|,\s+|;\s+|:\s+")

#: Короче этого обрезка до границы не берётся: «Не питч» из «Не питч, а именно
#: отчёт» — не заголовок, а первое слово.
_CUT_MIN = 10


def _our_cut_reads_as_heading(text: str, cfg: ProseConfig) -> bool:
    """Годится ли **наш** разрез в заголовок.

    Бремя доказательства здесь и у метки автора **разное**, и это не строгость
    ради строгости (`PLAN-7.2`, правка после прототипа). Метка — не догадка, а
    разметка: человек сам поставил двоеточие и назвал тему. Обрезка до запятой —
    наша догадка, и она обязана показать, что получилось название, а не начало
    фразы.

    Признак: **не меньше двух значимых слов** — длиннее трёх букв и не из
    стоп-списка. Отсекает «Теперь про то» (значимое одно) и пропускает «По
    срокам мы отстаём», «Пунктов выдачи всего 318».

    То же правило, применённое к меткам, отвергало «Воспроизводимость», «Наша
    модель», «Риски» — двенадцать меток из двадцати шести. Поэтому здесь и
    только здесь.
    """
    significant = sum(
        1
        for m in _WORD.finditer(text)
        if len(m.group(0)) >= 4 and m.group(0).lower() not in cfg.stopwords
    )
    return significant >= 2


def _strip_discourse(head: str, cfg: ProseConfig) -> str:
    """Снимает с заголовка связку — слово о речи, а не о предмете.

    **Только с нашего разреза.** Метку, которую автор поставил сам перед
    двоеточием, эта функция не видит и видеть не должна: он назвал тему, а мы
    лишь угадали границу. Замер 9: из семи заголовков корпуса, начинающихся с
    маркера смены темы, **пять — метки автора**, и все пять хороши как есть:
    «Риски», «Насчёт модели», «Заканчиваем планами», «Что показывает
    юнит-экономика».

    **Снимаются только наречия и частицы** (`discourse_adverbs`) и **рамки
    перед придаточным** (`clause_frames`). Предлогов в списках нет намеренно:
    предлог управляет падежом, и «Насчёт модели» без него даёт «Модели» в
    косвенном падеже, а склонения у нас нет (`ADR-0001`). Названий тем —
    «риски», «планы», «деньги» — там тоже нет: это и есть заголовок.

    Порядок важен: сначала наречие, потом рамка. «Теперь про то, что проверено
    на чужом материале» → «про то, что проверено…» → **«Проверено на чужом
    материале»**. Одним проходом вышло бы только полдела.

    Результат остаётся **подстрокой входа** — снимается префикс и ничего не
    дописывается (`Z-40`, правило «не выдумывать содержание»).
    """
    text = head
    for _ in range(2):
        low = text.lower()
        for phrase in cfg.clause_frames:
            if low.startswith(phrase):
                text = text[len(phrase):].lstrip(" ,:—–")
                break
        else:
            for word in cfg.discourse_adverbs:
                if low.startswith(word) and (
                    len(low) == len(word) or not low[len(word)].isalpha()
                ):
                    text = text[len(word):].lstrip(" ,:—–")
                    break
            else:
                break
    # Пусто или один знак — снимать было нечего, возвращаем как было.
    return _capitalize(text) if len(text) >= _CUT_MIN // 2 else head


def _significant(text: str, cfg: ProseConfig) -> int:
    """Сколько в тексте слов длиннее трёх букв и не из стоп-списка."""
    return sum(
        1
        for m in _WORD.finditer(text)
        if len(m.group(0)) >= 4 and m.group(0).lower() not in cfg.stopwords
    )


def _is_self_remark(text: str, cfg: ProseConfig) -> bool:
    """Реплика автора о себе: «приложу две», «расскажу подробнее».

    Признак узкий намеренно. Замер: мест с первым лицом по корпусу **24**, и
    только **одно** из них — реплика; правило «снимать первое лицо» имело бы
    точность 4% (`WORKLOG/2026-09-20-z42-baseline.md`, замер 2). Поэтому здесь
    не грамматика, а **закрытый список глаголов о работе с колодой**, взятый из
    `Z-41` и поставленный в первое лицо единственного числа.
    """
    if not cfg.self_verbs:
        return False
    words = {m.group(0).lower() for m in _WORD.finditer(text)}
    return bool(words & set(cfg.self_verbs))


def _drop_dangling_tail(text: str, cfg: ProseConfig) -> str:
    """Убирает у метки хвост, в котором нет ни одного значимого слова.

    Автор пишет «По срокам мы отстаём, и это: прямо, а не прятать…» — и меткой
    становится «По срокам мы отстаём, **и это**», оборванная на союзе. Хвост
    ничего не называет, а на слайде виден.

    Нашлось **чтением вывода** сразу после того, как привратник метки перестал
    считать слова: до этого такие метки отвергались по числу слов, и дефект
    прятался за другим дефектом.

    Правило то же, что у нашего разреза, и словарей союзов здесь нет: хвост
    выбрасывается, если в нём меньше двух значимых слов, **и только если
    оставшееся само читается как заголовок**. Порог в `_CUT_MIN` защищает
    «То, что нам нужно» → «То» и «Картинки, приложу две» → «Картинки»: остаток
    слишком короток, и метка остаётся целой.
    """
    cuts = list(_BOUNDARY.finditer(text))
    if not cuts:
        return text
    last = cuts[-1]
    head, tail = text[:last.start()].strip(), text[last.end():].strip()

    # Хвост — реплика автора о том, что он сам сделает с колодой: «Картинки,
    # **приложу две**». Порог длины здесь не действует намеренно (`Z-42`):
    # «Картинки» — такое же однословное название темы, как принятые «Риски» и
    # «Воспроизводимость», и оно лучше, чем название плюс кусок диктовки.
    #
    # Список не выдуман под этот пример, а **проспряган** из `address_verbs`
    # задачи `Z-41`: те же глаголы о работе с колодой, только первое лицо
    # единственного числа. Множественное сюда не входит — «Мы покажем
    # результаты» это содержание, а не реплика.
    if _is_self_remark(tail, cfg) and head and _significant(head, cfg) >= 1:
        return head

    if _our_cut_reads_as_heading(tail, cfg):
        return text
    if len(head) >= _CUT_MIN and _our_cut_reads_as_heading(head, cfg):
        return head
    return text


def heading_for(topic: _Topic, cfg: ProseConfig) -> tuple[str | None, list[str]]:
    """Заголовок темы и оставшееся тело.

    **Заголовок берётся из текста автора или не берётся вовсе** (`Z-40`,
    `PLAN-7.2`). До 19 сентября был четвёртый путь — склеить два самых частых
    слова темы, — и он давал «Вход движок», «Корпусу дало», «Выдачи больше».
    Убран не потому, что некрасиво: таких словосочетаний **во входном тексте
    нет**, их никто не писал. `CLAUDE.md`, «Чего не делать»: код упорядочивает и
    группирует найденное, а не изобретает.

    Порядок предпочтения, от самого надёжного источника к самому шаткому:

    1. **метка автора** — он сам вынес её перед двоеточием;
    2. **начало первого тезиса до запятой или тире** — наш разрез, и он обязан
       доказать, что не обрубок (`_our_cut_reads_as_heading`);
    3. **первый тезис целиком**, если влезает в заголовок;
    4. **отказ.** Пустой слот заголовка рамки не оставляет — проверено растром
       на VK WorkSpace (`WORKLOG/2026-09-19-z40-baseline.md`, замер 5), и довод
       `Z-23` «лучше плохой заголовок, чем пустой слот» на сдаточном шаблоне не
       подтвердился.

    **Взятое в заголовок уходит из тела.** Логическая проверка `PLAN-2.2`
    оставляла его у коротких тем; чтение вывода это опровергло — получался
    слайд, где заголовок слово в слово повторён единственным абзацем.

    **Но тело не имеет права опустеть**, и это не вкус. Приложение 1 ТЗ называет
    дефектом дословно: «пустой слайд или **слайд с одним заголовком**», а
    контекстуальная проверка 5 спрашивает «на слайде есть содержание, а не
    только заголовок?». `PLAN-7.2` собирался брать единственный тезис в
    заголовок целиком — план утверждал, что раздел без тела штатно ляжет на
    разделитель. **Утверждение оказалось неверным**, и поймал его тест,
    написанный при `PLAN-2.3`, вместе с чтением первоисточника.

    Поэтому тезис уходит в заголовок только тогда, когда после него в теме
    **что-то остаётся**: либо остаток той же фразы после запятой, либо
    следующие тезисы. Не остаётся ничего — заголовка нет, а содержание
    сохраняется.
    """
    theses = list(topic.theses)
    if topic.label:
        return _drop_dangling_tail(topic.label, cfg), theses
    if not theses:
        return None, theses

    first = theses[0]
    # Перебираются ВСЕ границы, а не только первая: «Теперь про то, что
    # проверено на чужом материале, это важнее…» на первой запятой даёт
    # «Теперь про то» — обрубок, на второй «Теперь про то, что проверено на
    # чужом материале» — заголовок. Найдено растром: слайд без заголовка на
    # карточной раскладке оставляет заметную пустоту сверху, и ради неё стоило
    # поискать дальше первой запятой.
    for cut in _BOUNDARY.finditer(first):
        head, rest = first[:cut.start()].strip(), first[cut.end():].strip()
        if not rest or len(head) > cfg.heading_max:
            # Дальше границы только удлиняют голову — искать больше нечего.
            break
        if len(head) >= _CUT_MIN and _our_cut_reads_as_heading(head, cfg):
            # Сторож спрашивают о разрезе **как он есть**, и только потом
            # снимается связка. Иначе «Теперь деньги» после снятия остаётся с
            # одним значимым словом, не проходит сторожа — и годный заголовок
            # «Деньги» теряется (`Z-42`, проверка первая, находка 3).
            #
            # Остаток — такой же тезис, как все прочие, и причёсывается так же.
            # Без `_tidy` он уезжал на слайд со строчной буквы: «как вот эта
            # самая, никакой разметки заполнять не надо» (`Z-42`, замер 11).
            return _strip_discourse(head, cfg), [_tidy(rest), *theses[1:]]
    if len(theses) > 1 and len(first) <= cfg.heading_max:
        # Третий путь — тоже **наша** догадка, а не метка автора, поэтому связка
        # снимается и здесь. «Теперь деньги.» стоит в тексте отдельным
        # предложением и приходит именно сюда, а не через разрез (`Z-42`).
        return _strip_discourse(first, cfg), theses[1:]
    return None, theses


#: Сколько слов допускается между словом просьбы и предлогом: «презентация
#: **с цифрами** по загрузке…». Три — с запасом: замер по корпусу нашёл максимум
#: два (`WORKLOG/2026-09-19-z39-baseline.md`, замер 7). Больше значило бы ловить
#: предлог из соседней мысли.
_TITLE_GAP = 3


@functools.lru_cache(maxsize=8)
def _title_patterns(cues: tuple[str, ...], keep: tuple[str, ...]):
    """Словарь просьб в выражения, терпимые к словам посередине.

    Словарь остаётся в конфиге, механизм — здесь (`ADR-0022`). Скомпилированное
    кэшируется: `deck_title` зовут на каждый блок прозы.
    """
    out = []
    for cue in cues:
        parts = cue.split()
        if len(parts) < 2:
            continue
        head, tail = parts[0], " ".join(parts[1:])
        out.append((
            tail in keep,
            re.compile(
                rf"{re.escape(head)}(?:\s+\S+){{0,{_TITLE_GAP}}}\s+({re.escape(tail)})\s+",
                re.IGNORECASE,
            ),
        ))
    return tuple(out)


def deck_title(sentences: list[str], cfg: ProseConfig) -> tuple[str | None, int]:
    """Заголовок колоды из просьбы: «презентация про X» → «X».

    Смотрит первые два предложения, а не одно: чат начинается с приветствия, и
    первая версия принимала за просьбу «Привет!» (журнал `PLAN-2.2`). Возвращает
    заголовок и номер предложения, из которого он взят, — само предложение
    обращение, а не содержание, и в тело не идёт.

    **Между словом просьбы и предлогом допускаются слова** (`Z-39`): «нужна
    презентация *с цифрами* по загрузке…» до 19 сентября не давала заголовка
    вовсе, потому что словарь искался подстрокой.

    **Предлог из `deck_title_keep` остаётся в заголовке**, и это не мелочь.
    «Презентация **по** загрузке» требует дательного падежа, и срезанный предлог
    оставляет «Загрузке сети пунктов выдачи» — обрубок на самом заметном слайде
    колоды. С предлогом выходит «По загрузке сети пунктов выдачи» — правильная
    русская фраза. Проверено на корпусе: два новых заголовка, три прежних не
    изменились ни на знак.
    """
    patterns = _title_patterns(cfg.deck_title, cfg.deck_title_keep)
    for n, sentence in enumerate(sentences[:2]):
        for keep, pattern in patterns:
            m = pattern.search(sentence)
            if not m:
                continue
            rest = sentence[m.start(1) if keep else m.end():].strip(" :—–")
            rest = re.split(r"[,.;]| для | на \d| минут", rest)[0].strip()
            if len(rest) >= 3:
                return _capitalize(rest), n
    return None, -1


# --- подгонка объёма ---------------------------------------------------
#
# Задача `Z-35`, план `PLAN-2.3`. Число слайдов решается в двух местах: здесь
# (сколько тем — от шаблона не зависит, `ADR-0016`) и в `deterministic.py`
# (сколько слайдов на тему — от шаблона зависит по определению).


def _split_topic(topic: _Topic, cfg: ProseConfig) -> list[_Topic]:
    """Делит тему по самому слабому внутреннему шву.

    Слабый шов — там, где связность соседних тезисов ниже всего; при равенстве
    берётся ближайший к середине, а при равенстве и тут — левый. Иначе исход
    зависел бы от порядка обхода.
    """
    theses = topic.theses
    best, best_key = 1, None
    for i in range(1, len(theses)):
        before = [s for x in theses[max(0, i - 2):i] for s in _stems(x, cfg)]
        after = [s for x in theses[i:i + 2] for s in _stems(x, cfg)]
        key = (_cohesion(before, after), abs(2 * i - len(theses)), i)
        if best_key is None or key < best_key:
            best, best_key = i, key
    return [
        _Topic(label=topic.label, theses=theses[:best], strength=topic.strength),
        _Topic(label=None, theses=theses[best:], strength=BOUNDARY_SIZE),
    ]


def fit_topic_count(
    topics: list[_Topic], target: tuple[int, int] | None, cfg: ProseConfig
) -> tuple[list[_Topic], str | None]:
    """Приводит число тем к цели: сливает по слабым швам, делит по слабым.

    Возвращает темы и причину, если цель не достигнута. Причина — не отговорка,
    а ответ: «контента хватает на N тем» говорит о входе, а не о нашем бессилии.

    Пол по содержательности встроен: тема из одного тезиса не делится никогда.
    Пятнадцать слайдов по одному тезису формально попадают в диапазон ТЗ и
    проваливают половину Приложения 1 (`PLAN-2.3`, второй проход проверки).
    """
    if not target or not topics:
        return topics, None
    low, high = target
    out = [_Topic(label=x.label, theses=list(x.theses), strength=x.strength) for x in topics]

    while len(out) > high and len(out) > 1:
        # Самый слабый шов; при равной крепости — самый левый.
        i = min(range(1, len(out)), key=lambda k: (out[k].strength, k))
        out[i - 1].theses.extend(out[i].theses)
        if out[i - 1].label is None:
            out[i - 1].label = out[i].label
        out.pop(i)

    while len(out) < low:
        # Самая крупная тема; при равном размере — самая левая.
        i = max(range(len(out)), key=lambda k: (len(out[k].theses), -k))
        if len(out[i].theses) < 2:
            return out, (
                f"контента хватает на {len(out)} тем: делить дальше нечего, "
                f"каждая тема — один тезис"
            )
        out[i:i + 1] = _split_topic(out[i], cfg)

    return out, None


# --- сборка ------------------------------------------------------------

def _is_prose_section(section: ContentSection, cfg: ProseConfig) -> bool:
    """Раздел без заголовка, который надо структурировать.

    Размеченный вход заголовки имеет и потому не трогается **по построению**, а
    не по удачно выбранному порогу (`PLAN-2.2`, первый проход, дыра 2).

    Неразмеченность бывает двух видов, и раньше видели только первый
    (`Z-39`, `PLAN-7.1`):

    * **простыня** — один длинный абзац, который надо резать;
    * **деловой текст** — много коротких абзацев без единого заголовка. Резать
      внутри почти нечего, но структуры у него ровно столько же: ноль.

    Спрашивать про длину абзаца — значит спрашивать «надо ли резать», а
    вопрос здесь другой: «проза ли это вообще». Замер
    (`WORKLOG/2026-09-19-z39-baseline.md`): у делового текста из девяти абзацев
    самый длинный был **184 знака при пороге 200**, ворота отвечали «не проза»,
    документ возвращался нетронутым, и колода выходила пустой на шести шаблонах
    из одиннадцати. Весь отказ держался на шестнадцати знаках.
    """
    if section.heading:
        return False
    paragraphs = [b for b in section.blocks if b.kind == "paragraph"]
    if any(b.length > cfg.prose_paragraph for b in paragraphs):
        return True
    return len(paragraphs) > 1


def needs_restructure(doc: ContentDoc, cfg: ProseConfig) -> bool:
    return any(_is_prose_section(s, cfg) for s in doc.sections)


@dataclass
class _Numbering:
    """Сквозные номера для блоков и разделов.

    Счётчик разделов заведён 19 сентября (`Z-39`, `PLAN-7.1`). До него номер
    темы начинался заново **на каждом блоке**, и разделы получали одинаковые
    id: `sec01t01` семь раз подряд. Дефект был тихим, пока блок в разделе был
    один; правка `Z-39` делает блоков много, и он стал бы систематическим.

    Цена не косметическая: `deterministic.py` держит `forced: dict[str, int]`
    по `section.id`, и два раздела с одним id делят одну запись —
    принудительное дробление одного молча применяется к другому.
    """

    n: int = 0
    s: int = 0

    def next(self) -> str:
        self.n += 1
        return f"b{self.n:02d}"

    def section(self, base: str) -> str:
        self.s += 1
        return f"{base}t{self.s:02d}"


def _blocks_from(theses: list[str], cfg: ProseConfig, ids: _Numbering) -> tuple[ContentBlock, ...]:
    """Тезисы в блоки: короткие идут пунктами списка, длинные — абзацами.

    И то и другое — атомы для `split`, ради которых всё затевалось. Разница
    только в том, что увидит `rank`: список ищет слот списка, абзац — текстовый.
    """
    blocks: list[ContentBlock] = []
    bullet_limit = cfg.thesis_target * 3 // 2
    run: list[str] = []

    def flush() -> None:
        if not run:
            return
        if len(run) == 1:
            blocks.append(ContentBlock(id=ids.next(), kind="paragraph", text=run[0]))
        else:
            blocks.append(
                ContentBlock(id=ids.next(), kind="list", items=tuple(run), text=" ".join(run))
            )
        run.clear()

    for thesis in theses:
        if len(thesis) <= bullet_limit:
            run.append(thesis)
            continue
        flush()
        blocks.append(ContentBlock(id=ids.next(), kind="paragraph", text=thesis))
    flush()
    return tuple(blocks)


def _take_title(sentences: list[str], cfg: ProseConfig) -> tuple[str | None, list[str], list[str]]:
    """Достаёт заголовок колоды и убирает из тела то, что было обращением.

    Возвращает заголовок, оставшиеся предложения и выброшенные. Выброшенное
    возвращается, а не пропадает: сколько знаков ушло, попадает в диагностику.
    """
    title, at = deck_title(sentences, cfg)
    if title is None:
        return None, sentences, []
    # Всё до просьбы («Привет!») выбрасывается, только если это короткие
    # обрывки: длинное предложение перед просьбой — уже содержание.
    head = sentences[:at]
    if any(len(s) >= cfg.thesis_min for s in head):
        return title, sentences[:at] + sentences[at + 1:], [sentences[at]]
    return title, sentences[at + 1:], sentences[:at + 1]


def restructure(
    doc: ContentDoc,
    cfg: ProseConfig | None = None,
    target: tuple[int, int] | None = None,
) -> ContentDoc:
    """Сплошная проза в разделы и тезисы. Размеченный вход возвращается как был.

    Возвращаемый документ несёт исходный текст в `origin`: `Z-08` отдаст модели
    прозу, а не нашу нарезку (первый проход логической проверки, дыра 1).

    `target` — желаемое число слайдов (`Z-35`). Здесь оно превращается в число
    **тем**: обложка съедает один слайд, поэтому цель уменьшается на единицу.
    Окончательную подгонку делает планировщик, уже зная раскладки шаблона.
    """
    cfg = cfg or load_config()
    if not needs_restructure(doc, cfg):
        return doc

    notes: list[str] = list(doc.notes)
    if not cfg.loaded:
        notes.append(
            f"Конфиг сегментации не прочитан, работают встроенные значения: {config_path()}"
        )

    ids = _Numbering()
    sections: list[ContentSection] = []
    title = doc.title
    dropped = 0
    taken: list[str] = []
    origin = "\n\n".join(
        b.text for s in doc.sections for b in s.blocks if b.kind == "paragraph"
    )

    for section in doc.sections:
        if not _is_prose_section(section, cfg):
            sections.append(section)
            continue
        # Темы собираются по ВСЕМУ разделу, а не по блоку (`Z-39`, `PLAN-7.1`).
        # Так надо по двум причинам, и вторая обнаружилась логической проверкой.
        #
        # Первая: **абзац идёт в разбор всегда**, независимо от длины. Раньше
        # короткий абзац миновал разбор и прилипал к предыдущей теме, и деловой
        # текст из девяти коротких абзацев слипался в одну тему. Прилипают
        # теперь только не-абзацы — список, метрика, картинка: список под
        # строкой «Вот выдача по месяцам:» принадлежит ей, а не себе.
        #
        # Вторая: подгонка объёма (`Z-35`) обязана вызываться **один раз на
        # раздел**. На каждый блок — значит на каждый абзац, и цель «10–15
        # слайдов» пришлась бы на каждый абзац по отдельности.
        topics: list[_Topic] = []
        strays: dict[int, list[ContentBlock]] = {}
        for block in section.blocks:
            if block.kind != "paragraph":
                # Привязка к теме, после которой блок стоял; -1 — до всякой темы.
                strays.setdefault(len(topics) - 1, []).append(block)
                continue

            sentences = list(split_sentences(block.text, cfg))
            if title is None:
                title, sentences, thrown = _take_title(sentences, cfg)
                taken.extend(thrown)
                dropped += sum(len(s) for s in thrown)

            units, lost = _units(sentences, cfg)
            dropped += lost
            topics.extend(group_topics(units, cfg))

        # Подгонка объёма идёт ДО заголовков: слияние тем иначе осиротило бы
        # заголовок, а деление — присвоило бы чужой (`PLAN-2.3`, дыра 3).
        wanted = (max(1, target[0] - 1), max(1, target[1] - 1)) if target else None
        topics, shortfall = fit_topic_count(topics, wanted, cfg)
        if shortfall:
            notes.append(f"Целевой объём не достигнут: {shortfall}.")

        # Прилипший блок садится на свою тему. После подгонки объёма тем могло
        # стать меньше или больше, и точное место теряется — тогда блок садится
        # на ближайшую существующую. Приблизительность здесь сознательная:
        # потерять блок с содержанием хуже, чем посадить его через один.
        for n, topic in enumerate(topics, 1):
            heading, body = heading_for(topic, cfg)
            extra = strays.pop(min(n - 1, len(topics) - 1), []) if strays else []
            sections.append(
                ContentSection(
                    id=ids.section(section.id),
                    heading=heading,
                    blocks=_blocks_from(body, cfg, ids) + tuple(extra),
                )
            )
        # Блоки, стоявшие до первой темы, и остаток после клампа — своим
        # разделом: без этого они бы просто пропали.
        leftover = [b for key in sorted(strays) for b in strays[key]]
        if leftover:
            sections.append(
                ContentSection(id=ids.section(section.id), heading=None,
                               blocks=tuple(leftover))
            )

    # Обложку `_with_cover` выделяет, только если заголовок колоды совпадает с
    # заголовком первого раздела (`deterministic.py`). Без этих строк колода
    # осталась бы без титульного слайда (дыра 3).
    if title and sections:
        first = sections[0]
        if not first.heading:
            sections[0] = ContentSection(id=first.id, heading=title, blocks=first.blocks)
        elif first.heading != title:
            sections.insert(0, ContentSection(id="cover", heading=title, blocks=()))

    notes.append(
        f"Вход распознан как сплошная проза: {len(sections)} разделов, "
        f"{sum(len(s.blocks) for s in sections)} блоков."
    )
    for sentence in taken:
        notes.append(f"В тело не пошло (обращение, а не содержание): «{sentence[:70]}».")
    if dropped:
        notes.append(f"Отброшено вводных оборотов и обращений: {dropped} знаков.")

    return ContentDoc(
        name=doc.name,
        title=title,
        sections=sections,
        origin=origin or doc.origin,
        notes=tuple(notes),
    )


def load_content(
    path: str,
    name: str | None = None,
    cfg: ProseConfig | None = None,
    target: tuple[int, int] | None = None,
) -> ContentDoc:
    """Вход стадии PLAN: разбор разметки, сегментация прозы и картинки,
    названные прозой.

    **Картинки выдираются после сегментации, и это решил замер, а не
    рассуждение.** Черновик `PLAN-7.10` утверждал обратное: «иначе путь уедет
    в тезис, сегментатор порежет его по словам». Проверил — не режет: путь
    переживает сегментацию целиком и попадает в тему «Картинки», то есть ровно
    туда, где о нём и сказано. А при выдирании **до** сегментации обе картинки
    уезжали в конец колоды: у сплошной прозы весь вход — один абзац, и блоки
    картинок приписывались после него, то есть к последней теме
    (`Z-28a`, `PLAN-7.10`, журнал).
    """
    doc = restructure(load_raw(path, name), cfg, target)
    doc, notes = extract_images(doc, os.path.dirname(os.path.abspath(path)), os.getcwd())
    if notes:
        doc.notes = tuple(doc.notes) + tuple(notes)
    return doc
