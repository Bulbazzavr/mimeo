"""Модель строит колоду из текста до вёрстки — `ADR-0023`, `PLAN-9.0`, `Z-57`.

Здесь то, что дали шаги Ш1 и Ш1б: формулировки промпта из `config/outline.json`,
схема ответа и проверки ответа (`check_outline`). Схема и проверки — контракт,
они в коде; формулировки — настройка, они в конфиге (`ADR-0022`). В сборку модуль
ещё не включён — это Ш3. Промпт 1.1 снят замером дословно
(`WORKLOG/2026-09-25-z57-prompt.md`) и заморожен до замера Ш9: любая его правка —
перемер на пяти текстах прозы корпуса в обоих режимах без кэша промпта
(`ADR-0023`, «Что уточнил замер»).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

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


def _slide_texts(slides: list[dict]) -> list[str]:
    """Что окажется на слайдах: заголовки и тезисы. Тезисы оглавления не в
    счёт — пункты оглавления собирает код из заголовков колоды (Ш3)."""
    out = []
    for s in slides:
        out.append(s["heading"])
        if s["kind"] != "agenda":
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
    out_paths = sorted(p for s in slides for p in s["images"])
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
        for s in slides:
            if s["kind"] == "agenda":
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
