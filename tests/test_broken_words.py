"""Слово, разорванное посередине строки (`Z-56`).

На девятке модели 26 сентября — четыре случая: «сортировк / и» в надписи 54 пт
на WorkSpace и «зафиксирован / ы» в узкой колонке 20 пт на VK Education. Мерки
высоты и ширины их не видят: после разрыва всё влезает. Зонд PowerPoint отдаёт
число разрывов и ширину слова целиком; петля VERIFY ужимает кегль, пока слово
не встанет в строку. PowerPoint здесь не запускается.
"""

from __future__ import annotations

from dataclasses import replace

from mimeo.verify import find_defects, repair_plan
from mimeo.verify.detect import BROKEN_WORD, OVERFLOW_HEIGHT
from mimeo.verify.metrics import _collect
from tests.test_verify_repair import make, scale_of


def _broken(width: float, words: int = 1, text_height: float = 40.0):
    """Бокс шириной 100 пт без полей; слово шириной `width` разорвано."""
    plan, lib, m = make(font=54.0, text_height=text_height)
    shape = replace(m.shapes[0], broken_words=words, broken_width=width)
    return plan, lib, replace(m, shapes=(shape,))


def test_probe_line_carries_breaks_and_word_width():
    out = ("deck=0\nslides=1\n"
           "shape slide=9 id=720 w=305.47 h=249.31 tw=277.88 th=259.2 ml=0 mr=9.6 mt=0 mb=9.6 "
           "fit=0 sz=54 len=26 x=33.8 y=139.63 anchor=1 bl=33.8 bt=139.63 rot=0 brk=1 ww=310.88\n"
           "status=ok\n")
    shape = _collect(out, ["d.pptx"])[0].shapes[0]
    assert (shape.broken_words, shape.broken_width) == (1, 310.88)
    old = _collect(out.replace(" brk=1 ww=310.88", ""), ["d.pptx"])[0].shapes[0]
    assert (old.broken_words, old.broken_width) == (0, 0.0), "старый зонд — разрывов не видно"


def test_broken_word_is_a_repairable_defect_with_its_width_ratio():
    plan, lib, m = _broken(width=105.0)
    found = find_defects(m, plan, lib, (0,))
    assert [(d.kind, d.ratio, d.repairable) for d in found.defects] == [(BROKEN_WORD, 1.05, True)]


def test_height_overflow_comes_first():
    """Переполнение по высоте — один дефект: его ремонт нередко снимает и разрыв."""
    plan, lib, m = _broken(width=105.0, text_height=120.0)
    assert [d.kind for d in find_defects(m, plan, lib, (0,)).defects] == [OVERFLOW_HEIGHT]


def test_the_word_shrinks_linearly_to_fit_its_line():
    """Ширина слова падает ровно как кегль: 100 × 0.97 / 1.05 = 92, а не корень,
    как у высоты (тот дал бы 96, и слово осталось бы разорванным)."""
    plan, lib, m = _broken(width=105.0)
    result = repair_plan(plan, find_defects(m, plan, lib, (0,)), m, (0,), lib)
    assert scale_of(result) == 92
    assert 105.0 * scale_of(result) / 100 < 100.0, "слово влезает в строку"


def test_a_barely_broken_word_still_moves_the_scale():
    """Ширину не знаем (0) или слово шире строки на волосок — шаг хотя бы в
    процент, иначе петля топталась бы на месте до конца раундов."""
    for width in (0.0, 100.5):
        plan, lib, m = _broken(width=width)
        result = repair_plan(plan, find_defects(m, plan, lib, (0,)), m, (0,), lib)
        assert scale_of(result) < 100 and result.changed
    from mimeo.verify.detect import Defect
    from mimeo.verify.repair import _wanted
    narrow = Defect(kind=BROKEN_WORD, slide_index=0, slot_id="s01", shape_id="11", role="body",
                    ratio=0.9, repairable=True)
    assert _wanted(100, narrow, 0.9) == 99, "шкала не растёт и не стоит на месте"


def test_a_word_that_cannot_shrink_is_reported_and_the_report_fits_its_schema():
    """Кегль на пределе читаемости — разрыв остаётся и назван поимённо."""
    import pytest

    from mimeo.verify import verify_deck
    from tests.test_verify_loop import Builder, load_schema, scene

    plan, lib, report, _font = scene()
    _, _, broken = _broken(width=110.0)
    at_floor = replace(broken.shapes[0], font_size=10.0)

    def measurer(decks):
        return (replace(broken, deck=decks[0], shapes=(at_floor,)),)

    rep = verify_deck("t.pptx", plan, lib, "out/deck.pptx", report, rounds=3,
                      measurer=measurer, builder=Builder(report)).report
    assert [d.kind for d in rep.unresolved] == [BROKEN_WORD] and rep.after == 1
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator(load_schema()).validate(rep.to_json())
