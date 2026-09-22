"""Экран «что вынули из вашего шаблона» (`Z-29`, `PLAN-8.1`, часть A).

Веб тут **ничего не считает** — он читает `design-system.json`, который уже
написал `analyze`, и сворачивает его до показываемого. Значит проверять надо
не арифметику, а **честность сокращения**: не выдумать там, где данных нет, и
не пообещать того, чего не показываем.

Оба дефекта, ради которых эти тесты написаны, нашлись **проверкой глазами**, а
не падением: страница подписывала «показаны 12 цветов из 24», не показывая ни
одного, и выводила в примерах знаки пиктограммного шрифта — на экране
квадратики.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "web"))

import serve                                                   # noqa: E402


# --- пиктограммы не выдаются за текст ----------------------------------


@pytest.mark.parametrize("text, pictogram", [
    ("", True),                       # один знак частной области
    ("  ", True),         # шрифт linecons у `business_plan`
    ("  x", True),              # больше половины — всё ещё мусор
    ("Business Plan", False),
    ("91%", False),
    ("VK ", False),                   # одна иконка в тексте — текст
    ("", False),                            # пусто это не пиктограммы
    ("   ", False),
])
def test_pictogram_runs_are_recognised(text: str, pictogram: bool) -> None:
    """Область частного использования Unicode (U+E000–U+F8FF) сама по себе
    ничего не значит: знаки рисует конкретная гарнитура, и в браузере на месте
    «примера из вашего шаблона» будут квадратики."""
    assert serve._is_pictogram(text) is pictogram


# --- единицы и гарнитуры ------------------------------------------------


def test_emu_converts_and_keeps_the_difference_between_zero_and_unknown() -> None:
    """`0` — это «поле нулевое», `None` — «не задано». Подменять второе первым
    значит выдумывать за шаблон."""
    assert serve._inch(914400) == 1.0
    assert serve._inch(0) == 0.0
    assert serve._inch(None) is None


def test_font_falls_back_and_then_admits_it_does_not_know() -> None:
    """В OOXML гарнитуры две — латинская и кириллическая, и в живых шаблонах
    сплошь заполнена одна. Обе пусты — гарнитура наследуется от темы."""
    assert serve._font_of({"latin": "Play", "cyrl": None}) == "Play"
    assert serve._font_of({"latin": None, "cyrl": "Arial"}) == "Arial"
    assert serve._font_of({"latin": None, "cyrl": None}) is None
    assert serve._font_of({}) is None


# --- свёртка ничего не выдумывает ---------------------------------------


def test_an_empty_template_yields_emptiness_and_not_invented_values() -> None:
    """Дыра, которую однажды уже ловила логическая проверка: `.potx` без
    слайдов. Доноров нет, раскладки берутся из макетов (`ADR-0006`), и ноль
    здесь — законный ответ, а не повод подставить что-нибудь."""
    out = serve._summarise({}, None, "пустой.potx")
    assert out["ok"] is True
    assert out["patterns"] == 0
    assert out["type_scale"] == []
    assert out["palette"]["core"] == []
    assert out["slide"]["aspect"] is None
    assert out["slide"]["width_in"] is None
    assert out["source"]["slides"] is None


def test_the_summary_does_not_promise_colours_it_will_not_show() -> None:
    """Страница показывает ядро палитры и роли темы; списка наблюдаемых цветов
    она не рисует. Значит и отдавать его нельзя: отданное и непоказанное
    порождает подпись «показаны 12 из 24» под пустым местом — так и вышло при
    первой проверке глазами."""
    system = {"palette": {"observed": [{"hex": "#000000", "count": 5}] * 24,
                          "core": ["#000000"], "theme": []}}
    out = serve._summarise(system, None, "t.pptx")
    assert out["palette"]["observed_total"] == 24, "число найденных цветов нужно"
    assert "observed" not in out["palette"], (
        "список наблюдаемых цветов страница не рисует — отдавать его нельзя"
    )


# --- на живом шаблоне ---------------------------------------------------


SAMPLE = os.path.join(ROOT, "samples", "business_plan.pptx")


@pytest.mark.skipif(
    not os.path.exists(SAMPLE), reason="шаблоны не коммитятся: tools/fetch_samples.py"
)
def test_summary_matches_what_analyze_actually_wrote(tmp_path) -> None:
    """Сквозная сверка: свёртка не расходится с артефактом движка.

    Числа не зашиты — они берутся из того же файла, что читает веб. Иначе тест
    проверял бы мою память о шаблоне, а не работу кода.
    """
    import subprocess
    out = tmp_path / "ds"
    subprocess.run(
        [sys.executable, "-m", "mimeo", "analyze", SAMPLE, "-o", str(out), "-q"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=300,
    )
    with open(out / "design-system.json", encoding="utf-8") as fh:
        system = json.load(fh)
    with open(out / "patterns.json", encoding="utf-8") as fh:
        patterns = json.load(fh)

    summary = serve._summarise(system, patterns, "business_plan.pptx")

    assert summary["source"]["slides"] == system["source"]["slides"]
    assert summary["patterns"] == len(patterns["patterns"])
    assert len(summary["type_scale"]) == len(system["type_scale"])
    assert summary["palette"]["core"] == system["palette"]["core"]
    assert summary["slide"]["aspect"] == system["slide"]["aspect"]

    # У этого шаблона есть роли на пиктограммном шрифте — на них и проверяем,
    # что «примера нет» и «пример — мусор» различаются.
    marked = [t for t in summary["type_scale"] if t["pictogram"]]
    assert marked, "у business_plan роли на linecons есть, признак обязан сработать"
    assert all(t["example"] is None for t in marked)


# --- превью слайдов (`PLAN-8.2`, часть B) -------------------------------


def test_powerpoint_is_guarded_as_an_exclusive_resource() -> None:
    """Приложение одноэкземплярное, и это не наше ограничение.

    `New-Object` подключается к уже открытому у пользователя экземпляру, а
    `Quit()` закрыл бы его документы. Поэтому оба зонда сторожат вход и
    выходят с кодом 3. Замер столкновением 22 сентября: два растровых прогона
    с разницей 1.2 с дают первый 4.5 с и код 0, второй **0.3 с и код 3**.

    Здесь проверяется, что сервер об этом знает: есть замок и он один.
    """
    import threading
    assert isinstance(serve._POWERPOINT, type(threading.Lock())), (
        "растровые операции обязаны быть под замком: превью и проверка вёрстки "
        "делят один PowerPoint"
    )


def test_preview_addresses_carry_numbers_and_never_paths() -> None:
    """Путь от страницы не принимается — ни целиком, ни частью. Снаружи ходят
    токен и два номера, путь живёт на сервере (как и у скачивания колоды)."""
    run = {"token": "abc/def", "dir": "/tmp/x", "decks": ["a.pptx"], "previews": {}}
    urls = serve.Handler._image_urls(None, run, 0, 3)
    assert len(urls) == 3
    for i, url in enumerate(urls):
        assert url == f"/api/preview-image?token=abc%2Fdef&n=0&i={i}"
        assert ".." not in url and "/tmp" not in url


def test_a_deck_number_from_outside_is_checked_against_what_exists() -> None:
    """Номер колоды приходит снаружи. Всё, что не цифра или за пределом
    списка, — `None`, и дальше по этому пути ничего не отдаётся."""
    run = {"decks": ["a.pptx", "b.pptx"]}
    take = serve.Handler._deck_index
    assert take(None, {"n": ["0"]}, run) == 0
    assert take(None, {"n": ["1"]}, run) == 1
    assert take(None, {"n": ["2"]}, run) is None          # за пределом
    assert take(None, {"n": ["-1"]}, run) is None         # не цифра
    assert take(None, {"n": ["../../etc"]}, run) is None  # путь
    assert take(None, {}, run) == 0                       # умолчание


def test_preview_width_is_generous_because_time_does_not_depend_on_it() -> None:
    """Замер: 13 слайдов дают 4.3 с при ширине 400, 3.5 с при 800 и 5.1 с при
    1280 — разброс это шум запуска приложения, а не разрешение. Значит мельчить
    незачем: экономия была бы только на памяти, а картинку открывают целиком."""
    assert serve.PREVIEW_WIDTH >= 800
