"""Сегментация сплошной прозы. Задача `Z-25`, план `PLAN-2.2`.

Тесты стерегут три обещания, которые легко потерять молча: ничего из текста
пользователя не пропадает и не переставляется, размеченный вход не трогается
вовсе, и предел длины тезиса — гарантия, а не тенденция.
"""

from __future__ import annotations

import os

import pytest

from mimeo.plan import load_content
from mimeo.plan.content import ContentBlock, ContentDoc, ContentSection, parse_markdown
from mimeo.plan.prose import (
    ProseConfig,
    _strip_discourse,
    _strip_lead_in,
    _tidy,
    _Topic,
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
_EXAMPLES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples"
)

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
    Приложения 1 ТЗ. Нашлось при подгонке объёма (`PLAN-2.3`).

    **Первоисточник проверен 19 сентября и подтвердил это дословно:**
    Приложение 1 перечисляет «пустой слайд или слайд с одним заголовком», а
    контекстуальная проверка 5 спрашивает «на слайде есть содержание, а не
    только заголовок?». `PLAN-7.2` собирался это правило снять, утверждая, что
    раздел без тела штатно ляжет на разделитель, — **утверждение было неверным**,
    и поймал его этот тест.

    **Третье утверждение при этом снято, и тоже замером** (`Z-40`,
    `WORKLOG/2026-09-19-z40-baseline.md`, замер 5). Здесь стояло «и при этом
    заголовок нужен: пустой слот — дефект `Z-23`». Растр настоящего PowerPoint
    на VK WorkSpace показал, что пустой слот заголовка **не оставляет ни рамки,
    ни плашки** — просто пустое поле. А заголовок, который приходилось ради
    этого утверждения выдумывать, давал «Вход движок» и «Корпусу дало»:
    словосочетания, которых во входном тексте нет.

    Лучше без заголовка, чем с выдуманным. Содержание при этом не теряется —
    его и стережёт первое утверждение.
    """
    topic = _Topic(label=None, theses=["То есть ниша почти пустая"])
    heading, body = heading_for(topic, CFG)
    assert body == ["То есть ниша почти пустая"], "тезис обязан остаться в теле"
    assert heading != body[0], "заголовок не должен повторять единственный абзац"


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


# --- деловой текст: `Z-39`, план `PLAN-7.1` ------------------------------
#
# Форма, из-за которой заведена задача: короткие абзацы без единого заголовка,
# темы помечены не вводным словом, а именной группой с двоеточием. До
# 19 сентября такой текст не входил в сегментатор **вовсе**: ворота спрашивали
# «есть ли абзац длиннее порога», а самый длинный был 184 знака при пороге 200.

BUSINESS = (
    "Нужна презентация с цифрами по загрузке сети пунктов выдачи за полугодие.\n\n"
    "Среднее время выдачи одного заказа: январь 2.8 мин, июнь 3.9.\n\n"
    "По регионам за июнь: Центр 22 400 заказов, Урал 11 800.\n\n"
    "Нагрузка на сотрудника в пике: 19 заказов в час при норме 14.\n\n"
    "Что предлагаем: открыть 12 пунктов в Центре, добавить вторую смену.\n"
)


def _business() -> ContentDoc:
    return ContentDoc(name="x", sections=list(parse_markdown(BUSINESS).sections))


def test_many_short_paragraphs_without_headings_are_prose() -> None:
    """Неразмеченность бывает двух видов, и раньше видели только один.

    Простыня — один длинный абзац. Деловой текст — много коротких. Структуры у
    второго ровно столько же: ноль. Ворота, спрашивающие про длину абзаца,
    отвечали «не проза» и возвращали документ нетронутым.
    """
    doc = _business()
    assert all(
        b.length <= CFG.prose_paragraph
        for s in doc.sections
        for b in s.blocks
        if b.kind == "paragraph"
    ), "текст перестал быть примером: появился абзац длиннее порога"
    assert needs_restructure(doc, CFG)


def test_business_text_gives_a_topic_per_paragraph() -> None:
    """Раньше девять абзацев слипались в одну тему, и колода выходила пустой."""
    out = restructure(_business(), CFG)
    assert len(out.sections) >= 4, [s.heading for s in out.sections]


def test_labels_become_headings_without_any_new_vocabulary() -> None:
    """Метку через двоеточие механизм понимал и до `Z-39` — до него просто не
    доходило. Словарь оборотов смены темы тут ни при чём."""
    headings = {s.heading for s in restructure(_business(), CFG).sections}
    assert "По регионам за июнь" in headings, headings
    assert "Нагрузка на сотрудника в пике" in headings, headings
    assert "Что предлагаем" in headings, headings


def test_section_ids_are_unique() -> None:
    """Дубль id — не косметика: `deterministic.py` держит `forced` словарём по
    `section.id`, и два раздела с одним id делят одну запись, то есть
    принудительное дробление одного молча применяется к другому."""
    for text in (BUSINESS, CHAT):
        ids = [s.id for s in restructure(_doc(text), CFG).sections]
        assert len(ids) == len(set(ids)), ids


def test_volume_target_is_applied_once_per_section() -> None:
    """Подгонка объёма вызывалась на каждый блок. Когда блок в разделе один —
    незаметно; после `Z-39` блоков много, и цель «десять тем» пришлась бы на
    каждый абзац по отдельности."""
    out = restructure(_business(), CFG, target=(4, 5))
    # Обложка не тема: у неё есть заголовок и нет содержимого, и в цель по
    # слайдам она входит отдельно (`PLAN-2.3` — цель уменьшается на единицу).
    topics = [s for s in out.sections if s.heading and s.id != "cover"]
    assert len(topics) <= 4, [s.heading for s in out.sections]
    shortfall = [n for n in out.notes if "Целевой объём не достигнут" in n]
    assert len(shortfall) <= 1, shortfall


def test_non_paragraph_block_sticks_to_its_topic() -> None:
    """Список под строкой «Вот выдача по месяцам:» принадлежит ей, а не себе."""
    text = (
        "Нужна презентация про загрузку сети.\n\n"
        "Вот выдача по месяцам, заказов:\n\n"
        "- январь — 41 200\n- февраль — 38 900\n- март — 52 400\n\n"
        "Что предлагаем: открыть 12 пунктов в Центре, добавить вторую смену.\n"
    )
    out = restructure(_doc(text), CFG)
    kinds = [b.kind for s in out.sections for b in s.blocks]
    assert "list" in kinds, "список пропал"
    holder = next(s for s in out.sections if any(b.kind == "list" for b in s.blocks))
    assert holder.heading, "список оказался в разделе без темы"


def test_deck_title_tolerates_words_before_the_preposition() -> None:
    """«Нужна презентация **с цифрами** по загрузке…» до `Z-39` не давала
    заголовка вовсе: словарь искался подстрокой."""
    title, _ = deck_title(list(split_sentences(BUSINESS, CFG)), CFG)
    assert title and "загрузке" in title.lower(), title


def test_kept_preposition_saves_the_case() -> None:
    """Срезанный предлог оставляет «Загрузке сети…» — обрубок на самом заметном
    слайде колоды. С предлогом выходит правильная русская фраза."""
    title, _ = deck_title(list(split_sentences(BUSINESS, CFG)), CFG)
    assert title.startswith("По "), title


# --- заголовок из слов автора: `Z-40`, план `PLAN-7.2` -------------------
#
# До 19 сентября был четвёртый путь — склеить два самых частых слова темы. Он
# давал «Вход движок», «Корпусу дало», «Выдачи больше»: словосочетания, которых
# во входном тексте нет. Убран не за некрасивость, а потому что `CLAUDE.md`
# запрещает выдумывать содержание, которого во входе нет.


@pytest.mark.parametrize(
    "path",
    sorted(
        os.path.join(_EXAMPLES, n)
        for n in os.listdir(_EXAMPLES)
        if n.startswith("content-") and n.endswith(".md")
    ),
    ids=os.path.basename,
)
def test_no_heading_is_invented(path: str) -> None:
    """Главная проверка задачи: **каждый заголовок есть во входе дословно.**

    Машинная формулировка правила «не выдумывать содержание». Сравнение по
    нижнему регистру — `_capitalize` меняет первую букву (логическая проверка
    плана, Н7). Пробелы схлопываются: сегментатор нормализует их, и «два
    пробела» не должны ронять проверку.
    """
    with open(path, encoding="utf-8") as fh:
        source = " ".join(fh.read().split()).lower()
    doc = load_content(path)
    invented = [
        s.heading
        for s in doc.sections
        if s.heading and s.id != "cover" and " ".join(s.heading.split()).lower() not in source
    ]
    assert not invented, f"заголовков, которых нет во входе: {invented}"


def test_word_salad_generator_is_gone() -> None:
    """`_synthesize` удалён, а не обойдён. Оставленная функция вернулась бы в
    код при первой же правке «а давайте хоть что-нибудь напишем»."""
    import mimeo.plan.prose as module

    assert not hasattr(module, "_synthesize")


def test_label_longer_than_five_words_is_accepted() -> None:
    """«Доля заказов, выданных дольше пяти минут» — 39 знаков, шесть слов.

    Счёт слов отвергал три готовых заголовка из четырёх, а `heading_max` и так
    делает работу: при пределе 6, 7 и без предела вовсе результат одинаков."""
    label = "Доля заказов, выданных дольше пяти минут"
    assert len(label.split()) > 5
    units, _ = _units([f"{label}: 4%, 5%, 9%, 11%, 8%, 14%."], CFG)
    assert units[0].label == label


def test_dangling_tail_of_a_label_is_trimmed() -> None:
    """«По срокам мы отстаём, и это» — метка, оборванная на союзе.

    Дефект внесён правкой привратника и найден **чтением вывода**: до неё такие
    метки отвергались по числу слов, и один дефект прятался за другим."""
    topic = _Topic(label="По срокам мы отстаём, и это", theses=["Прямо, а не прятать"])
    heading, _ = heading_for(topic, CFG)
    assert heading == "По срокам мы отстаём"


def test_short_remainder_keeps_the_label_whole() -> None:
    """Порог защищает «То, что нам нужно» от превращения в «То»."""
    heading, _ = heading_for(_Topic(label="То, что нам нужно", theses=["тело"]), CFG)
    assert heading == "То, что нам нужно"


def test_authors_own_remark_is_dropped_even_when_the_rest_is_short() -> None:
    """«Картинки, приложу две» → «Картинки» (`Z-42`, `PLAN-7.4`).

    **Этот случай раньше защищался порогом длины намеренно**, и решение
    отменено с доводом, а не по недосмотру: «Картинки» — такое же однословное
    название темы, как принятые «Риски» и «Воспроизводимость», и оно лучше, чем
    название плюс кусок диктовки. Порог остаётся для всех прочих хвостов, что и
    проверяет тест выше.
    """
    heading, _ = heading_for(_Topic(label="Картинки, приложу две", theses=["тело"]), CFG)
    assert heading == "Картинки"


def test_first_person_plural_is_content_and_stays() -> None:
    """Отрицательные контроли правила (а), и два из трёх придуманы нарочно.

    Замер: мест с первым лицом по корпусу 24, реплика одна. Правило обязано
    выдержать вход, которого у нас нет."""
    for label in ("Мы покажем результаты", "Я работаю в Москве", "Мы работаем в семи городах"):
        heading, _ = heading_for(_Topic(label=label, theses=["тело"]), CFG)
        assert heading == label, label


def test_cut_is_taken_at_the_first_suitable_boundary_not_the_first() -> None:
    """«Теперь про то, что проверено на чужом материале, это важнее…»

    На первой запятой выходит «Теперь про то» — обрубок. На второй — заголовок.
    Искать дальше первой границы стоило: растр показал, что слайд без заголовка
    на карточной раскладке оставляет заметную пустоту сверху.
    """
    thesis = "Теперь про то, что проверено на чужом материале, это важнее всего"
    heading, body = heading_for(_Topic(label=None, theses=[thesis]), CFG)
    # Граница выбрана вторая — это предмет теста. А связка «теперь про то, что»
    # снята сверху (`Z-42`): сторож смотрел на разрез «Теперь про то, что
    # проверено на чужом материале», и только принятый разрез очищается.
    assert heading == "Проверено на чужом материале"
    # Остаток причёсывается как любой тезис — с прописной (`Z-42`). Раньше он
    # уезжал на слайд со строчной буквы; проверка на регистр здесь была
    # побочной, предмет этого теста — выбор границы.
    assert body == ["Это важнее всего"], body


def test_stump_is_refused_rather_than_shipped() -> None:
    """Извлечь нечего — заголовка нет, и содержание остаётся на слайде."""
    thesis = "По нашему корпусу это дало 16 переполнений до и 1 после"
    heading, body = heading_for(_Topic(label=None, theses=[thesis]), CFG)
    assert heading is None
    assert body == [thesis], "содержание обязано остаться"


# --- Z-42: на слайде фраза доклада, а не кусок диктовки -----------------
#
# Три правила и три предела применимости (`PLAN-7.4`). Карточка `Z-42` прямо
# запрещает сводить их в одно: «смешать их в одно правило значит получить
# правило, которое нельзя проверить».


def test_conjunction_is_a_boundary_and_a_bare_comma_is_not() -> None:
    """Запятая в русском размечает придаточные, а не мысли.

    Замер: разрез по каждой запятой дал «Которые сейчас есть», «В зависимости
    от того» — новые обрывки вместо старых. Разрез перед сочинительным союзом
    обрывков не даёт (`WORKLOG/2026-09-20-z42-baseline.md`, замеры 4 и 5).
    """
    sentence = (
        "Когда нужно собрать презентацию в корпоративном стиле, человек берёт "
        "шаблон и часами двигает текст по слайдам руками, а генераторы, "
        "которые сейчас есть, стиль не переносят вовсе"
    )
    out = [_tidy(t) for t in split_theses(sentence, CFG)]
    assert out == [
        ("Когда нужно собрать презентацию в корпоративном стиле, человек берёт "
         "шаблон и часами двигает текст по слайдам руками"),
        "А генераторы, которые сейчас есть, стиль не переносят вовсе",
    ], out


def test_conjunction_before_a_subordinator_is_not_a_boundary() -> None:
    """Сторож на собственное правило: без него оно само порождало обрывок.

    «…, и если текст не поместился — ужимаем» разрезать после запятой значит
    оставить условие без главной части."""
    sentence = (
        "После сборки мы открываем файл настоящим PowerPoint и спрашиваем у "
        "него реальные габариты каждой надписи, а не гадаем по числу знаков, "
        "и если текст не поместился — ужимаем кегль и пересобираем"
    )
    out = [_tidy(t) for t in split_theses(sentence, CFG)]
    assert "И если текст не поместился" not in out, out
    assert any("и если текст не поместился" in t.lower() for t in out), out


def test_long_sentence_without_a_boundary_is_kept_whole() -> None:
    """Четвёртая ступень: обрывок не чинит ни одна стадия, а переполнение чинит
    петля VERIFY. Значит длинный тезис лучше обрубленного."""
    sentence = "Рынок " + "очень длинное описание без единого союза " * 4
    out = split_theses(sentence.strip(), CFG)
    assert len(out) == 1, out
    assert len(out[0]) > CFG.thesis_max


def test_monstrous_sentence_is_still_cut_so_nothing_is_lost() -> None:
    """Пятая ступень: выше потолка выбор уже не «фраза или обрывок», а
    «обрывок или потеря». Раздел, который не влезет ни в один слот, уходит в
    `unplaced` целиком."""
    sentence = "слово " * 200
    out = split_theses(sentence.strip(), CFG)
    assert len(out) > 1


def test_discourse_connective_is_stripped_from_our_own_cut() -> None:
    """«Теперь про то, что проверено…» → «Проверено на чужом материале».

    Наречие и рамка перед придаточным снимаются подряд: одним проходом вышло бы
    только полдела."""
    topic = _Topic(label=None, theses=[
        "Теперь про то, что проверено на чужом материале, это важнее всего"])
    heading, _ = heading_for(topic, CFG)
    assert heading == "Проверено на чужом материале"


def test_authors_own_label_is_never_touched() -> None:
    """Пять заголовков корпуса из семи — метки самого автора, и все пять хороши.

    Он поставил двоеточие и назвал тему; это разметка, а не наша догадка.
    Приёмка правила (б): два улучшились, **пять побайтово те же**."""
    for label in ("Насчёт модели", "Риски", "Заканчиваем планами",
                  "Что показывает юнит-экономика", "Деньги нужны на три вещи"):
        heading, _ = heading_for(_Topic(label=label, theses=["тело"]), CFG)
        assert heading == label, label


def test_preposition_is_not_stripped_because_case_would_break() -> None:
    """«Насчёт модели» без предлога даёт «Модели» в косвенном падеже.

    Предлог управляет падежом, а склонения у нас нет (`ADR-0001`). Поэтому в
    списке наречия и рамки, но не предлоги — названный предел, а не недосмотр.
    """
    assert _strip_discourse("Насчёт модели", CFG) == "Насчёт модели"
    assert _strip_discourse("Про зависимости", CFG) == "Про зависимости"


def test_topic_noun_is_not_mistaken_for_a_connective() -> None:
    """«Риски», «Деньги», «Планы» стоят в `topic_shift` как признак границы, но
    это слова **о предмете** — они и есть заголовок."""
    for head in ("Риски", "Деньги нужны на три вещи", "Планы на квартал"):
        assert _strip_discourse(head, CFG) == head, head
