"""Встраивание изображений (`Z-28a`, `PLAN-7.10`).

Тестов на этот тракт не было ни одного — `replace_picture` не упоминался в
`tests/` вовсе, и замер нашёл в нём три дефекта с первого прогона
(`WORKLOG/2026-09-21-z28a-baseline.md`). Здесь по тесту на каждый.
"""

from __future__ import annotations

import hashlib
import os
import zipfile

import pytest

from mimeo.analyze import analyze_template
from mimeo.analyze.picture import (
    BACKDROP, BACKGROUND, FRAME, ICON, ILLUSTRATION, _overlap, classify,
)
from mimeo.compose import build
from mimeo.plan import load_content, plan_deck
from mimeo.plan.content import parse_markdown, extract_images, resolve_image
from mimeo.plan.imagesize import image_size

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(ROOT, "examples", "img", "pipeline-stages.png")
IMG2 = os.path.join(ROOT, "examples", "img", "overflow-16-to-1.png")
VK = os.path.join(ROOT, "tz", "templates", "2_Датасет VK Tech шаблон.pptx")


class _Rect:
    def __init__(self, x, y, cx, cy):
        self.x, self.y, self.cx, self.cy = x, y, cx, cy


# --- размеры из заголовка файла ----------------------------------------


def test_image_size_reads_png_header():
    assert image_size(IMG) == (960, 260)
    assert image_size(IMG2) == (640, 420)


def test_unreadable_image_says_so_instead_of_guessing():
    """«Проверить не смог» и «квадрат» — разные ответы (`CLAUDE.md`)."""
    assert image_size(os.path.join(ROOT, "README.md")) is None
    assert image_size(os.path.join(ROOT, "нет-такого-файла.png")) is None


# --- путь к картинке, названный прозой ---------------------------------


def test_path_in_prose_becomes_an_image_block():
    """ТЗ говорит: вход — сплошная неструктурированная проза. До `Z-28a`
    картинку понимала только markdown-разметка, и на заявленном входе
    встраивания не было вовсе: ноль заливок на 84 парах."""
    doc = parse_markdown(
        "Приложу схему — examples/img/pipeline-stages.png — и на этом всё.",
        "t",
    )
    out, notes = extract_images(doc, ROOT)
    blocks = [b for s in out.sections for b in s.blocks]
    images = [b for b in blocks if b.kind == "image"]
    assert len(images) == 1
    assert images[0].ref.endswith("pipeline-stages.png")
    assert notes


def test_path_leaves_the_text_and_takes_its_punctuation_with_it():
    """Иначе путь остаётся текстом на слайде — ровно то, что было видно на
    растре сдаточной колоды VK Tech до `Z-28a`. Обрамляющие тире тоже уходят:
    первая сборка дала «Схему четырёх стадий— и столбики»."""
    doc = parse_markdown(
        "Схему четырёх стадий — examples/img/pipeline-stages.png — "
        "и столбики про переполнения, examples/img/overflow-16-to-1.png",
        "t",
    )
    out, _ = extract_images(doc, ROOT)
    text = " ".join(b.text for s in out.sections for b in s.blocks if b.kind != "image")
    assert ".png" not in text, f"путь остался текстом: {text}"
    assert text == "Схему четырёх стадий и столбики про переполнения", text


def test_a_word_that_merely_looks_like_a_path_is_left_alone():
    """Признак — расширение **плюс файл на диске**. Одного расширения мало."""
    doc = parse_markdown("Смотри в файле выдуманный-отчёт.png, он в архиве.", "t")
    out, notes = extract_images(doc, ROOT)
    blocks = [b for s in out.sections for b in s.blocks]
    assert not [b for b in blocks if b.kind == "image"]
    assert "выдуманный-отчёт.png" in blocks[0].text, "несуществующий путь не вырезаем"
    assert any("нет на диске" in n for n in notes), "о пропаже надо сказать вслух"


def test_ref_is_portable_not_machine_specific():
    """`ref` уезжает в `deck-plan.json`. Абсолютный путь сделал бы артефакт
    разным на разных машинах, а критерий 2 — про воспроизводимость."""
    where = resolve_image("examples/img/pipeline-stages.png", ROOT, os.getcwd())
    assert where
    assert not os.path.isabs(where) or where.startswith(os.getcwd().replace(os.sep, "/"))
    assert "\\" not in where


# --- вид слота под картинку --------------------------------------------


def test_slot_kinds_follow_geometry():
    slide = (12192000, 6858000)
    big = _Rect(1000000, 1000000, 4000000, 2000000)
    assert classify(big, [], *slide) == ILLUSTRATION
    assert classify(_Rect(0, 0, 300000, 300000), [], *slide) == ICON
    assert classify(_Rect(0, 0, 12192000, 6858000), [], *slide) == BACKGROUND
    assert classify(big, [_Rect(1000000, 1000000, 4000000, 2000000)], *slide) == BACKDROP


@pytest.mark.skipif(not os.path.exists(VK), reason="материалы ТЗ не коммитятся")
def test_most_image_slots_are_not_for_our_illustration():
    """Замер, из-за которого признак появился: из 688 слотов `image` по
    корпусу годен каждый пятый. На выданном VK Tech — 94 из 296."""
    a = analyze_template(VK)
    kinds = [s.picture_kind for p in a.patterns.patterns for s in p.slots
             if s.content_type == "image"]
    assert kinds, "у этого шаблона слоты под картинку есть"
    # Пять рамок под фото с подсказкой «Вставить фото» (`Z-55`) — отдельный вид.
    assert set(kinds) <= {ILLUSTRATION, ICON, BACKDROP, BACKGROUND, FRAME}
    assert kinds.count(FRAME) == 5
    assert kinds.count(ILLUSTRATION) < len(kinds), (
        "если годными признаны все, признак ничего не отсекает"
    )
    assert kinds.count(ILLUSTRATION) > 0, "если годных ноль, вставлять будет некуда"


@pytest.mark.skipif(not os.path.exists(VK), reason="материалы ТЗ не коммитятся")
def test_our_picture_goes_only_into_an_illustration_slot():
    a = analyze_template(VK)
    doc = load_content(os.path.join(ROOT, "examples", "content-mimeo.md"))
    plan = plan_deck(doc, a.patterns, a.design_system.source.sha256)
    by_id = {p.id: p for p in a.patterns.patterns}
    placed = [
        by_id[s.pattern_id].slots
        for s in plan.slides for f in s.fills if f.kind == "image"
    ]
    fills = [(s, f) for s in plan.slides for f in s.fills if f.kind == "image"]
    assert fills, "на этом шаблоне картинки обязаны вставиться"
    for slide, fill in fills:
        slot = next(x for x in by_id[slide.pattern_id].slots if x.id == fill.slot_id)
        assert slot.picture_kind == ILLUSTRATION, (
            f"наша картинка легла в слот вида {slot.picture_kind}"
        )
    assert placed


# --- две картинки на одном слайде --------------------------------------


@pytest.mark.skipif(not os.path.exists(VK), reason="материалы ТЗ не коммитятся")
def test_two_pictures_on_one_slide_do_not_overwrite_each_other(tmp_path):
    """Имя части бралось от номера слайда: вторая картинка затирала первую,
    обе связи указывали на неё, предупреждений ноль. На растре одна и та же
    картинка стояла дважды (`WORKLOG/2026-09-21-z28a-baseline.md`, дефект A).
    """
    source = tmp_path / "two.md"
    source.write_text(
        "# Две подряд\n\nТекст слайда.\n\n"
        f"![одна]({IMG})\n\n![другая]({IMG2})\n",
        encoding="utf-8",
    )
    a = analyze_template(VK)
    doc = load_content(str(source))
    plan = plan_deck(doc, a.patterns, a.design_system.source.sha256)
    planned = [f for s in plan.slides for f in s.fills if f.kind == "image"]
    if len(planned) < 2:
        pytest.skip("обе картинки на один слайд не легли — проверять нечего")
    target = tmp_path / "out.pptx"
    build(VK, plan, a.patterns, str(target))
    with zipfile.ZipFile(target) as z:
        ours = [n for n in z.namelist() if "/media/mimeo" in n]
        digests = {hashlib.sha256(z.read(n)).hexdigest() for n in ours}
    assert len(ours) == len(planned), (
        f"заливок {len(planned)}, а частей {len(ours)}: картинка затёрта"
    )
    assert len(digests) == 2, "обе картинки разные, значит и байты должны различаться"


# --- картинка не должна уносить с собой текст --------------------------


def test_a_homeless_picture_does_not_take_the_section_with_it():
    """`Match.fits` судит по `leftover`, и пока картинка попадала туда, на
    шаблоне без слотов-иллюстраций непригодными становились **все** раскладки:
    раздел пропадал целиком вместе с текстом — минус 484 и 468 знаков на
    `60042` и `prostoj-shablon`.

    Текст — редакция `content-mimeo.md` 26 сентября, фикстурой: 27 сентября
    абзац «чего не умеем» переписан, путь без модели режет его на пять пунктов,
    и раздел с двумя картинками и этим списком на `60042` не встаёт целиком ни в
    одну раскладку — это другой класс (раздел крупнее раскладок бедного
    шаблона), а не пропажа из-за картинки, которую держит этот тест."""
    poor = os.path.join(ROOT, "samples", "60042.pptx")
    if not os.path.exists(poor):
        pytest.skip("шаблоны не коммитятся: tools/fetch_samples.py")
    a = analyze_template(poor)
    doc = load_content(os.path.join(ROOT, "tests", "fixtures", "content-mimeo-2609.md"))
    plan = plan_deck(doc, a.patterns, a.design_system.source.sha256)
    assert not plan.unplaced, f"разделы пропали целиком: {plan.unplaced}"
    said = [w for w in plan.warnings if "Картинок не вставлено" in w]
    assert said, "картинке негде встать — об этом обязано быть сказано"
    assert "слайд" in said[0], "остаток обязан называть слайды"


# --- слот, который выглядит местом под иллюстрацию, но им не является -----
#     (`Z-51`, `PLAN-7.11`)


def test_a_slot_that_covers_a_caption_is_not_a_place_for_our_picture():
    """Перекрытие надо считать в обе стороны, и вторую сторону нашёл растр.

    На `p25` выданного VK Tech слот `s03` 5.69×1.73 накрыт подписью `s06`
    лишь на 22 % своей площади — по старому правилу «не подложка», — а саму
    подпись накрывает целиком. Наша картинка там прятала её без следа.
    """
    slide = (9144000, 5143500)          # 10×5.63 дюйма
    band = _Rect(0, 0, 4000000, 1200000)
    caption = _Rect(200000, 400000, 3000000, 300000)   # целиком внутри полосы
    # Полоса накрыта подписью лишь на малую долю — прежняя проверка молчала.
    assert _overlap(band, caption) < 0.5
    assert classify(band, [caption], *slide) == BACKDROP


def test_a_slot_beside_a_caption_stays_an_illustration():
    """Встречная проверка не должна запрещать всё подряд: подпись рядом со
    слотом, а не внутри него, — обычная и здоровая вёрстка."""
    slide = (9144000, 5143500)
    picture = _Rect(0, 0, 3000000, 2000000)
    caption = _Rect(0, 2100000, 3000000, 300000)       # ниже, не пересекается
    assert classify(picture, [caption], *slide) == ILLUSTRATION


@pytest.mark.skipif(not os.path.exists(VK), reason="материалы ТЗ не коммитятся")
def test_the_given_template_has_such_slots_and_they_are_excluded():
    """Замер, из-за которого проверка появилась: по корпусу таких слотов
    **52 из 179**, из них 40 на выданном VK Tech и 7 на VK Education."""
    from mimeo.analyze.picture import load_config
    cfg = load_config()
    a = analyze_template(VK)
    hiding = [
        (p.id, s.id)
        for p in a.patterns.patterns
        for s in p.slots
        if s.content_type == "image" and s.picture_kind == ILLUSTRATION
        for t in p.slots
        if t.content_type in ("text", "list", "number")
        and _overlap(t.rect, s.rect) > cfg.backdrop_overlap
    ]
    assert not hiding, f"слот-иллюстрация прячет подпись: {hiding[:5]}"


# --- две наши картинки не ложатся одна на другую --------------------------


def test_a_slot_overlapping_a_taken_one_is_not_offered():
    """`Z-28a` развёл две картинки **в пакете** — у каждой своя часть. Но
    слоты живого шаблона бывают вложены друг в друга (`p20` выданного
    VK Tech: `s04` 4.60×1.74 целиком внутри `s03` 4.69×1.82), и обе картинки
    ложились в одно место. Числами это не ловится: заливок столько, сколько
    задумано."""
    from mimeo.model import Slot
    from mimeo.plan.matching import _clear_of_taken

    def slot(sid, x, y, cx, cy):
        return Slot(id=sid, role="image", content_type="image",
                    rect=_Rect(x, y, cx, cy), type_role=None, capacity=None,
                    required=False, picture_kind=ILLUSTRATION)

    outer = slot("s01", 0, 0, 4000000, 2000000)
    inner = slot("s02", 100000, 100000, 3800000, 1800000)
    apart = slot("s03", 5000000, 0, 4000000, 2000000)

    class _Pat:
        slots = (outer, inner, apart)

    kept = _clear_of_taken([inner, apart], _Pat(), {"s01"})
    assert [s.id for s in kept] == ["s03"], "вложенный слот предложен повторно"
    assert _clear_of_taken([inner, apart], _Pat(), set()) == [inner, apart]


def test_match_does_not_put_two_pictures_into_nested_slots():
    """То же правило, но через `match`: без него обе картинки раздела ложатся
    в вложенные друг в друга слоты и на растре наезжают одна на другую."""
    from mimeo.model import Capacity, Pattern, Slot
    from mimeo.plan.content import ContentBlock, ContentSection
    from mimeo.plan.matching import match

    def img(sid, x, y, cx, cy):
        return Slot(id=sid, role="image", content_type="image",
                    rect=_Rect(x, y, cx, cy), type_role=None, capacity=None,
                    required=False, picture_kind=ILLUSTRATION)

    title = Slot(
        id="s00", role="title", content_type="text",
        rect=_Rect(0, 0, 8000000, 500000), type_role="h1",
        capacity=Capacity(max_chars=80, max_lines=2, chars_per_line=40,
                          target_chars=60, max_items=None, donor_chars=None,
                          basis="test"),
        required=True,
    )
    # `s02` целиком внутри `s01` — рамка и фотография в ней; `s03` в стороне.
    #
    # Пропорции подобраны так, чтобы **без правила** обе картинки выбрали
    # вложенную пару: полоса 3.69 ближе всего к `s01` (3.70), столбики 1.52 —
    # к `s02` (1.50), а непересекающийся `s03` (1.00) проигрывает обоим.
    pattern = Pattern(
        id="p01", kind="image_text", donor_part="/ppt/slides/slide1.xml",
        donor_index=1,
        slots=(title, img("s01", 0, 600000, 3700000, 1000000),
               img("s02", 100000, 600000, 1500000, 1000000),
               img("s03", 4500000, 600000, 2000000, 2000000)),
        members=(1,), cohesion=None, donor_reason="тест", source="test",
    )
    section = ContentSection(
        id="sec", heading="Картинки",
        blocks=(ContentBlock(id="b1", kind="image", ref=IMG),
                ContentBlock(id="b2", kind="image", ref=IMG2)),
    )
    m = match(section, pattern)
    assert m is not None
    used = [f.slot_id for f in m.fills if f.kind == "image"]
    assert len(used) == 2, f"обе картинки обязаны встать, встало {used}"
    assert "s03" in used, "второй картинке полагается непересекающийся слот"
    assert not ({"s01", "s02"} <= set(used)), (
        f"обе картинки легли во вложенные слоты: {used}"
    )
