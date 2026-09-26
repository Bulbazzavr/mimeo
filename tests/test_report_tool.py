"""`tools/report.py verify` мерит колоды модели (`PLAN-9.0`, Ш9).

Числа в документах берутся стадиями `report.py`, а не разовыми скриптами
(`CLAUDE.md`). До Ш9 `verify` модели не знал: сдаточные колоды с моделью мерил
только разовый скрипт растра. Здесь — то, что держит новое без PowerPoint:
путь модели тем же вызовом, что у сборки (`cli.model_path`), свой каталог
ответов, отказ модели — код 3 до всякой вёрстки, цель объёма доходит до плана,
картинки считаются по байтам в файле. Проверку вёрстки подменяет заглушка:
сама она мерится настоящим PowerPoint, а здесь проверяется, **что** ей дают.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import types
import zipfile
from dataclasses import replace

import pytest

from mimeo.analyze import analyze_template
from mimeo.plan import load_content, outline, plan_deck
from mimeo.plan.client import Access, load_config
from tests.fake_model import FakeModel
from tests.test_model_path import TEXT, _answer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PNG = os.path.join(ROOT, "examples", "img", "pipeline-stages.png")
PNG2 = os.path.join(ROOT, "examples", "img", "overflow-16-to-1.png")


@pytest.fixture()
def report(monkeypatch):
    """Модуль инструмента; `TEMPLATES` — глобальная, возвращается после теста."""
    spec = importlib.util.spec_from_file_location(
        "report_tool", os.path.join(ROOT, "tools", "report.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "TEMPLATES", mod.TEMPLATES)
    return mod


@pytest.fixture()
def prose(tmp_path) -> str:
    """Проза с картинкой рядом, как у `test_model_path`; абсолютный путь."""
    folder = tmp_path / "text"
    (folder / "img").mkdir(parents=True)
    shutil.copy(PNG, folder / "img" / "stages.png")
    path = folder / "text.md"
    path.write_text(TEXT, encoding="utf-8")
    return str(path)


@pytest.fixture()
def answers(prose, tmp_path) -> str:
    """Каталог с ответами модели на `prose` в обоих режимах — ключ по
    **настоящему** конфигу (адрес сервера в ключ не входит, `ADR-0021`), как у
    ответов замера. Оба режима: сверка с вызовом сборки на одном только
    «оставить» сравнивала бы в «доработать» два промаха — это нашла мутация."""
    folder = str(tmp_path / "answers")
    with FakeModel(answer=_answer()) as fake:
        config = load_config()
        config = replace(config, access=Access.ON, cache_root=folder,
                         endpoint=replace(config.endpoint, base_url=fake.base_url))
        for mode in outline.MODES:
            got = outline.run(prose, load_content(prose), text_mode=mode, config=config)
            assert got.status == "accepted", got.line
    return folder


@pytest.fixture()
def templates(tmp_path, multi_template_path) -> str:
    folder = tmp_path / "templates"
    folder.mkdir()
    shutil.copy(multi_template_path, folder / "multi.pptx")
    return str(folder)


@pytest.fixture()
def measured(monkeypatch):
    """Заглушка проверки вёрстки: что ей дали — план и файл каждой колоды."""
    seen: list = []

    def fake_verify(template, plan, patterns, target, built, rounds=3):
        seen.append((plan, target))
        rep = types.SimpleNamespace(before=0, after=0, overflow_width=(), donor_overflow=(),
                                    occluded=(), stopped="fits", seconds=0.0)
        return types.SimpleNamespace(report=rep, plan=plan)

    monkeypatch.setattr("mimeo.verify.verify_deck", fake_verify)
    return seen


def _record(tmp_path) -> dict:
    with open(tmp_path / "out" / "verify" / "outline.json", encoding="utf-8") as fh:
        return json.load(fh)


# --- путь модели --------------------------------------------------------------


def test_default_measures_the_path_without_the_model(report, prose, templates, measured,
                                                     tmp_path, monkeypatch, capsys):
    """Умолчание — off, а не умолчание сборки (cache): после Ш8 голая команда
    из документов иначе молча стала бы мерить колоды модели."""
    monkeypatch.chdir(tmp_path)
    assert report.main(["verify", "--content", prose, "--templates", templates]) == 0
    assert _record(tmp_path)["status"] == "off"
    assert measured and all(plan.planner != "mixed" for plan, _ in measured)
    assert "модель выключена" in capsys.readouterr().out


def test_answers_come_from_the_given_folder(report, prose, answers, templates, measured,
                                            tmp_path, monkeypatch, capsys):
    """`--llm-cache` — ответы замера не из `cache/llm/`; сервера нет вовсе, и
    мерится колода модели, а не путь без неё."""
    monkeypatch.chdir(tmp_path)
    code = report.main(["verify", "--content", prose, "--templates", templates,
                        "--llm", "cache", "--llm-cache", answers])
    assert code == 0
    record = _record(tmp_path)
    assert (record["status"], record["answer_source"]) == ("accepted", "cache")
    assert measured and all(plan.planner == "mixed" for plan, _ in measured)
    out = capsys.readouterr().out
    assert "колоду построила модель" in out
    where = os.path.relpath(answers).replace(os.sep, "/")
    # Точной фразой шапки: само слово «answers» есть и во временном пути pytest
    # (имя теста), и проверка словом проходила без шапки — это нашла мутация.
    assert f"ответы — `{where}`" in out, "шапка называет каталог ответов"


def test_model_path_is_the_build_call(report, prose, answers):
    """Тот же вызов, что у сборки: `cli.model_path` с переданным конфигом, а не
    копия — исход и документ совпадают с путём `build`."""
    from mimeo import cli

    doc = load_content(prose)
    got, _, _ = report.model_doc(prose, doc, None, "cache", "improve", answers,
                                 out_dir=os.path.join(os.path.dirname(answers), "a"))
    args = types.SimpleNamespace(content=prose, llm="cache", text="improve",
                                 out=os.path.join(os.path.dirname(answers), "b"))
    same, _ = cli.model_path(args, doc, None, replace(load_config(), cache_root=answers))
    assert got.status == "accepted", "сравнивать два промаха кэша бессмысленно"
    assert "текст improve" in got.line, "режим дошёл до вызова"
    assert (got.status, got.line) == (same.status, same.line)
    assert got.doc == same.doc


def test_refusal_stops_before_any_layout(report, prose, templates, measured, tmp_path,
                                         monkeypatch, capsys):
    """Модель просили, а колоды нет — код 3 и ни одной колоды: путь без модели
    под именем модели не мерим (его меряет `--llm off`)."""
    monkeypatch.chdir(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    code = report.main(["verify", "--content", prose, "--templates", templates,
                        "--llm", "cache", "--llm-cache", str(empty)])
    assert code == 3
    assert not measured
    assert not (tmp_path / "out" / "verify" / "multi-1.pptx").exists()
    assert "мерить нечего" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    ["slots", "--llm", "cache"],
    ["plan", "--llm", "on", "--text", "keep"],
    ["verify", "--text", "keep"],
    ["verify", "--llm-cache", "x"],
    ["verify", "--llm", "off", "--text", "improve"],
])
def test_model_flags_that_would_measure_nothing_are_refused(report, argv, tmp_path):
    """Флаги, которые обещают модель, а замер шёл бы без неё, — ошибка разбора.
    Каталог шаблонов пуст: пропусти разбор такие флаги, замер упал бы на нём
    другим кодом — а не поднял бы PowerPoint посреди тестов."""
    with pytest.raises(SystemExit) as stop:
        report.main(argv + ["--templates", str(tmp_path)])
    assert stop.value.code == 2


# --- цель объёма --------------------------------------------------------------

#: Заметка об объёме, которую пишет **план** при заданной цели. У разбора текста
#: с целью своя — «Целевой объём не достигнут: контента хватает…» — и она
#: доезжает до предупреждений плана и без цели: проверка по одному «Целевой
#: объём» проходила при потерянной цели, это нашла мутация.
PLAN_VOLUME = "Целевой объём 10–15 слайдов"


@pytest.mark.parametrize("variants", [1, 3])
def test_volume_target_reaches_the_plan(report, multi_template_path, variants):
    """Как у `cmd_build` и `_build_variants`: до Ш9 цель здесь терялась, и колода с
    недобором мерилась бы без добора и без слов о нём."""
    a = analyze_template(multi_template_path)
    doc = load_content(os.path.join(ROOT, "examples", "content-short.md"), target=(10, 15))
    plans = report._variant_plans(doc, a, variants, (10, 15))
    assert plans
    for plan in plans:
        assert any(w.startswith(PLAN_VOLUME) for w in plan.warnings), plan.warnings
    if variants == 1:
        same = plan_deck(doc, a.patterns, a.design_system.source.sha256, target=(10, 15))
        assert plans[0].slides == same.slides


def test_measured_plans_carry_the_slides_flag(report, prose, templates, measured, tmp_path,
                                              monkeypatch):
    """`--slides` доходит от командной строки до плана, который мерится."""
    monkeypatch.chdir(tmp_path)
    assert report.main(["verify", "--content", prose, "--templates", templates,
                        "--slides", "10-15"]) == 0
    assert measured
    for plan, _ in measured:
        assert any(w.startswith(PLAN_VOLUME) for w in plan.warnings), plan.warnings


# --- картинки -----------------------------------------------------------------


def _png_hash(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _deck(tmp_path, media: dict[str, str]) -> str:
    """Пакет с частями `ppt/media/`: имя части → файл, чьи байты в ней лежат."""
    target = tmp_path / "deck.pptx"
    with zipfile.ZipFile(target, "w") as z:
        z.writestr("ppt/presentation.xml", "<p/>")
        for name, source in media.items():
            with open(source, "rb") as fh:
                z.writestr(f"ppt/media/{name}", fh.read())
    return str(target)


def test_pictures_are_counted_by_bytes_in_the_file(report, tmp_path):
    """Результат, а не намерение (`Z-28a`): своя картинка шаблона не считается,
    картинка автора — считается, где бы ни лежала и как бы ни называлась."""
    author = {_png_hash(PNG)}
    assert report._embedded(_deck(tmp_path, {"image1.png": PNG2, "mimeo1.png": PNG}), author) == 1
    assert report._embedded(_deck(tmp_path, {"image1.png": PNG2}), author) == 0
    assert report._embedded(_deck(tmp_path, {"image1.png": PNG2}), set()) == 0


def test_author_pictures_are_the_files_of_image_blocks(report, prose):
    """Картинка автора — файл блока `image` документа; путь без файла на диске
    блоком не становится, и в счёт не идёт."""
    doc, notes, _ = outline.to_doc(_answer(), TEXT, prose, "t")
    assert report._author_images(doc) == {_png_hash(PNG)}
    empty = load_content(os.path.join(ROOT, "examples", "content-short.md"))
    assert report._author_images(empty) == set()


def test_markup_picture_without_a_file_is_not_counted(report):
    """Разметка `![](…)` даёт блок `image` и без файла на диске — такой блок в
    счёт картинок автора не идёт и счёт не роняет."""
    from mimeo.plan.content import parse_markdown

    doc = parse_markdown("# Тема\n\n![схема](нет-такого-файла.png)\n\nТекст.", "t")
    assert [b.kind for s in doc.sections for b in s.blocks][0] == "image"
    assert report._author_images(doc) == set()


def test_only_image_blocks_are_author_pictures(report):
    """Картинка автора — файл блока `image`. Путь у блока другого вида (сегодня
    `ref` бывает только у картинок, но поле общее) в счёт не идёт: данные
    таблицы или диаграммы файлом картинкой не станут."""
    from mimeo.plan.content import ContentBlock, ContentDoc, ContentSection

    table = ContentBlock(id="b1", kind="table", ref=PNG)
    picture = ContentBlock(id="b2", kind="image", ref=PNG2)
    doc = ContentDoc(name="t", title=None, sections=[ContentSection(
        id="s1", heading="Тема", blocks=(table, picture))])
    assert report._author_images(doc) == {_png_hash(PNG2)}
