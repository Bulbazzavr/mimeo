"""Порождение колод и отбор трёх вариантов. `ADR-0020`, `Z-26`, план `PLAN-6.0`.

Вместо «угадать правильную раскладку с первого раза» — **породить много колод и
отобрать**. Ранг перестаёт быть оракулом и становится порождателем
разнообразия: от него требуется не точность, а несхожесть вариантов.

Почему это возможно именно у нас: один вариант плана стоит 0.002–0.015 с при
потолке ТЗ в 300 с на колоду. Двадцать семь вариантов — меньше половины секунды
(`WORKLOG/2026-09-19-variants-baseline.md`, замер 1).

**Чем это оправдано, а не красиво.** Замер 3: доминирующей политики нет — лучшая
берёт 6 шаблонов из 14, и каждая из 27 где-нибудь лучшая. Замер 4: на трёх
выданных шаблонах перебор снижает число безвозвратно сломанных слотов
**с 13 до 5**, не трогая ни одной константы продукта.

## Два требования ТЗ, которые кажутся противоречивыми

ТЗ требует, чтобы варианты были «визуально различимы и при этом одинаково
соответствовали правилам шаблона». `ADR-0020` прочитал вторую половину как
равенство по весам колоды — и замер 2 показал, что **такой тройки не существует
ни на одном шаблоне из четырнадцати**. Ключ весов это пять счётных величин, и
колоды, разошедшиеся на треть слайдов, разойдутся хотя бы в одной.

Требования относятся к **разным свойствам**, и в этом всё разрешение:

* «визуально различимы» — про **композицию**: какие раскладки и в каком порядке.
  Меряется `distance`;
* «одинаково соответствуют правилам шаблона» — про **соблюдение шаблона**:
  шрифты, цвета, макеты. У нас оно одинаково **по построению**, потому что
  каждый вариант собирается клонированием донорского слайда самого шаблона
  (`ADR-0004`, `ADR-0011`). Не результат отбора, а свойство архитектуры.

Разбор — `PLAN-6.0`, раздел про противоречие.

## Чего этот модуль не делает

Не меняет умолчаний ранга. Соблазн был: замер показал, что высокий штраф за
переполнение лучше нынешнего почти везде. Но подмена константы — ровно та
подгонка, от которой лечит `ADR-0020`. Константа остаётся, политика добавляется.
"""

from __future__ import annotations

import itertools
import json
import os
from dataclasses import dataclass, replace

from ..model import DeckPlan, PatternLibrary
from .content import ContentDoc
from .deterministic import plan_deck
from .matching import DEFAULT_TUNING, Tuning
from .quality import DeckScore, score_deck

#: Доля слайдов, на которых раскладки обязаны разойтись, чтобы варианты
#: считались различимыми. При колоде в 12 слайдов это четыре разных слайда.
#:
#: Величина подбираемая, и прятать это незачем. Смягчает её то, что она **не
#: участвует в выборе лучшего** — только в отборе непохожих. Ошибка здесь стоит
#: «варианты менее различны», а не «выбрали плохое». Лежит в конфиге.
DEFAULT_MIN_DISTANCE = 0.30

#: Сетка политик по умолчанию, если конфига нет. Значения вокруг нынешних
#: констант продукта: `repeat` 0.25, `slack` 0.25, `over` 0.35.
_GRID = ((0.0, 0.25, 0.80), (0.10, 0.25, 0.45), (0.15, 0.35, 0.90))


@dataclass(frozen=True)
class Policy:
    """Именованная настройка ранга. Имя нужно для отчёта и для разрешения ничьих."""

    name: str
    tuning: Tuning


@dataclass(frozen=True)
class Variant:
    """Порождённая колода со своей оценкой."""

    policy: Policy
    plan: DeckPlan
    score: DeckScore

    @property
    def layouts(self) -> tuple[str, ...]:
        return tuple(s.pattern_id for s in self.plan.slides)


# --- мера различия -----------------------------------------------------


def distance(left: DeckPlan, right: DeckPlan) -> float:
    """Доля слайдов, на которых стоят разные раскладки. 0 — колоды неотличимы.

    Нормировка на **большую** из двух колод: если один вариант короче, разница
    в длине это тоже различие, а не повод его не заметить.

    Мера приблизительна, и это признано в `PLAN-6.0`: две разные раскладки
    могут выглядеть похоже, а одна и та же с разным заполнением — по-разному.
    Поэтому отбор проверяется растром (`ADR-0019`: при расхождении правы глаза).
    """
    a = tuple(s.pattern_id for s in left.slides)
    b = tuple(s.pattern_id for s in right.slides)
    longest = max(len(a), len(b))
    if not longest:
        return 0.0
    same = sum(1 for i in range(min(len(a), len(b))) if a[i] == b[i])
    return round(1.0 - same / longest, 3)


# --- политики ----------------------------------------------------------


def config_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    return os.path.join(root, "config", "variants.json")


def default_policies() -> tuple[Policy, ...]:
    """Сетка по трём величинам ранга. Порядок фиксирован, имена читаемы.

    **Начинаем сеткой, а не тремя политиками**, вопреки первоначальному порядку
    в `ADR-0020`. Тот предлагал три и расширение «если замер покажет». Замер
    показал заранее: нынешняя политика продукта стоит **20-й из 27** на двух
    выданных шаблонах из трёх. Три политики — это заведомо проиграть там, где
    сдавать.
    """
    out = []
    for repeat, slack, over in itertools.product(*_GRID):
        out.append(
            Policy(
                name=f"r{repeat:g}-s{slack:g}-o{over:g}",
                tuning=Tuning(repeat=repeat, slack=slack, over=over),
            )
        )
    return tuple(out)


def load_policies(path: str | None = None) -> tuple[tuple[Policy, ...], float, str]:
    """Политики и порог различия из конфига. Возвращает ещё и источник.

    Отсутствие файла — не ошибка, а работа на встроенной сетке; вызывающий
    узнаёт об этом по третьему значению, а не по молчанию. То же правило, что
    у `prose.load_config`.
    """
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return default_policies(), DEFAULT_MIN_DISTANCE, "встроенная сетка"

    listed = raw.get("policies")
    policies: list[Policy] = []
    if isinstance(listed, list):
        for item in listed:
            if not isinstance(item, dict) or "name" not in item:
                continue
            policies.append(
                Policy(
                    name=str(item["name"]),
                    tuning=Tuning(
                        repeat=float(item.get("repeat", DEFAULT_TUNING.repeat)),
                        slack=float(item.get("slack", DEFAULT_TUNING.slack)),
                        over=float(item.get("over", DEFAULT_TUNING.over)),
                    ),
                )
            )
    min_distance = raw.get("min_distance")
    if not isinstance(min_distance, (int, float)):
        min_distance = DEFAULT_MIN_DISTANCE
    return (tuple(policies) or default_policies()), float(min_distance), path


# --- порождение --------------------------------------------------------


def generate(
    doc: ContentDoc,
    library: PatternLibrary,
    design_system_sha256: str,
    policies: tuple[Policy, ...],
    target: tuple[int, int] | None = None,
    frames_later: bool = True,
) -> tuple[Variant, ...]:
    """Колода на каждую политику. Разбор шаблона снаружи и один на всех.

    Детерминированность — условие, а не пожелание (`ADR-0020`, риск 2): порядок
    политик задан списком, случайности нет нигде. `frames_later` — зальёт ли
    генератор пустые рамки под фото после плана (`Tuning.frames_later`): это
    обстоятельство сборки, одно на все политики.
    """
    out = []
    for policy in policies:
        tuning = replace(policy.tuning, frames_later=frames_later)
        plan = plan_deck(doc, library, design_system_sha256, target, tuning)
        out.append(Variant(policy=policy, plan=plan, score=score_deck(plan, library)))
    return tuple(out)


# --- отбор -------------------------------------------------------------


def select(
    variants: tuple[Variant, ...],
    count: int = 3,
    min_distance: float = DEFAULT_MIN_DISTANCE,
) -> tuple[tuple[Variant, ...], str]:
    """Лучшие `count` вариантов, попарно различающиеся не меньше порога.

    Возвращает отобранное и **словесную причину**, если отобрано меньше
    запрошенного. Понижать порог молча запрещено: на трёх шаблонах корпуса из
    четырнадцати тройки заметно разных колод не существует вовсе, и выдать три
    почти одинаковых файла значит соврать отчётом, который формально правдив
    (`Z-20`, «молчаливый ноль»).
    """
    if not variants:
        return (), "Не порождено ни одного варианта."

    # Ничьи разрешаются именем политики — иначе порядок зависел бы от хеша.
    ordered = sorted(variants, key=lambda v: (v.score.key, v.policy.name))

    chosen: list[Variant] = [ordered[0]]
    for cand in ordered[1:]:
        if len(chosen) >= count:
            break
        if all(distance(cand.plan, c.plan) >= min_distance for c in chosen):
            chosen.append(cand)

    if len(chosen) >= count:
        return tuple(chosen), ""

    seen = len({v.layouts for v in variants})
    reason = (
        f"Запрошено вариантов: {count}, отобрано {len(chosen)}. "
        f"Из {len(variants)} порождённых колод различных всего {seen}, и "
        f"попарно разойтись хотя бы на {min_distance:.0%} слайдов могут только "
        f"{len(chosen)}. Шаблон беден раскладками: больше непохожих вариантов "
        f"из него не построить, и выдавать почти одинаковые файлы за разные мы "
        f"не станем."
    )
    return tuple(chosen), reason


# --- отчёт -------------------------------------------------------------


def report(chosen: tuple[Variant, ...], reason: str = "") -> list[str]:
    """Строки отчёта по вариантам: чем различаются и чем одинаковы.

    Вторая половина не менее важна первой и в `PLAN-6.0` появилась только из
    обратного плана от ИКР: преимущество «все варианты одинаково соблюдают
    правила шаблона» у нас есть **по построению**, но если о нём не сказать, его
    никто не увидит.
    """
    if not chosen:
        return [reason or "Вариантов нет."]

    lines = [f"вариантов  {len(chosen)}"]
    for n, v in enumerate(chosen, 1):
        s = v.score
        lines.append(
            f"  {n}. {v.policy.name:16} слайдов {len(v.plan.slides):3}  "
            f"потеряно {s.lost}  сломано {s.broken}  тесно {s.tight}  "
            f"раскладок {s.layouts}  на донышке {s.thin}"
        )
    if len(chosen) > 1:
        pairs = [
            f"{i + 1}↔{j + 1} {distance(chosen[i].plan, chosen[j].plan):.0%}"
            for i, j in itertools.combinations(range(len(chosen)), 2)
        ]
        lines.append("  различие " + ", ".join(pairs) + "  (доля слайдов с разной раскладкой)")
    lines.append(
        "  правила шаблона соблюдены одинаково всеми вариантами: каждый собран "
        "клонированием слайдов этого же шаблона, чужих шрифтов, цветов и "
        "макетов ни в одном нет (ADR-0004)"
    )
    if reason:
        lines.append("  " + reason)
    return lines
