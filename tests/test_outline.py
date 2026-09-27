"""Промпт и схема ответа модели, строящей колоду (`ADR-0023`, `PLAN-9.0`, Ш1)."""

import json
import os
import re

import pytest

from mimeo.plan import outline

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


#: Запись промпта, которым снят последний замер. Сменили промпт — новый замер,
#: новая запись и новое имя здесь; иначе этот тест падает (заморозка до Ш9).
MEASURED_PROMPT = "2026-09-26-prompt-1.3.md"


def _measured_blocks() -> list[str]:
    """Пять блоков кода из записи замера: общий текст, тезисы «оставить» и
    «доработать», объём «оставить» и «доработать» (с 1.3, `PLAN-10.0`)."""
    path = os.path.join(ROOT, "WORKLOG", MEASURED_PROMPT)
    text = open(path, encoding="utf-8").read()
    return re.findall(r"```\n(.*?)\n```", text, re.S)[:5]


@pytest.mark.parametrize("mode, block", [("keep", 1), ("improve", 2)])
def test_prompt_is_the_measured_one(mode, block):
    """Продукт шлёт ровно тот промпт, которым сняты числа, при рамках замера 10–15."""
    blocks = _measured_blocks()
    expected = (blocks[0].replace("{volume}", blocks[block + 2])
                .replace("{theses}", blocks[block] + "\n")
                .replace("{slides_min}", "10").replace("{slides_max}", "15"))
    assert outline.system_prompt(mode, 10, 15) == expected


def test_builtin_prompt_equals_config(tmp_path):
    common, theses, volume, loaded = outline.load_config()
    assert loaded
    assert (common, theses, volume) == (outline._SYSTEM, outline._THESES, outline._VOLUME)
    fallback = outline.load_config(str(tmp_path / "нет.json"))
    assert fallback == (outline._SYSTEM, outline._THESES, outline._VOLUME, False)


def test_bounds_reach_the_prompt():
    prompt = outline.system_prompt("keep", 3, 7)
    assert "(от 3 до 7)" in prompt and "{" not in prompt


def test_brief_expands_only_when_rewriting():
    """Бриф разворачивается в «доработать» до нижней рамки; в «оставить»
    фразы автора дословны — развернуть их нечем (`OQ-38`, `PLAN-10.0`)."""
    improve, keep = outline.system_prompt("improve", 10, 15), outline.system_prompt("keep", 10, 15)
    assert "разверни его до 10" in improve and "без новых чисел, фактов, названий" in improve
    assert "разверни" not in keep and "слайдов меньше" in keep
    # Потолок — первым и в обоих режимах: «каждой мысли свой слайд» модель
    # читала и на длинном тексте — 16–18 слайдов при потолке 15 (замер 26 сентября).
    assert improve.index("не больше 15") < improve.index("разверни")
    assert "не больше 15" in keep


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        outline.system_prompt("as_is", 10, 15)


def test_kinds_are_the_code_vocabulary():
    """Тип слайда — из словаря kind'ов макетов, а не выдуман рядом (`ADR-0023`, п. 2)."""
    schema = json.load(open(os.path.join(ROOT, "contracts", "pattern-library.schema.json"), encoding="utf-8"))
    enums = [node["enum"] for node in _walk(schema) if isinstance(node, dict) and "cover" in node.get("enum", [])]
    assert enums and set(outline.KINDS) <= set(enums[0])
    variants = outline.RESPONSE_SCHEMA["properties"]["slides"]["items"]["anyOf"]
    kinds = [k for v in variants for k in v["properties"]["kind"]["enum"]]
    assert sorted(kinds) == sorted(outline.KINDS), "каждый тип — ровно в одном варианте"
    # Необязательны ровно таблица и диаграмма (`Z-32`), и каждая — только у
    # своего типа: грамматика сервера не даёт поставить таблицу слайду text.
    for v in variants:
        extra = set(v["properties"]) - set(v["required"])
        assert v["additionalProperties"] is False
        assert extra <= set(outline.OPTIONAL_FIELDS)
        assert all(v["properties"]["kind"]["enum"] == [field] for field in extra)


TEXT = (
    "Сделай, пожалуйста, презентацию про наш движок mimeo. "
    "Точка после латиницы — не часть слова: движок mimeo.\n"
    "Мы вытаскиваем из шаблона дизайн-систему, то есть цвета, шрифты, размеры. "
    "Раскладок в живых шаблонах от 5 до 49 штук. "
    "У них нет контроля качества, у нас же свой приёмочный склад. "
    "Не забудь про картинки: схему стадий — examples/img/stages.png.\n"
    "- январь — 41 200\n"
    "Доля долгих выдач: 4%, 5%.\n"
    "В конце попроси доступ к инференсу."
)


def _slide(heading, kind="text", theses=(), images=()):
    return {"heading": heading, "kind": kind, "role": "прочее", "theses": list(theses),
            "image_idea": "", "images": list(images)}


def _answer(*extra, cover=None, closing=None):
    """Чистый ответ: проходит все проверки в обоих режимах."""
    slides = [
        cover or _slide("Движок mimeo", "cover", ["презентацию про наш движок mimeo"]),
        _slide("Разбор шаблона", "bullets", ["Мы вытаскиваем из шаблона дизайн-систему",
                                             "цвета, шрифты, размеры"]),
        _slide("Раскладки", "metric", ["от 5 до 49 штук"]),
        _slide("Склад", "text", ["свой приёмочный склад"]),
        _slide("Схема стадий", "image_text", ["схема стадий"], ["examples/img/stages.png"]),
        _slide("Январь", "metric", ["январь — 41 200"]),
        _slide("Долгие выдачи", "metric", ["Доля долгих выдач: 4%, 5%"]),
        *extra,
        closing or _slide("Просьба", "closing", ["доступ к инференсу"]),
    ]
    return {"slides": slides, "missing_roles": []}


def _failed(answer, mode="keep", slides_max=15):
    return {c.name for c in outline.check_outline(TEXT, answer, mode, slides_max) if not c.ok}


@pytest.mark.parametrize("mode", ["keep", "improve"])
def test_clean_answer_passes(mode):
    """Сжатие фразы, другой падеж («схема» — «схему»), разряды «41 200» — не отказ."""
    checks = outline.check_outline(TEXT, _answer(), mode, 15)
    assert outline.accepted(checks), [c for c in checks if not c.ok]


@pytest.mark.parametrize("mutate, expected", [
    (lambda a: a["slides"][5]["theses"].__setitem__(0, "январь"), "числа"),
    (lambda a: a["slides"][3]["theses"].append("складов 60"), "числа"),
    (lambda a: a["slides"][4]["images"].clear(), "картинки"),
    (lambda a: a["slides"][3]["images"].append("examples/img/stages.png"), "картинки"),
    (lambda a: a["slides"][3]["theses"].append("см. examples/img/stages.png"), "картинки"),
    (lambda a: a["slides"][3].__setitem__("heading", "Склад на Python"), "латиница"),
    (lambda a: a["slides"][3]["theses"].append("Не забудь про склад"), "обращения"),
    (lambda a: a["slides"].extend(_slide(f"Ещё {i}") for i in range(10)), "объём"),
    (lambda a: a["slides"][3].__setitem__("kind", "timeline"), "типы"),
    (lambda a: a["slides"][3]["theses"].append("склад в Твери"), "названия"),
    (lambda a: a["slides"][2].__setitem__("kind", "agenda") or a["slides"][3].__setitem__("kind", "agenda"), "типы"),
    (lambda a: a["slides"][0].__setitem__("heading", "Революция в презентациях"), "название"),
    (lambda a: a["slides"][-1]["theses"].append("ваш путь к успеху"), "дословность"),
    (lambda a: a["slides"][-1].__setitem__("heading", "Спасибо за внимание"), "клише финала"),
])
def test_each_check_catches_its_violation(mutate, expected):
    answer = _answer()
    mutate(answer)
    assert expected in _failed(answer, slides_max=15), _failed(answer)


@pytest.mark.parametrize("thesis", [
    "Свежесть: свой приёмочный склад",   # подставлено подлежащее
    "январь 4%",                          # склеены два предложения
    "склад для приёмки свой",             # переставлен порядок слов
])
def test_keep_refuses_retelling(thesis):
    """В «оставить» пересказ — отказ; в «доработать» тот же тезис законен."""
    answer = _answer()
    answer["slides"][3]["theses"] = [thesis]
    assert _failed(answer, "keep") == {"дословность"}
    assert _failed(answer, "improve") == set()


def test_extra_agenda_goes_first_when_over_the_ceiling():
    """Колода на слайд длиннее потолка — снимается оглавление: его пункты
    собирает код, текста автора там нет (замер 26 сентября: 16 при 15)."""
    answer = _answer(_slide("Содержание", "agenda", ["что-то"]))
    over = len(answer["slides"])
    trimmed, note = outline.without_extra_agenda(answer, over - 1)
    assert len(trimmed["slides"]) == over - 1 and "оглавление снято" in note
    assert not any(s["kind"] == "agenda" for s in trimmed["slides"])
    same, quiet = outline.without_extra_agenda(answer, over)
    assert same is answer and quiet == "", "в потолке — ничего не снимается"
    two_over, _ = outline.without_extra_agenda(answer, over - 2)
    assert len(two_over["slides"]) == over - 1, "снимается только оглавление, остальное — дело «объёма»"


def test_agenda_theses_are_not_on_slides():
    """Пункты оглавления соберёт код: число, стоящее только там, потеряно."""
    answer = _answer(_slide("Содержание", "agenda", ["от 5 до 49 штук"]))
    answer["slides"][2]["theses"] = ["раскладок много"]
    assert "числа" in _failed(answer)


def test_numbers_are_compared_whole():
    """«41 200» — одно число: «41» и «200» не выдают его за найденное."""
    answer = _answer()
    answer["slides"][5]["theses"] = ["январь 41"]
    assert {"числа", "дословность"} <= _failed(answer)
    assert outline.same_stem("движка", "движок") and outline.same_stem("схема", "схему")
    assert not outline.same_stem("12500", "12501") and not outline.same_stem("путь", "пустой")


def test_number_written_as_digit_is_not_invention():
    """«из четырёх переведены три» → «3 из 4»: цифрой вместо слова, не выдумка;
    «5 из 4» — выдумка. И обратно: цифра автора, записанная словом, не потеряна."""
    text = "Из четырёх площадок переведены три. Заказов 7 за день."
    def deck(*theses):
        return {"slides": [_slide("Площадки", "metric", list(theses))], "missing_roles": []}
    assert outline.accepted(outline.check_outline(text, deck("Переведено 3 из 4", "семь заказов"), "improve", 15))
    failed = {c.name for c in outline.check_outline(text, deck("Переведено 5 из 4", "7 заказов"), "improve", 15) if not c.ok}
    assert failed == {"числа"}


def test_function_words_are_not_invention():
    """Предлог, которого нет в тексте, — не выдумка: так замер Ш1б назвал «для»
    в «Презентация для технического заказчика», и это была ошибка проверки."""
    answer = _answer(cover=_slide("Движок для презентаций", "cover", ["презентацию про наш движок mimeo"]))
    assert "название" not in _failed(answer, "improve")


def test_improve_may_reword_cover_and_closing():
    """«Данные за полугодие…» из «с цифрами… за полугодие» — законная
    переформулировка «доработать», не отказ (замер Ш1б); «Спасибо…» — отказ."""
    answer = _answer(closing=_slide("Просьба", "closing", ["Ожидаем доступ к инференсу"]))
    answer["slides"][0]["theses"] = ["Данные о движке mimeo"]
    assert _failed(answer, "improve") == set()
    answer["slides"][-1]["theses"] = ["Спасибо за внимание"]
    assert _failed(answer, "improve") == {"клише финала"}


@pytest.mark.parametrize("field", ["heading", "kind", "theses", "images"])
def test_broken_form_is_one_refusal(field):
    """Схема держит форму и в режиме со схемой проверяется всё равно (`ADR-0023`)."""
    answer = _answer()
    del answer["slides"][3][field]
    assert _failed(answer) == {"форма"}


def _walk(node):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)
