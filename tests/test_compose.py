"""Сборка колоды. ADR-0011, ADR-0012.

Открыть результат в PowerPoint мы не можем, поэтому вместо одной невозможной
проверки здесь три независимые: структурная (наш валидатор), круговая (наш же
анализатор читает наш файл) и внешняя (`python-pptx`, написанный не нами).
"""

from __future__ import annotations

import os
import zipfile

import pytest

from mimeo.analyze import analyze_template
from mimeo.compose import build, inspect
from mimeo.compose.package import FIXED_TIME
from mimeo.plan import parse_markdown, plan_deck

CONTENT = """# Сервис прогнозирования

Вводный абзац о том, зачем нужен сервис и какую боль он снимает на практике.

## Что умеет система

- Прогнозирует деградацию датчиков
- Оценивает риск задымления
- Выявляет аномальный доступ

## Результаты

0.82

Точность прогноза

## Спасибо
"""


def _make(template: str, out_dir) -> tuple:
    analysis = analyze_template(template)
    doc = parse_markdown(CONTENT, name="test-content")
    plan = plan_deck(doc, analysis.patterns, analysis.design_system.source.sha256)
    os.makedirs(out_dir, exist_ok=True)
    path = str(out_dir / "deck.pptx")
    report = build(template, plan, analysis.patterns, path)
    return analysis, plan, report, path


@pytest.fixture(scope="module")
def built(multi_template_path, tmp_path_factory):
    return _make(multi_template_path, tmp_path_factory.mktemp("build"))


# --- три независимые проверки ------------------------------------------


def test_package_is_structurally_valid(built):
    """Наш валидатор: связи разрешаются, типы содержимого на месте, XML цел."""
    *_, path = built
    assert inspect(path) == []


def test_our_own_analyzer_reads_it_back(built):
    """Круговая проверка: если файл читается нашим разбором, он самосогласован."""
    analysis, plan, report, path = built
    again = analyze_template(path)
    assert again.design_system.source.slides == len(plan.slides)
    assert again.design_system.slide.cx_emu == analysis.design_system.slide.cx_emu


def test_an_independent_library_opens_it(built):
    """python-pptx написан не нами и не разделяет наших заблуждений."""
    pptx = pytest.importorskip("pptx")
    *_, path = built
    presentation = pptx.Presentation(path)
    assert len(presentation.slides) > 0


# --- содержимое --------------------------------------------------------


def _all_text(slide) -> str:
    """Текст слайда с раскрытием групп: карточки лежат внутри групп."""
    chunks = []

    def walk(shapes):
        for shape in shapes:
            if shape.shape_type is not None and str(shape.shape_type).startswith("GROUP"):
                walk(shape.shapes)
            elif shape.has_text_frame:
                chunks.append(shape.text_frame.text)

    walk(slide.shapes)
    return "\n".join(chunks)


def test_our_text_is_actually_in_the_file(built):
    pptx = pytest.importorskip("pptx")
    *_, path = built
    text = "\n".join(_all_text(s) for s in pptx.Presentation(path).slides)
    assert "Сервис прогнозирования" in text
    assert "Что умеет система" in text
    assert "Прогнозирует деградацию датчиков" in text


def test_list_items_all_survive(built):
    pptx = pytest.importorskip("pptx")
    *_, path = built
    text = "\n".join(_all_text(s) for s in pptx.Presentation(path).slides)
    for item in ("Прогнозирует деградацию", "Оценивает риск", "Выявляет аномальный"):
        assert item in text, item


def test_donor_formatting_is_kept(built):
    """Текст наш, оформление их: свойства прогона донора не трогаются."""
    pptx = pytest.importorskip("pptx")
    *_, path = built
    sized = 0
    for slide in pptx.Presentation(path).slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    if run.font.size is not None or run.font.name is not None:
                        sized += 1
    assert sized > 0, "ни у одного прогона не осталось свойств донора"


def test_inherited_shapes_are_reported(built):
    """Унаследованное — не побочный эффект, а суть подхода, и его считают."""
    *_, report, _ = built
    assert report.inherited_shapes > 0
    assert report.substituted > 0


# --- пакет -------------------------------------------------------------


def test_original_slides_are_gone(built):
    """Колода состоит из запланированных слайдов, а не из шаблонных."""
    *_, plan, _, path = built
    with zipfile.ZipFile(path) as zf:
        slides = [n for n in zf.namelist() if n.startswith("ppt/slides/slide")]
    assert len(slides) == len(plan.slides)


def test_masters_and_theme_survive(built):
    *_, path = built
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    assert any(n.startswith("ppt/slideMasters/") for n in names)
    assert any(n.startswith("ppt/theme/") for n in names)


def test_no_dangling_notes_relationship(built):
    """notesSlide ссылается обратно на свой слайд, которого больше нет."""
    *_, path = built
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name.startswith("ppt/slides/_rels/"):
                assert b"notesSlide" not in zf.read(name)


def test_build_is_reproducible(multi_template_path, tmp_path):
    """Одинаковый план даёт байт в байт одинаковый файл. ADR-0003."""
    first = _make(multi_template_path, tmp_path / "a")[3]
    second = _make(multi_template_path, tmp_path / "b")[3]
    assert open(first, "rb").read() == open(second, "rb").read()


def test_timestamps_are_fixed(built):
    """Без фиксированной метки времени воспроизводимость была бы фикцией."""
    *_, path = built
    with zipfile.ZipFile(path) as zf:
        assert {i.date_time for i in zf.infolist()} == {FIXED_TIME}


# --- деградация --------------------------------------------------------


def test_layout_sourced_patterns_also_build(template_path, tmp_path):
    """Шаблон из двух слайдов: паттерны из макетов (ADR-0006), тот же путь."""
    analysis, plan, report, path = _make(template_path, tmp_path)
    assert all(p.source == "layouts" for p in analysis.patterns.patterns)
    assert report.slides > 0
    assert inspect(path) == []


def test_mismatched_artifacts_are_reported(multi_template_path, tmp_path):
    """План от другого разбора — повод предупредить, а не молча собрать не то."""
    import dataclasses

    analysis = analyze_template(multi_template_path)
    doc = parse_markdown(CONTENT)
    plan = plan_deck(doc, analysis.patterns, analysis.design_system.source.sha256)
    broken = dataclasses.replace(
        plan, source=dataclasses.replace(plan.source, patterns_sha256="0" * 64)
    )
    report = build(multi_template_path, broken, analysis.patterns, str(tmp_path / "x.pptx"))
    assert any("хеши не совпали" in w for w in report.warnings)


def test_inspect_catches_a_broken_package(tmp_path):
    """Валидатор должен ловить то, ради чего написан."""
    path = str(tmp_path / "broken.pptx")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("hello.txt", "не пакет вовсе")
    assert inspect(path)


def test_inspect_rejects_a_non_zip(tmp_path):
    path = str(tmp_path / "plain.pptx")
    open(path, "w", encoding="utf-8").write("это просто текст")
    assert inspect(path)


# --- экранирование значений в .rels и [Content_Types].xml ---------------
#
# Найдено 15 сентября на выданном шаблоне VK Education (`Z-06`): в гиперссылке
# на Figma стоял `&amp;`, разбор превращал его в `&`, а запись возвращала голым —
# и пакет становился битым. На одиннадцати наших образцах такой ссылки не было.


def test_ampersand_in_hyperlink_survives_the_round_trip():
    """`&` в ссылке — не редкость, а норма для любого URL с параметрами."""
    import xml.etree.ElementTree as ET

    from mimeo.compose.package import Rel, Relationships

    url = "https://www.figma.com/file/abc?type=design&node-id=94%3A6307&mode=design"
    raw = Relationships([
        Rel(id="rId1", type="http://x/hyperlink", target=url, external=True)
    ]).to_bytes()

    ET.fromstring(raw)  # падение здесь и означало битый пакет
    back = Relationships.parse(raw)
    assert back.items[0].target == url, "ссылка должна пережить запись и чтение"
    assert b"&amp;" in raw and raw.count(b"&amp;") == 2


def test_quote_and_angle_brackets_are_escaped_too():
    import xml.etree.ElementTree as ET

    from mimeo.compose.package import ContentTypes, Rel, Relationships

    raw = Relationships([
        Rel(id="rId1", type="http://x/t", target='a"b<c>d&e', external=False)
    ]).to_bytes()
    ET.fromstring(raw)
    assert Relationships.parse(raw).items[0].target == 'a"b<c>d&e'

    ct = ContentTypes({"png": "image/png"}, {"/ppt/media/a&b.png": "image/png"})
    ET.fromstring(ct.to_bytes())


def test_long_text_in_a_no_wrap_box_gets_wrapping_short_text_does_not():
    """`DOM-TEXT §15`: надпись шаблона без переноса (`wrap="none"`) под короткое
    слово; наш заголовок длиннее её строки — перенос включается, иначе строка
    уходит за край слайда. Короткий текст фигуру не меняет."""
    from xml.etree import ElementTree as ET

    from mimeo.compose.substitute import allow_wrap
    from mimeo.oxml.ns import NS, qn

    def box():
        return ET.fromstring(
            f'<p:sp xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:txBody>'
            '<a:bodyPr wrap="none"/><a:p><a:r><a:t>WE</a:t></a:r></a:p></p:txBody></p:sp>')

    short, long_ = box(), box()
    assert not allow_wrap(short, 12, 19)
    assert short.find(f".//{qn('a:bodyPr')}").get("wrap") == "none"
    assert allow_wrap(long_, 40, 19)
    assert long_.find(f".//{qn('a:bodyPr')}").get("wrap") == "square"
