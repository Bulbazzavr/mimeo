"""Сегментация сплошной прозы. Задача `Z-25`, план `PLAN-2.2`.

Тесты стерегут три обещания, которые легко потерять молча: ничего из текста
пользователя не пропадает и не переставляется, размеченный вход не трогается
вовсе, и предел длины тезиса — гарантия, а не тенденция.
"""

from __future__ import annotations

from mimeo.plan.content import ContentBlock, ContentDoc, ContentSection, parse_markdown
from mimeo.plan.prose import (
    ProseConfig,
    _Topic,
    _strip_lead_in,
    _units,
    deck_title,
    group_topics,
    heading_for,
    load_config,
    needs_restructure,
    restructure,
    split_sentences,
    split_theses,
)

CFG = load_config()

CHAT = (
    "Привет! Нужна презентация про наш сервис доставки «Свежесть» для инвесторов. "
    "Расскажи, что мы вообще делаем: мы работаем в семи городах, привозим продукты "
    "из хозяйств за два часа, средний чек около 2400 руб. и выручка выросла втрое. "
    "Наша модель: подписка на 1990 рублей в месяц даёт бесплатную доставку, сейчас "
    "подписчиков 34 тысячи, удержание через полгода 61%. "
    "Ещё важно сказать про команду: четырнадцать человек, основатели строили "
    "логистику в X5 и в Ozon. "
    "Риски тоже нужно упомянуть честно: сезонность поставок зимой и зависимость от "
    "небольшого числа хозяйств в двух регионах."
)


def _doc(text: str) -> ContentDoc:
    return parse_markdown(text, name="проза")


def _signature(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


def _pieces(doc: ContentDoc) -> list[str]:
    """Всё, что увидит зритель, в порядке появления. Обложка повторяет заголовок
    колоды по устройству модели (как `# Заголовок` в разметке) и не считается
    вторым вхождением текста."""
    out = [doc.title or ""]
    for n, section in enumerate(doc.sections):
        if not (n == 0 and section.heading == doc.title):
            out.append(section.heading or "")
        for block in section.blocks:
            out.extend(block.items if block.kind == "list" else [block.text])
    return [p for p in out if p]


# --- конфиг ------------------------------------------------------------


def test_config_is_read_from_file_and_says_so() -> None:
    assert CFG.loaded, "config/prose.json не прочитан"
    assert CFG.lead_in and CFG.deck_title and CFG.stopwords


def test_missing_config_is_not_silent() -> None:
    """Отсутствие конфига — работа на встроенных значениях, и это видно."""
    cfg = load_config("нет-такого-файла.json")
    assert not cfg.loaded
    assert cfg.thesis_max > 0 and cfg.abbreviations
    doc = restructure(_doc(CHAT), cfg)
    assert any("Конфиг сегментации не прочитан" in note for note in doc.notes)


# --- предложения -------------------------------------------------------


def test_sentences_survive_abbreviations_and_numbers() -> None:
    text = "Выручка 2,4 млрд руб. за год. Доля рынка т. е. около 4%. Основал А. Иванов."
    assert split_sentences(text, CFG) == (
        "Выручка 2,4 млрд руб. за год.",
        "Доля рынка т. е. около 4%.",
        "Основал А. Иванов.",
    )


def test_lowercase_after_dot_is_not_a_boundary() -> None:
    assert len(split_sentences("Сервис работает в 7 городах. и растёт втрое.", CFG)) == 1


# --- тезисы ------------------------------------------------------------


def test_short_sentence_stays_whole() -> None:
    """Режется только то, что не влезает: иначе «А — Б» распадается на огрызки."""
    sentence = "Основные конкуренты — большие маркетплейсы."
    assert split_theses(sentence, CFG) == [sentence]


def test_long_sentence_is_cut_and_nothing_exceeds_the_limit() -> None:
    sentence = ("Деньги нужны на три вещи — запуск в ещё пяти городах, собственный "
                "автопарк холодильников, разработка мобильного приложения и наём "
                "команды в новые регионы, всего просим 120 миллионов рублей за 15%.")
    theses = split_theses(sentence, CFG)
    assert len(theses) > 1
    assert max(len(t) for t in theses) <= CFG.thesis_max


def test_wall_without_any_delimiter_still_obeys_the_limit() -> None:
    """Последний рубеж: текст без единого знака препинания режется по словам."""
    wall = " ".join(["слово"] * 200)
    theses = split_theses(wall, CFG)
    assert max(len(t) for t in theses) <= CFG.thesis_max


# --- темы и заголовки --------------------------------------------------


def test_label_starts_its_own_topic() -> None:
    """«Наша модель:» — автор сам назвал тему, она не должна тонуть в соседней."""
    units, _ = _units(list(split_sentences(CHAT, CFG)), CFG)
    headings = [heading_for(t, CFG)[0] for t in group_topics(units, CFG)]
    assert "Наша модель" in headings
    assert any(h and h.startswith("Риски") for h in headings)


def test_heading_comes_from_the_start_of_the_topic() -> None:
    """Заголовок — то, с чего тема начата, а не самый короткий тезис в ней.
    Первая версия брала самый короткий, и заголовок съезжал на случайную мысль
    из середины (журнал `PLAN-2.2`)."""
    topic = _Topic(
        label=None,
        theses=["Рынок фермерских продуктов", "Ниша пустая", "Доля меньше 4% от всего рынка"],
    )
    heading, body = heading_for(topic, CFG)
    assert heading == "Рынок фермерских продуктов"
    assert body == ["Ниша пустая", "Доля меньше 4% от всего рынка"]


def test_single_thesis_topic_does_not_become_its_own_heading() -> None:
    """Тема из одного тезиса: взять его в заголовок значит либо задвоить текст,
    либо оставить слайд с одним заголовком. И то и другое — дефект из
    Приложения 1 ТЗ. Нашлось при подгонке объёма (`PLAN-2.3`)."""
    topic = _Topic(label=None, theses=["То есть ниша почти пустая"])
    heading, body = heading_for(topic, CFG)
    assert body == ["То есть ниша почти пустая"], "тезис обязан остаться в теле"
    assert heading != body[0], "заголовок не должен повторять единственный абзац"
    assert heading, "и при этом заголовок нужен: пустой слот — дефект Z-23"


def test_lead_in_is_stripped_from_the_thesis() -> None:
    """«Расскажи, что…» — обращение к чату, а не тезис слайда."""
    text, lost = _strip_lead_in("Расскажи, что мы работаем в семи городах", CFG)
    assert text == "Мы работаем в семи городах"
    assert lost == len("Расскажи, что ")
    units, dropped = _units(["Расскажи, что мы работаем в семи городах и возим продукты"], CFG)
    assert dropped > 0
    assert not any(t.lower().startswith("расскажи") for u in units for t in u.theses)


def test_heading_is_not_repeated_in_the_body() -> None:
    doc = restructure(_doc(CHAT), CFG)
    for section in doc.sections:
        texts = [b.text for b in section.blocks] + [
            i for b in section.blocks for i in b.items
        ]
        assert section.heading not in texts, f"заголовок повторён в теле: {section.heading}"


def test_deck_title_is_taken_from_the_request_not_from_the_greeting() -> None:
    title, at = deck_title(list(split_sentences(CHAT, CFG)), CFG)
    assert title == "Наш сервис доставки «Свежесть»"
    assert at == 1, "просьба стоит вторым предложением, приветствие первым"


def test_cover_matches_the_deck_title() -> None:
    """Иначе `_with_cover` не выделит титульный слайд (`deterministic.py`)."""
    doc = restructure(_doc(CHAT), CFG)
    assert doc.title and doc.sections[0].heading == doc.title


def test_every_topic_has_a_heading() -> None:
    """Раздел без заголовка оставит пустую оформленную рамку (`Z-23`)."""
    doc = restructure(_doc(CHAT), CFG)
    assert all(s.heading for s in doc.sections)


# --- целостность -------------------------------------------------------


def test_nothing_is_invented_or_reordered() -> None:
    """Каждый кусок результата встречается в исходнике дословно и по порядку."""
    doc = restructure(_doc(CHAT), CFG)
    source = _signature(CHAT)
    at = 0
    for piece in _pieces(doc):
        found = source.find(_signature(piece), at)
        assert found >= 0, f"в исходнике нет куска: {piece}"
        at = found + len(_signature(piece))


def test_losses_are_counted_not_hidden() -> None:
    """Потерянное не оценивается на глаз: оно должно умещаться в то, о чём
    сегментатор сам доложил. Иначе «отброшено 138 знаков» — намерение, а не
    результат (правило `CLAUDE.md` про счёт намерения)."""
    doc = restructure(_doc(CHAT), CFG)
    kept = sum(len(_signature(p)) for p in _pieces(doc))
    lost = len(_signature(CHAT)) - kept
    declared = int(
        next(n for n in doc.notes if "Отброшено" in n).split(":")[1].split()[0]
    )
    assert lost > 0, "обращение и вводные обороты действительно отбрасываются"
    assert lost <= declared, "исчезло больше, чем заявлено в диагностике"
    assert kept > len(_signature(CHAT)) * 4 // 5, "тело потеряло больше пятой части"
    assert any("В тело не пошло" in note for note in doc.notes)


def test_theses_become_atoms() -> None:
    """Ради этого всё и затевалось: `split` дробит раздел по атомам, а атом —
    блок или пункт списка. До сегментации простыня была одним атомом, и четыре
    колоды из одиннадцати выходили пустыми (`WORKLOG/2026-09-14-prose-input.md`).
    """
    from mimeo.plan.content import load
    from mimeo.plan.deterministic import split

    raw = load("examples/content-prose.md")
    assert sum(b.units for s in raw.sections for b in s.blocks) == 1, (
        "исходная посылка: без сегментации вся проза — один неделимый атом"
    )
    doc = restructure(raw, CFG)
    atoms = sum(b.units for s in doc.sections for b in s.blocks)
    assert atoms >= 8, f"атомов после сегментации всего {atoms}"
    biggest = max(doc.sections, key=lambda s: sum(b.units for b in s.blocks))
    assert len(split(biggest, 2)) == 2


# --- неприкосновенность размеченного входа -----------------------------


MARKED = """# Сервис прогнозирования

Вводный абзац про то, что платформа собирает телеметрию и предсказывает отказы
оборудования за сутки до события, снижая долю ложных срабатываний в разы.

## Что умеет

- Прогнозирует деградацию датчиков
- Оценивает риск задымления
"""


def test_marked_up_input_is_untouched() -> None:
    doc = _doc(MARKED)
    assert not needs_restructure(doc, CFG)
    assert restructure(doc, CFG) is doc


def test_trigger_needs_both_conditions() -> None:
    """Длинный абзац под заголовком — не проза: у него есть структура."""
    long_text = "Очень длинный абзац. " * 20
    with_heading = ContentDoc(
        name="x",
        sections=[
            ContentSection(
                id="sec01",
                heading="Раздел",
                blocks=(ContentBlock(id="b01", kind="paragraph", text=long_text),),
            )
        ],
    )
    assert not needs_restructure(with_heading, CFG)
    without = ContentDoc(
        name="x",
        sections=[
            ContentSection(
                id="sec01",
                heading=None,
                blocks=(ContentBlock(id="b01", kind="paragraph", text=long_text),),
            )
        ],
    )
    assert needs_restructure(without, CFG)


def test_short_prose_is_left_alone() -> None:
    """Абзац короче порога — не «сплошное описание», а просто короткий текст."""
    doc = _doc("Мы возим продукты из хозяйств за два часа.")
    assert not needs_restructure(doc, CFG)


# --- детерминированность ------------------------------------------------


def test_same_input_gives_the_same_structure() -> None:
    first = restructure(_doc(CHAT), CFG)
    second = restructure(_doc(CHAT), CFG)
    assert [(s.id, s.heading, s.blocks) for s in first.sections] == [
        (s.id, s.heading, s.blocks) for s in second.sections
    ]
    assert first.title == second.title and first.notes == second.notes


def test_config_phrase_order_does_not_depend_on_file_order() -> None:
    """Длинная подсказка должна срабатывать раньше короткой независимо от того,
    в каком порядке они записаны в конфиге."""
    cfg = ProseConfig(lead_in=("ещё важно", "ещё важно сказать про"), loaded=True)
    ordered = load_config().lead_in
    assert list(ordered) == sorted(ordered, key=lambda s: (-len(s), s))
    assert cfg.lead_in != ordered
