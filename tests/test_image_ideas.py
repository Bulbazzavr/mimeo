"""Идеи картинок модели в плане колоды (`PLAN-9.0`, Ш6; `ADR-0023`, п. 1).

Идея встаёт на первый слайд своего раздела — только на типах, где сюжет
возможен (text, bullets, cards, image_text), не на разделе с картинкой автора
и не больше одной на три слайда колоды, по порядку колоды. Всё, что не взято,
названо в предупреждении плана. У пути без модели идей нет — план не меняется
ни ключом. Замер, из которого правила, — `WORKLOG/2026-09-25-z57-sh6-baseline.md`.
"""

from __future__ import annotations

import json
import os

import pytest

from mimeo.analyze import analyze_template
from mimeo.plan import outline
from mimeo.plan.content import ContentBlock, ContentDoc, ContentSection
from mimeo.plan.deterministic import plan_deck
from mimeo.plan.variants import generate, load_policies, select

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(ROOT, "contracts", "deck-plan.schema.json")
PNG = os.path.join(ROOT, "examples", "img", "pipeline-stages.png")


def _sec(sid, heading, kind, idea="", items=("пункт один", "пункт два"), image=False):
    blocks = (ContentBlock(id=f"{sid}b1", kind="list", items=tuple(items), text=" ".join(items)),)
    if image:
        blocks += (ContentBlock(id=f"{sid}i01", kind="image", ref=PNG),)
    return ContentSection(id=sid, heading=heading, blocks=blocks, kind=kind, image_idea=idea)


def _doc(*sections, cover_idea="Логотип компании на фоне продуктов", planner="mixed"):
    """Колода модели так, как её строит `outline.to_doc`: обложка — только название."""
    cover = ContentSection(id="m01", heading="Колода", blocks=(), kind="cover", image_idea=cover_idea)
    return ContentDoc(name="t.md", title="Колода", sections=[cover, *sections],
                      origin="текст", planner=planner)


def _six():
    return _doc(
        _sec("m02", "Склад", "bullets", "Кладовщик принимает ящики с овощами"),
        _sec("m03", "Рост", "metric", "График роста заказов", items=("41 200",)),
        _sec("m04", "Схема", "text", "Схема стадий", image=True),
        _sec("m05", "Курьеры", "cards", "Курьер передаёт пакет у двери  "),
        _sec("m06", "Очередь", "text", "Очередь у стойки выдачи"),
        # пробелы — не идея: модель отдаёт пустую строку и так, и эдак
        _sec("m07", "Просьба", "closing", "   ", items=("нужно решение",)),
    )


@pytest.fixture(scope="module")
def library(multi_template_path):
    a = analyze_template(multi_template_path)
    return a.patterns, a.design_system.source.sha256


def _ideas(plan) -> dict[str, str]:
    return {s.origin_section: s.image_idea for s in plan.slides if s.image_idea}


def _note(plan) -> str:
    found = [w for w in plan.warnings if w.startswith("Идеи картинок")]
    assert len(found) <= 1
    return found[0] if found else ""


def test_ideas_go_to_the_plan_by_kind_image_and_cap(library):
    """Обложка и число — не по типу; раздел с картинкой автора — не нужна; потолок
    — одна на три слайда, по порядку колоды; остальное названо словами."""
    lib, sha = library
    plan = plan_deck(_six(), lib, sha)
    assert len(plan.slides) == 7
    assert _ideas(plan) == {
        "m02": "Кладовщик принимает ящики с овощами",
        "m05": "Курьер передаёт пакет у двери",
    }, "потолок 7 // 3 = 2 — первые две годные по порядку колоды"
    note = _note(plan)
    assert "от модели 6, в план колоды взято 2" in note
    assert "по типу слайда — 2 (обложка, крупное число)" in note
    assert "на слайде с картинкой из текста — 1" in note
    assert "сверх потолка — 1: «Очередь»" in note
    assert "(потолок 2 при 7 слайдах)" in note


def test_idea_is_in_json_only_where_it_stands_and_passes_the_schema(library):
    jsonschema = pytest.importorskip("jsonschema")
    lib, sha = library
    data = plan_deck(_six(), lib, sha).to_json()
    with_key = [s["origin"]["section"] for s in data["slides"] if "image_idea" in s]
    assert with_key == ["m02", "m05"]
    with open(SCHEMA, encoding="utf-8") as fh:
        jsonschema.validate(data, json.load(fh))


def test_empty_idea_is_refused_by_the_schema(library):
    """Отсутствие поля значит «идеи нет»; пустая строка — не второй способ это сказать."""
    jsonschema = pytest.importorskip("jsonschema")
    lib, sha = library
    data = plan_deck(_six(), lib, sha).to_json()
    data["slides"][0]["image_idea"] = ""
    with open(SCHEMA, encoding="utf-8") as fh:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data, json.load(fh))


def test_ideas_do_not_change_the_layout(library):
    """Идея — пометка для генератора, а не довод раскладки: те же раскладки и
    заливки, что у той же колоды без идей."""
    lib, sha = library
    with_ideas = plan_deck(_six(), lib, sha)
    bare = ContentDoc(name="t.md", title="Колода",
                      sections=[ContentSection(s.id, s.heading, s.blocks, s.kind) for s in _six().sections],
                      origin="текст", planner="mixed")
    without = plan_deck(bare, lib, sha)
    assert [(s.pattern_id, s.fills) for s in with_ideas.slides] == \
           [(s.pattern_id, s.fills) for s in without.slides]
    assert not _ideas(without) and not _note(without)


def test_path_without_model_has_no_ideas_and_no_key(library):
    lib, sha = library
    doc = _six()
    doc = ContentDoc(name=doc.name, title=doc.title, origin=doc.origin, planner="deterministic",
                     sections=[ContentSection(s.id, s.heading, s.blocks) for s in doc.sections])
    data = plan_deck(doc, lib, sha).to_json()
    assert all("image_idea" not in s for s in data["slides"])
    assert not any(w.startswith("Идеи картинок") for w in data["diagnostics"]["warnings"])


def test_split_section_carries_its_idea_once(library):
    """Раздел, не влезший целиком, делится — идея встаёт на первую часть, а не
    на каждую: иначе генератор нарисовал бы одно и то же дважды."""
    lib, sha = library
    long_items = tuple(f"пункт номер {n}: " + "длинное пояснение к пункту " * 3 for n in range(12))
    doc = _doc(_sec("m02", "Длинный список", "bullets", "Кладовщик у стеллажей", items=long_items),
               _sec("m03", "Итог", "text", items=("коротко",)), cover_idea="")
    plan = plan_deck(doc, lib, sha)
    parts = [s for s in plan.slides if s.origin_section == "m02"]
    assert len(parts) > 1, "фикстура обязана делить раздел — иначе тест ничего не держит"
    assert [s.image_idea for s in parts] == ["Кладовщик у стеллажей"] + [None] * (len(parts) - 1)


def test_idea_of_a_section_left_out_of_the_deck_is_named(library):
    """Раздел, которому нет места ни в одной раскладке (таблица на шаблоне без
    таблиц), уходит в `unplaced` — и его идея названа, а не упала и не встала
    на чужой слайд."""
    lib, sha = library
    table = ContentSection(id="m02", heading="Таблица", kind="text", image_idea="Склад ночью",
                           blocks=(ContentBlock(id="t1", kind="table", text="| a | b |\n| 1 | 2 |"),))
    rest = [_sec(f"m{n:02d}", f"Раздел {n}", "text", items=("коротко",)) for n in range(3, 8)]
    plan = plan_deck(_doc(table, *rest, cover_idea=""), lib, sha)
    assert "m02" in plan.unplaced and len(plan.slides) == 6
    assert not _ideas(plan)
    assert "раздела нет в колоде — 1" in _note(plan)


def test_cap_follows_the_plan_and_deck_order(library):
    """Потолок считается от слайдов плана, отбор — по порядку колоды."""
    lib, sha = library
    sections = [_sec(f"m{n:02d}", f"Раздел {n}", "text", f"Сюжет {n}") for n in range(2, 11)]
    plan = plan_deck(_doc(*sections, cover_idea=""), lib, sha)
    cap = len(plan.slides) // 3
    kept = _ideas(plan)
    assert len(kept) == cap >= 2
    order = [s.origin_section for s in plan.slides if s.origin_section in {x.id for x in sections}]
    assert list(kept) == order[:cap]


def test_variants_carry_one_set_of_ideas(library):
    """Три варианта вёрстки — одно содержание: и идеи у них одни (`ADR-0023`)."""
    lib, sha = library
    policies, min_distance, _ = load_policies()
    chosen, _ = select(generate(_six(), lib, sha, policies), 3, min_distance)
    assert chosen
    sets = {tuple(sorted(_ideas(v.plan).items())) for v in chosen if len(v.plan.slides) == 7}
    assert len(sets) == 1
    assert () not in sets, "у вариантов идеи есть, а не одинаково пусто"


def test_rules_builtin_equal_config(tmp_path):
    assert outline.idea_rules() == outline.IdeaRules()
    assert outline.idea_rules(str(tmp_path / "нет.json")) == outline.IdeaRules()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"image_ideas": {"kinds": ["picture"], "slides_per_idea": 0}}),
                   encoding="utf-8")
    assert outline.idea_rules(str(bad)) == outline.IdeaRules(), "тип не из KINDS и ноль не принимаются"


def test_idea_kinds_and_words_are_the_code_vocabulary():
    assert set(outline.IdeaRules().kinds) <= set(outline.KINDS)
    assert set(outline.KIND_WORDS) == set(outline.KINDS)
