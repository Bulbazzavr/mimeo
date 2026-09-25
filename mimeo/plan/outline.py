"""Модель строит колоду из текста до вёрстки — `ADR-0023`, `PLAN-9.0`, `Z-57`.

Здесь то, что дали шаги Ш1 и Ш1б: формулировки промпта из `config/outline.json`,
схема ответа и проверки ответа (`check_outline`). Схема и проверки — контракт,
они в коде; формулировки — настройка, они в конфиге (`ADR-0022`). С Ш3 модуль
включён в сборку: `run` — путь модели для одной сборки, `to_doc` — колода из
принятого ответа, `outline.json` — запись того и другого. Промпт 1.1 снят замером дословно
(`WORKLOG/2026-09-25-z57-prompt.md`) и заморожен до замера Ш9: любая его правка —
перемер на пяти текстах прозы корпуса в обоих режимах без кэша промпта
(`ADR-0023`, «Что уточнил замер»).
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, replace

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

#: Те же типы словами — как их называет строка `kind` промпта. Нужны там, где
#: тип читает человек: в предупреждении плана об идеях картинок (Ш6).
KIND_WORDS = {
    "cover": "обложка", "agenda": "оглавление", "section": "разделитель",
    "text": "абзац", "bullets": "список", "cards": "равные пункты",
    "two_column": "сравнение", "metric": "крупное число", "quote": "цитата",
    "table": "таблица", "image_text": "картинка с текстом", "closing": "финал",
}

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
    "Реши, сколько нужно слайдов (от {slides_min} до {slides_max}; если содержания"
    " меньше — меньше, ничего не выдумывая), в каком порядке они идут и что на"
    " каждом.\n"
    "Для каждого слайда:\n"
    "- heading — заголовок от 2 до 6 слов, в именительном падеже;\n"
    "{theses}- kind — тип слайда: cover (обложка), agenda (оглавление), section"
    " (разделитель), text (абзац), bullets (список), cards (равные пункты),"
    " two_column (сравнение), metric (крупное число), quote (цитата), table"
    " (таблица), image_text (картинка с текстом), closing (финал); оглавление и"
    " финал — только из того, что есть в тексте и на слайдах колоды;\n"
    "- role — роль слайда, одна из: обложка, проблема, решение, продукт, рынок,"
    " тяга, бизнес-модель, конкуренты, финансы, команда, риски, планы, просьба,"
    " прочее;\n"
    "- image_idea — если слайду по смыслу нужна иллюстрация, одной фразой опиши, что"
    " на ней изображено, без указаний стиля; иначе пустая строка;\n"
    "- images — пути к картинкам из текста, которые относятся к этому слайду,"
    " дословно как в тексте; иначе пустой список.\n"
    "Правила: не добавляй фактов, чисел и названий, которых нет в тексте. Каждое"
    " число из текста должно попасть на какой-нибудь слайд. Каждый путь к картинке —"
    " ровно на один слайд, даже если он упомянут в просьбе к тебе. В missing_roles"
    " перечисли роли, которых в тексте нет.\n"
    "Обложка — тема словами автора, без добавленных слов. Финал — итог или просьба"
    " автора его словами; если их в тексте нет, финала не делай. Благодарностей,"
    " лозунгов и призывов от себя не пиши."
)
_THESES = {
    "keep": "- theses — от 1 до 4 тезисов: ДОСЛОВНЫЕ фразы или части фраз из текста автора,"
    " без перефразирования; каждый тезис — из одного предложения автора: сокращать"
    " можно, добавлять и переставлять слова нельзя; до 90 знаков;\n",
    "improve": "- theses — от 1 до 4 тезисов: короткие законченные фразы до 90 знаков; можно"
    " переформулировать для ясности, но только то, что есть в тексте; названия,"
    " термины и сокращения — только из текста и так, как в тексте: новых не вводи,"
    " не переводи и не сокращай;\n",
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


# --- Какие идеи картинок идут в план колоды (`PLAN-9.0`, Ш6) -----------------
#
# Замер 25 сентября (`WORKLOG/2026-09-25-z57-sh6-baseline.md`): из 63 идей
# промпта 1.1 нарисовать можно 8, и все восемь — на слайдах text, bullets и
# cards; на обложке, числах, таблице, сравнении и цитате — ни одной из 22: там
# модель просит график или значок, а это нативные объекты (`Z-32`). Отбор
# «по типу, затем по порядку» терял один сюжет из восьми, «по порядку» —
# четыре. Потолок — страховка на чужом тексте: главное держит промпт.

#: Запасные значения — те же, что `image_ideas` в `config/outline.json`; сверяет тест.
_IDEA_KINDS = ("text", "bullets", "cards", "image_text")
_SLIDES_PER_IDEA = 3


@dataclass(frozen=True)
class IdeaRules:
    """Типы слайда, на которых идея может стоять, и потолок: не больше одной
    идеи на `slides_per_idea` слайдов колоды."""

    kinds: tuple[str, ...] = _IDEA_KINDS
    slides_per_idea: int = _SLIDES_PER_IDEA


def idea_rules(path: str | None = None) -> IdeaRules:
    """Правила отбора идей из `config/outline.json`. Нет файла или ключа —
    встроенные значения, как у промпта; тип не из `KINDS` не принимается."""
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f).get("image_ideas") or {}
    except FileNotFoundError:
        return IdeaRules()
    kinds = raw.get("kinds")
    per = raw.get("slides_per_idea")
    good_kinds = isinstance(kinds, list) and kinds and all(k in KINDS for k in kinds)
    return IdeaRules(
        kinds=tuple(kinds) if good_kinds else _IDEA_KINDS,
        slides_per_idea=per if isinstance(per, int) and not isinstance(per, bool) and per >= 1
        else _SLIDES_PER_IDEA,
    )


# --- Проверки ответа (`ADR-0023`, п. 5; `PLAN-9.0`, Ш4) -----------------------
#
# Колода принимается целиком или не принимается. Схема держит форму, а смысл —
# эти проверки; они же мерили промпт в Ш1б (`WORKLOG/2026-09-25-z57-sh1b-*.md`),
# поэтому промпт настроен ровно на то, чем его потом судит продукт.

#: Пути картинок вычитаются из текста до счёта чисел и слов: цифры и латиница
#: пути — не содержание.
_PATH = re.compile(r"[A-Za-z0-9_./-]+\.(?:png|jpe?g)")
#: Число — целиком: десятичная часть при нём, разряды «14 900» склеены. Иначе
#: «3.9» даёт «9», а «14 900» — «14», и проверки видят числа, которых нет:
#: замер Ш1б — «март 9%» и «июнь 14%» проходили дословность по обломкам.
_THOUSANDS = re.compile(r"(?<!\d)\d{1,3}(?:[ \u00a0]\d{3})+(?!\d)")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
#: Точка и дефис — только внутри слова («Node.js», «v1-2»): у выражения замера
#: 23 сентября точка конца предложения прилипала к слову, и «mimeo.» не
#: совпадало с «mimeo».
_LAT = re.compile(r"[A-Za-z][A-Za-z0-9+#]*(?:[.-][A-Za-z0-9+#]+)*")
_TOKEN = re.compile(r"\d+(?:[.,]\d+)?|[a-zа-я]+(?:-[a-zа-я]+)*")
_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")

#: Числа до десяти словом, во всех падежах («ё» как «е»). «Переведено 3 площадки
#: из 4» из «из четырёх площадок переведены три» — не выдумка, а запись цифрой:
#: так замер Ш1б на `chat` и `short` дал ложные отказы. Факт языка, не
#: настройка — поэтому в коде, как `_ADDRESS_MODAL` в `prose.py`. Больше десяти
#: словом в корпусе нет; это названная граница.
_NUMBER_WORDS = {
    "1": ("один", "одна", "одно", "одного", "одной", "одному", "одним", "одном", "одну"),
    "2": ("два", "две", "двух", "двум", "двумя"),
    "3": ("три", "трех", "трем", "тремя"),
    "4": ("четыре", "четырех", "четырем", "четырьмя"),
    "5": ("пять", "пяти", "пятью"),
    "6": ("шесть", "шести", "шестью"),
    "7": ("семь", "семи", "семью"),
    "8": ("восемь", "восьми", "восемью", "восьмью"),
    "9": ("девять", "девяти", "девятью"),
    "10": ("десять", "десяти", "десятью"),
}


def _spelled(number: str, words: set[str]) -> bool:
    return any(w in words for w in _NUMBER_WORDS.get(number, ()))


@dataclass(frozen=True)
class Check:
    """Итог одной проверки. `detail` называет нарушение словами — оно уходит в
    диагностику и в `outline.json`, а не остаётся в голове проверяющего."""

    name: str
    ok: bool
    detail: str = ""


def _norm(s: str) -> str:
    """Та же нормализация, которой считалась дословность в замерах 23–24 сентября."""
    return re.sub(r"\s+", " ", s.lower()).strip(" .,;:—-«»\"")


def _prep(s: str) -> str:
    """Нижний регистр, «ё» как «е», пути картинок вычтены, разряды склеены."""
    s = _PATH.sub(" ", s).lower().replace("ё", "е")
    return _THOUSANDS.sub(lambda m: re.sub(r"[ \u00a0]", "", m.group(0)), s)


def _tokens(s: str) -> list[str]:
    return _TOKEN.findall(_prep(s))


def _content(tokens: list[str], stopwords) -> list[str]:
    """Слова, несущие смысл, — то же определение, что у пути без модели
    (`prose.py`): от четырёх букв и не из `stopwords` `config/prose.json`; и
    числа. Предлоги и союзы модель вправе снять или добавить, сжимая фразу.
    **Цена названа:** трёхбуквенных слов («май», «год») и снятого «не» эта
    проверка не видит — оттенки смысла остаются за растром (`ADR-0023`)."""
    return [t for t in tokens if t[0].isdigit() or (len(t) >= 4 and t not in stopwords)]


def same_stem(a: str, b: str) -> bool:
    """Одно слово в разных формах: общее начало не короче `max(4, короче − 2)`.

    Правило выбрано замером (`WORKLOG/2026-09-25-z57-sh1b-baseline.md`): пять
    первых букв рвут беглую гласную и пятибуквенные слова («движка» / «движок»,
    «схема» / «схему»), а «общее начало» держит окончания до трёх букв
    («продуктам» / «продукты») и не пускает «путь», «спасибо», «внимание».
    Числа сравниваются только точно: у «12500» и «12501» четыре общих знака.
    """
    if a == b:
        return True
    if a[:1].isdigit() or b[:1].isdigit():
        return False
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n >= max(4, min(len(a), len(b)) - 2)


def _in_vocab(word: str, vocab: set[str]) -> bool:
    return word in vocab or any(same_stem(word, t) for t in vocab)


def _subsequence(needle: list[str], hay: list[str]) -> bool:
    """Слова `needle` идут в `hay` в том же порядке, возможно с пропусками."""
    it = iter(hay)
    return all(any(same_stem(w, t) for t in it) for w in needle)


def _is_title(index: int, slide: dict) -> bool:
    """Обложка колоды — первый слайд типа cover. На слайд от неё идёт только
    название: подзаголовок `_with_cover` вынес бы отдельным слайдом
    (`PLAN-9.0`, проверка 1, п. 14)."""
    return index == 0 and slide.get("kind") == "cover"


def _slide_texts(slides: list[dict]) -> list[str]:
    """Что окажется на слайдах: заголовки и тезисы. Тезисы оглавления не в
    счёт — пункты оглавления собирает код из заголовков колоды; тезисы обложки
    тоже — на обложку идёт только название (Ш3). Иначе число, стоящее лишь в
    подзаголовке обложки, считалось бы «на слайде», а на слайд не попало бы.
    Замер: на ответах Ш1б итог проверок от этого тот же, 9 из 10
    (`WORKLOG/2026-09-25-z57-sh3-baseline.md`, § 5)."""
    out = []
    for i, s in enumerate(slides):
        out.append(s["heading"])
        if s["kind"] != "agenda" and not _is_title(i, s):
            out.extend(s["theses"])
    return out


def _form(answer) -> str | None:
    """Форма ответа по схеме — проверяется и в режиме со схемой (`ADR-0023`)."""
    if not isinstance(answer, dict) or not isinstance(answer.get("slides"), list):
        return "нет списка slides"
    if not answer["slides"]:
        return "slides пуст"
    for i, s in enumerate(answer["slides"], 1):
        if not isinstance(s, dict):
            return f"слайд {i} — не объект"
        for key, kind in (("heading", str), ("kind", str), ("theses", list), ("images", list)):
            if not isinstance(s.get(key), kind):
                return f"слайд {i}: поле {key} отсутствует или не того типа"
        if not all(isinstance(x, str) for x in s["theses"] + s["images"]):
            return f"слайд {i}: в theses или images не строки"
    return None


def check_outline(
    text: str,
    answer,
    mode: str,
    slides_max: int,
    prose_cfg=None,
    closing_captions=None,
) -> tuple[Check, ...]:
    """Все проверки ответа модели. Колода годна, только если прошли все.

    `closing_captions` — выражения подписей финала шаблона (`config/kinds.json`,
    `Z-58`): тот же признак, которым код узнаёт макет «Спасибо», ловит
    «Спасибо за внимание», дописанное моделью от себя.
    """
    from .prose import has_address, load_config as load_prose

    problem = _form(answer)
    if problem:
        return (Check("форма", False, problem),)
    if closing_captions is None:
        from ..analyze.captions import load_config as load_captions

        closing_captions = load_captions().closing
    prose_cfg = prose_cfg or load_prose()

    slides = answer["slides"]
    plain = _PATH.sub(" ", text)
    on_slides = _slide_texts(slides)
    joined = _PATH.sub(" ", " ".join(on_slides))
    checks = [Check("форма", True)]

    src_nums, out_nums = _NUM.findall(_prep(text)), _NUM.findall(_prep(joined))
    src_words, out_words = set(_tokens(text)), set(_tokens(joined))
    lost = sorted({n for n in src_nums if n not in out_nums and not _spelled(n, out_words)})
    invented = sorted({n for n in out_nums if n not in src_nums and not _spelled(n, src_words)})
    checks.append(Check("числа", not lost and not invented,
                        f"потеряны {lost}, выдуманы {invented}" if lost or invented else ""))

    src_paths = sorted(_PATH.findall(text))
    # Картинка обложки на слайд не идёт — так же, как её тезисы (`_is_title`).
    out_paths = sorted(p for i, s in enumerate(slides) if not _is_title(i, s) for p in s["images"])
    detail = ""
    if out_paths != src_paths:
        missing = sorted(set(src_paths) - set(out_paths))
        foreign = sorted(set(out_paths) - set(src_paths))
        twice = sorted({p for p in out_paths if out_paths.count(p) > 1})
        detail = f"нет на слайдах {missing}, не из текста {foreign}, дважды {twice}"
    # Путь в заголовке или тезисе лёг бы на слайд текстом — это растр `Z-28a`.
    as_text = [t for t in on_slides if _PATH.search(t)]
    if as_text:
        detail = (detail + "; " if detail else "") + f"путь текстом на слайде: {as_text}"
    checks.append(Check("картинки", not detail, detail))

    src_lat = {w.lower() for w in _LAT.findall(plain)}
    new_lat = sorted({w for w in _LAT.findall(joined) if w.lower() not in src_lat})
    checks.append(Check("латиница", not new_lat, f"нет в тексте: {new_lat}" if new_lat else ""))

    addressed = [t for t in on_slides if has_address(t, prose_cfg)]
    checks.append(Check("обращения", not addressed, f"на слайдах: {addressed}" if addressed else ""))

    checks.append(Check("объём", len(slides) <= slides_max,
                        "" if len(slides) <= slides_max else f"слайдов {len(slides)} при потолке {slides_max}"))

    unknown = sorted({s["kind"] for s in slides if s["kind"] not in KINDS})
    agendas = sum(s["kind"] == "agenda" for s in slides)
    checks.append(Check("типы", not unknown and agendas <= 1,
                        f"не из словаря {unknown}, оглавлений {agendas}" if unknown or agendas > 1 else ""))

    stop = prose_cfg.stopwords
    vocab = set(_tokens(text))
    # Название колоды — заголовок обложки — словами автора в обоих режимах: оно
    # виднее всего. Тезисы обложки и финала по словам **не** сверяются, и это
    # решил замер Ш1б, а не удобство: в «доработать» такая сверка дала четыре
    # ложных отказа на законных переформулировках («Данные за полугодие…» из
    # «с цифрами… за полугодие») и ни одной находки, которой не дали бы клише
    # и дословность. Лозунг в «оставить» ловит дословность, «Спасибо…» — клише;
    # лозунг в «доработать» код не ловит — это оттенок смысла, за растром.
    title = slides[0]["heading"] if slides[0]["kind"] == "cover" else ""
    foreign = sorted({w for w in _content(_tokens(title), stop)
                      if not w[0].isdigit() and not _in_vocab(w, vocab)})
    checks.append(Check("название", not foreign,
                        f"слов нет в тексте: {foreign}" if foreign else ""))

    lowered = text.lower()
    cliches = [t for t in on_slides
               if any(rx.search(t) and not rx.search(lowered) for rx in closing_captions)]
    checks.append(Check("клише финала", not cliches, f"на слайдах: {cliches}" if cliches else ""))

    if mode == "keep":
        # Дословно — или сжатие одного предложения автора: слова по основам идут
        # в его порядке, лишних нет. Склейка двух предложений («февраль 5%») и
        # подставленное подлежащее («Свежесть: свой склад…») — уже пересказ.
        norm_src = _norm(plain)
        sentences = [_content(_tokens(x), stop) for x in _SENTENCE.split(text) if x.strip()]
        loose = []
        for i, s in enumerate(slides):
            if s["kind"] == "agenda" or _is_title(i, s):
                continue
            for th in s["theses"]:
                words = _content(_tokens(th), stop)
                if _norm(th) in norm_src or (words and any(_subsequence(words, x) for x in sentences)):
                    continue
                loose.append(th)
        checks.append(Check("дословность", not loose, f"не из текста автора: {loose}" if loose else ""))

    return tuple(checks)


def accepted(checks: tuple[Check, ...]) -> bool:
    return all(c.ok for c in checks)


# --- Колода из ответа модели в сборке (`PLAN-9.0`, Ш3) -------------------
#
# Один запрос на колоду: сплошной текст файла целиком, промпт режима и рамки.
# Ответ принимается, только если прошли все проверки выше; тогда из него
# строится `ContentDoc`, а раскладки выбирает код. Всё прочее — путь без модели,
# тот же документ, что дал `load_content`, и слова о том, почему.

#: Рамки объёма ТЗ (раздел 2) — рамки модели, когда `--slides` не задан. То же,
#: что `cli.TZ_SLIDES`, — сверяет тест. Это рамки **промпта**, а не добор: путь
#: без модели без флага объём не подгоняет (`Z-35`), и код с моделью — тоже.
#: Замер: модель рамки не добивает — «меньше — меньше» (short 7, numbers 9;
#: `WORKLOG/2026-09-25-z57-sh3-baseline.md`, § 3).
TZ_FRAMES = (10, 15)

#: Режим текста по умолчанию — **до решения пользователя** (`PLAN-9.0`, Ш9).
#: «Оставить» осторожнее: отказ проверок ведёт на путь без модели, а лозунг в
#: «доработать» код не ловит (`ADR-0023`, «Что уточнил замер»).
DEFAULT_MODE = "keep"

#: Имя схемы — как в замере Ш1б (`out/z57/sh1b_probe.py`); на ответ не влияет.
_NAME, _TOOL = "deck", "build_deck"
_PURPOSE = "Разложить текст автора по слайдам презентации."

#: Исходы, которые знает `outline.json` (`contracts/outline.schema.json`).
STATUSES = ("accepted", "rejected", "unparsed", "no_answer", "too_long", "off", "markup")

RECORD_NAME = "outline.json"
RECORD_VERSION = "1.0"


def request(text: str, mode: str, frames: tuple[int, int], transport=None, label: str = "колода"):
    """Запрос колоды: системный промпт режима с рамками и текст автора целиком.

    Текст — **файл как есть**, а не `ContentDoc.origin`: тот собран из одних
    абзацев, и у `numbers` без списка месяцев в нём нет «41 200»
    (`WORKLOG/2026-09-25-z57-sh3-baseline.md`, § 1). Замер Ш1б слал файл
    целиком, и тело запроса продукта дало его ответы 10 из 10 (там же, § 2).
    """
    from .prompt import Mode, Request

    transport = transport or Mode.JSON_SCHEMA
    user = text
    if transport is Mode.FREE_TEXT:
        # Схема без `response_format` доходит до модели только текстом —
        # так же, как у `build_request` прежнего контракта.
        user += "\n\nОтветь одним JSON-объектом по схеме:\n" + json.dumps(
            RESPONSE_SCHEMA, ensure_ascii=False)
    return Request(
        mode=transport, system=system_prompt(mode, frames[0], frames[1]), user=user,
        schema=RESPONSE_SCHEMA, section_id=label, candidates=(),
        name=_NAME, tool=_TOOL, purpose=_PURPOSE,
    )


def too_long(system: str, user: str, endpoint) -> str | None:
    """Причина не звать модель, если запрос с ответом не влезет в контекст.

    Токенизатор есть только у сервера, поэтому оценка — знаками:
    `endpoint.chars_per_token` снизу (`config/model.json`). Запас под ответ —
    весь `max_tokens`. Длиннее — путь без модели с заметкой: сервер отказал бы
    сам (HTTP 400 за полсекунды), но вызов был бы потрачен, а причина — не
    названа (`WORKLOG/2026-09-25-z57-sh3-baseline.md`, § 4).
    """
    estimate = math.ceil((len(system) + len(user)) / endpoint.chars_per_token)
    if estimate + endpoint.max_tokens <= endpoint.context_tokens:
        return None
    room = int((endpoint.context_tokens - endpoint.max_tokens) * endpoint.chars_per_token)
    return (f"текст {len(user)} знаков длиннее порога {max(room - len(system), 0)}: "
            f"контекст {endpoint.context_tokens} токенов, из них {endpoint.max_tokens} — под ответ")


def to_doc(answer: dict, text: str, path: str, name: str, prose_cfg=None) -> tuple:
    """Документ из принятого ответа. Возвращает (документ, заметки, колода для
    `outline.json`).

    - **Обложка — только название**: название документа и первый раздел с тем
      же заголовком без блоков, тогда `_with_cover` делает ровно одну обложку
      (`PLAN-9.0`, проверка 1, п. 14). Подзаголовок модели на слайд не идёт —
      проверки его и не считают (`_is_title`).
    - **Пункты оглавления собирает код** — заголовки остальных слайдов, кроме
      обложки и оглавления: модель их пересказывает (замер 23 сентября).
    - Тезисы делятся на пункты и абзацы **тем же правилом, что у пути без
      модели** (`prose._blocks_from`); у цитаты — блок цитаты, иначе вид
      «quote» не вошёл бы в пригодные никогда.
    - Картинки — по путям из текста, от каталога текста и от текущего, как у
      `extract_images`; файла нет — заметка, а не текст пути на слайде.
    """
    from .content import ContentBlock, ContentDoc, ContentSection, resolve_image
    from .prose import _blocks_from, _Numbering, load_config as load_prose

    prose_cfg = prose_cfg or load_prose()
    slides = answer["slides"]
    title = slides[0]["heading"] if _is_title(0, slides[0]) else None
    headings = [s["heading"] for i, s in enumerate(slides)
                if s["kind"] != "agenda" and not _is_title(i, s)]
    bases = (os.path.dirname(os.path.abspath(path)), os.getcwd())
    ids = _Numbering()
    sections, notes, deck = [], [], []
    for i, s in enumerate(slides):
        sid = f"m{i + 1:02d}"
        idea = s.get("image_idea") if isinstance(s.get("image_idea"), str) else ""
        if _is_title(i, s):
            sections.append(ContentSection(id=sid, heading=title, blocks=(), kind="cover",
                                           image_idea=idea))
            deck.append({"heading": title, "kind": "cover", "theses": [], "images": [],
                         "image_idea": idea})
            continue
        theses = list(headings) if s["kind"] == "agenda" else [t for t in s["theses"] if t.strip()]
        if s["kind"] == "agenda" and not theses:
            continue            # оглавлению нечего перечислять — слайда нет
        if s["kind"] == "quote":
            blocks = tuple(ContentBlock(id=ids.next(), kind="quote", text=t) for t in theses)
        else:
            blocks = _blocks_from(theses, prose_cfg, ids)
        images = []
        for ref in s["images"]:
            where = resolve_image(ref, *bases)
            if where is None:
                notes.append(f"Модель поставила картинку «{ref}», но файла нет на диске — "
                             "картинка не вставлена.")
                continue
            images.append(ContentBlock(id=f"{sid}i{len(images) + 1:02d}", kind="image", ref=where))
        sections.append(ContentSection(id=sid, heading=s["heading"], blocks=blocks + tuple(images),
                                       kind=s["kind"], image_idea=idea))
        deck.append({"heading": s["heading"], "kind": s["kind"], "theses": theses,
                     "images": [b.ref for b in images], "image_idea": idea})
    doc = ContentDoc(name=name, title=title, sections=sections, origin=text,
                     notes=tuple(notes), planner="mixed")
    return doc, notes, deck


@dataclass(frozen=True)
class Outcome:
    """Чем кончился путь модели. `doc` идёт в вёрстку всегда: документ из
    ответа, если он принят, иначе — документ пути без модели, с заметкой."""

    doc: object
    status: str
    line: str
    record: dict

    @property
    def by_model(self) -> bool:
        return self.status == "accepted"


def _source(chosen: bool, default: str) -> str:
    return "задано при запуске" if chosen else default


def run(path: str, fallback, *, access: str | None = None, text_mode: str | None = None,
        target: tuple[int, int] | None = None, config=None, prose_cfg=None,
        closing_captions=None) -> Outcome:
    """Путь модели для одной сборки: решить, звать ли, спросить, проверить.

    `fallback` — документ `load_content`: он же признак прозы (`origin`) и он
    же колода, если модель не дала годного ответа. `access` и `text_mode` —
    флаги (`None` — не заданы); `target` — `--slides`.
    """
    from . import client as model_client
    from .validate import extract_json

    config = config or model_client.load_config()
    access_value = access or config.access.value
    access_src = _source(access is not None, "умолчание config/model.json")
    mode = text_mode or DEFAULT_MODE
    mode_src = _source(text_mode is not None, "умолчание")
    frames = tuple(target) if target else TZ_FRAMES
    frames_src = "--slides" if target else "рамки ТЗ"
    ignored = f"; --text {text_mode} не применяется" if text_mode else ""

    record: dict = {
        "version": RECORD_VERSION,
        "status": "",
        "line": "",
        "access": access_value,
        "access_source": access_src,
        "text_mode": mode,
        "text_mode_source": mode_src,
        "frames": list(frames),
        "frames_source": frames_src,
        "text": {"path": path.replace("\\", "/"), "chars": None, "content": None},
        "model": config.endpoint.model,
        "prompt_version": _prompt_version(),
        "key": None,
        "answer_source": None,
        "seconds": None,
        "answer": None,
        "checks": [],
        "deck": None,
    }

    def done(status: str, line: str, doc=None) -> Outcome:
        # Та же строка — заметкой в предупреждения плана: веб видит итог сборки
        # только через `--report`, а печатную сводку разбирать нельзя (`Z-46`).
        record["status"], record["line"] = status, line
        base = doc if doc is not None else fallback
        base = replace(base, notes=tuple(base.notes) + (f"Модель {line}.",))
        return Outcome(doc=base, status=status, line=line, record=record)

    if not fallback.origin:
        return done("markup", f"не нужна: вход размечен, структуру задал автор (Z-08){ignored}")
    if access_value == model_client.Access.OFF.value:
        return done("off", f"выключена ({access_src}): колода собрана путём без модели{ignored}")

    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    record["text"].update(chars=len(text), content=text)
    head = (f"{access_value} ({access_src}), текст {mode} ({mode_src}), "
            f"рамки {frames[0]}–{frames[1]} ({frames_src}): ")

    req = request(text, mode, frames, config.mode, label=f"колода {os.path.basename(path)} {mode}")
    client = model_client.ModelClient(
        replace(config, access=model_client.Access(access_value)), inputs=(path,))
    record["key"] = client.key(req)
    reason = too_long(req.system, req.user, config.endpoint)
    if reason:
        return done("too_long", head + f"{reason} — собрано путём без модели")

    answer = client.complete(req)
    if not answer:
        return done("no_answer", head + f"{answer.note} — собрано путём без модели")
    record["answer_source"] = answer.source
    record["seconds"] = round(answer.elapsed, 1) if answer.source == "model" else None
    got = "ответ из кэша" if answer.source == "cache" else f"ответ модели за {answer.elapsed:.1f} с"
    parsed = extract_json(answer.text)
    if parsed is None:
        record["answer"] = answer.text
        return done("unparsed", head + f"{got} не разобрался как JSON — собрано путём без модели")
    record["answer"] = parsed

    checks = check_outline(text, parsed, mode, frames[1], prose_cfg, closing_captions)
    record["checks"] = [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks]
    if not accepted(checks):
        failed = ", ".join(f"«{c.name}»" for c in checks if not c.ok)
        return done("rejected", head + f"{got} отвергнут проверками {failed} — "
                    "собрано путём без модели")

    doc, _notes, deck = to_doc(parsed, text, path, fallback.name, prose_cfg)
    record["deck"] = deck
    kept = "записан в кэш, " if answer.source == "model" else ""
    return done("accepted", head + f"колоду построила модель — {got}, {kept}проверки пройдены; "
                f"слайдов в ответе {len(parsed['slides'])}", doc)


def _prompt_version() -> str | None:
    from .. import config as cfg

    return cfg.stamp_file(config_path()).version


def write_record(out_dir: str, record: dict) -> str:
    """Кладёт `outline.json` в каталог артефактов. Пишется **всегда**, со
    статусом: иначе файл прошлой сборки в том же каталоге соврал бы про эту."""
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, RECORD_NAME)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return target
