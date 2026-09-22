"""Машинночитаемый итог сборки (`Z-46`, `PLAN-8.0`).

Контракт объявлен в `SPEC-WEB`, разделе 4, **до реализации** — как обещание
второму участнику. Исполнитель с тех пор сменился, обещание нет: форма не
меняется, и здесь она заперта схемой.

**Главное, что проверяется, — не форма, а одно поле.** `render_report` равен
`null`, когда стадия VERIFY не запускалась, и путём, когда запускалась. «Не
проверяли» и «дефектов нет» — разные вещи, и это правило в проекте оплачено
дороже всего: однажды измеритель отказал, вернул пустоту, а мы прочли её как
«чисто». В вебе оно ломается легче всего, достаточно показать ноль.
"""

from __future__ import annotations

import json
import os

import pytest

from mimeo.cli import build_parser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT = os.path.join(ROOT, "examples", "content-mimeo.md")
SCHEMA = os.path.join(ROOT, "contracts", "build-report.schema.json")
SAMPLE = os.path.join(ROOT, "samples", "business_plan.pptx")


def _run(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


def _report(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _validate(payload: dict) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    with open(SCHEMA, encoding="utf-8") as fh:
        jsonschema.validate(payload, json.load(fh))


needs_sample = pytest.mark.skipif(
    not os.path.exists(SAMPLE), reason="шаблоны не коммитятся: tools/fetch_samples.py"
)


@needs_sample
def test_single_build_writes_a_report_matching_the_schema(tmp_path):
    out = tmp_path / "out"
    target = out / "report.json"
    _run(["build", SAMPLE, CONTENT, "-o", str(out), "--output", str(out / "deck.pptx"),
          "--report", str(target), "-q"])
    payload = _report(str(target))
    _validate(payload)
    assert payload["version"] == "1.0"
    assert len(payload["decks"]) == 1, "без --variants колода одна"
    deck = payload["decks"][0]
    assert deck["slides"] > 0
    assert os.path.exists(deck["path"]), "отчёт называет путь, которого нет"
    assert payload["seconds"] > 0


@needs_sample
def test_report_says_null_when_layout_was_not_checked(tmp_path):
    """`null` значит «не проверяли». Ноль дефектов значил бы «проверено и
    чисто», а это другое утверждение, и подменять одно другим нельзя."""
    out = tmp_path / "out"
    target = out / "report.json"
    _run(["build", SAMPLE, CONTENT, "-o", str(out), "--output", str(out / "deck.pptx"),
          "--report", str(target), "-q"])
    deck = _report(str(target))["decks"][0]
    assert deck["render_report"] is None, (
        "без --verify отчёт вёрстки не существует, и поле обязано быть null"
    )


@needs_sample
def test_variants_are_numbered_and_single_deck_is_not(tmp_path):
    """`variant` = null и `variant` = 1 — разные вещи: «единственная колода» и
    «первая из трёх». Веб показывает их по-разному, и различие держится здесь."""
    out = tmp_path / "out"
    single, many = out / "one.json", out / "many.json"
    _run(["build", SAMPLE, CONTENT, "-o", str(out), "--output", str(out / "a.pptx"),
          "--report", str(single), "-q"])
    _run(["build", SAMPLE, CONTENT, "-o", str(out), "--output", str(out / "b.pptx"),
          "--variants", "3", "--report", str(many), "-q"])

    assert _report(str(single))["decks"][0]["variant"] is None

    payload = _report(str(many))
    _validate(payload)
    decks = payload["decks"]
    assert len(decks) >= 2, "на этом шаблоне вариантов выходит несколько"
    assert [d["variant"] for d in decks] == list(range(1, len(decks) + 1))
    assert len({d["path"] for d in decks}) == len(decks), "варианты пишут в один файл"
    for deck in decks:
        assert os.path.exists(deck["path"])


@needs_sample
def test_paths_in_the_report_use_forward_slashes(tmp_path):
    """Отчёт читает машина. `os.path.join` на Windows смешивает разделители в
    одной строке, и потребителю приходится гадать."""
    out = tmp_path / "out"
    target = out / "report.json"
    _run(["build", SAMPLE, CONTENT, "-o", str(out), "--output", str(out / "deck.pptx"),
          "--report", str(target), "-q"])
    for deck in _report(str(target))["decks"]:
        assert "\\" not in deck["path"], deck["path"]


@needs_sample
def test_diagnostics_carry_what_the_engine_says_out_loud(tmp_path):
    """Предупреждения плана — это то, что движок говорит человеку словами.
    Веб обязан их показать, а значит они должны в отчёт попасть."""
    out = tmp_path / "out"
    target = out / "report.json"
    _run(["build", SAMPLE, CONTENT, "-o", str(out), "--output", str(out / "deck.pptx"),
          "--report", str(target), "-q"])
    diagnostics = _report(str(target))["diagnostics"]
    assert diagnostics["warnings"], "на этом входе движку есть что сказать"
    assert all(isinstance(w, str) for w in diagnostics["warnings"])
    assert diagnostics["unplaced"] == [], "содержание на этом шаблоне не теряется"


@needs_sample
def test_without_the_flag_nothing_is_written(tmp_path):
    """Флаг добавлен, поведение по умолчанию не изменилось: девять сдаточных
    колод собираются этой же командой."""
    out = tmp_path / "out"
    _run(["build", SAMPLE, CONTENT, "-o", str(out), "--output", str(out / "deck.pptx"), "-q"])
    assert not list(out.glob("*.json")), "без --report отчёт писаться не должен"


@needs_sample
def test_a_shortfall_of_variants_reaches_the_report(tmp_path):
    """Попросили больше вариантов, чем вышло, — причина обязана быть в отчёте.

    Движок называет её человеку с 19 сентября (`Z-26`), но в отчёт она не
    попадала, и потребитель — веб — показал бы пять колод вместо девяти
    **молча**. Это «молчаливый ноль» из `Z-20`: формально правдиво и вводит в
    заблуждение. Найдено 22 сентября, когда у веба появилось поле «сколько
    вариантов»: до того никто не просил больше трёх.
    """
    out = tmp_path / "out"
    target = out / "report.json"
    asked = 27          # столько политик ранга в config/variants.json
    _run(["build", SAMPLE, CONTENT, "-o", str(out), "--output", str(out / "deck.pptx"),
          "--variants", str(asked), "--slides", "10-15", "--report", str(target), "-q"])
    payload = _report(str(target))
    _validate(payload)

    decks = payload["decks"]
    assert len(decks) < asked, (
        "на этом шаблоне столько заметно разных колод не выходит — "
        "без недобора проверять нечего"
    )
    said = [w for w in payload["diagnostics"]["warnings"]
            if w.startswith("Запрошено вариантов")]
    assert said, "недобор вариантов промолчал в отчёте"
    assert str(asked) in said[0] and str(len(decks)) in said[0], (
        f"причина не называет оба числа: {said[0]}"
    )
