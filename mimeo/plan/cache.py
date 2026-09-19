"""Кэш ответов модели. Шаг Ш3 плана `PLAN-2.6`, решение — `ADR-0021`.

Кэш здесь не ускоритель, а **способ выполнить критерий 2**: эксперт без
видеокарты и без ключа обязан получить те же колоды, что показываем мы. Значит
ответ модели получается один раз, кладётся в репозиторий и дальше читается как
данные (`ADR-0009`).

**Кэш разделён надвое.** Ответ модели о выданном шаблоне — производная от данных
ТЗ, а п. 7.3.5 Положения объявляет их конфиденциальными. Общий кэш означал бы
тихую утечку через коммит, поэтому каталог выбирается **по входным путям**, а не
флагом: забыть флаг можно, подменить путь нельзя.

Ключ — `sha256` от всего, что меняет ответ: имени модели, режима, параметров,
схемы и текста запроса. Адрес сервера в ключ **не входит** — одна и та же модель
на LM Studio и на инференсе VK должна попадать в один кэш.

Устойчивость ключа проверена замером, а не предположена: три процесса с
`PYTHONHASHSEED=random` дали один и тот же хеш —
`WORKLOG/2026-09-17-llm-client.md`, замер 4.
"""

from __future__ import annotations

import hashlib
import json
import os

#: Версия формата ключа. Меняется, когда меняется состав ключа: старые файлы
#: тогда перестают находиться, и это правильно — они отвечают на другой вопрос.
KEY_FORMAT = "mimeo-llm-cache/1"

#: Версия формата файла. Меняется, когда меняется состав самого файла.
FILE_FORMAT = 1


def repo_root() -> str:
    """Корень репозитория: `mimeo/` лежит в нём, пакет — на два уровня ниже."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def key_for(
    *,
    model: str,
    mode: str,
    system: str,
    user: str,
    schema: dict | None = None,
    params: dict | None = None,
    extra_body: dict | None = None,
) -> str:
    """Ключ ответа. Всё, что меняет ответ, обязано быть здесь.

    Адреса сервера здесь нет намеренно: переезд с LM Studio на чужой инференс не
    должен обесценивать накопленное. Модель различается именем, а у локальных
    сборок в имени есть и квантование (`…@q8_0`).
    """
    digest = hashlib.sha256()
    for part in (
        KEY_FORMAT,
        model,
        mode,
        _canonical(params or {}),
        _canonical(extra_body or {}),
        _canonical(schema or {}),
        system,
        user,
    ):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def is_tz_path(path: str, root: str | None = None) -> bool:
    """Лежит ли путь под `tz/` — каталогом материалов ТЗ.

    Сравнение по нормализованному абсолютному пути, а не по окончанию строки:
    на Windows приходит `F:\\…\\tz\\templates\\x.pptx` с обратными слэшами, и
    проверка через `endswith('tz/...')` молча не сработала бы. В этом проекте
    так уже терялась правка — `CLAUDE.md`, про `glob` и обратный слэш.
    """
    base = os.path.normcase(os.path.abspath(os.path.join(root or repo_root(), "tz")))
    here = os.path.normcase(os.path.abspath(path))
    return here == base or here.startswith(base + os.sep)


def root_for(
    inputs,
    root: str = "cache/llm",
    tz_root: str = "tz/cache/llm",
    repo: str | None = None,
) -> str:
    """Каталог кэша под эти входы. Хоть один из-под `tz/` — значит кэш закрытый.

    Правило намеренно пессимистично: смешанный набор входов идёт в закрытый
    кэш. Ошибиться в эту сторону значит недокоммитить, в другую — опубликовать
    конфиденциальное.
    """
    repo = repo or repo_root()
    chosen = tz_root if any(is_tz_path(p, repo) for p in inputs if p) else root
    # normpath, а не голый join: в конфиге пути записаны через прямой слэш, и
    # без нормализации получилось бы `…\mimeo\tz/cache/llm` — работать будет, а
    # в диагностике и в сравнениях выглядит как чужой путь.
    return os.path.normpath(chosen if os.path.isabs(chosen) else os.path.join(repo, chosen))


def path_for(root: str, key: str) -> str:
    return os.path.join(root, f"{key}.json")


def read(root: str, key: str) -> str | None:
    """Ответ из кэша или `None`. Битый файл — промах, а не падение.

    Промах и битый файл различаются вызывающим по этому же `None` только внешне:
    строку в диагностику пишет клиент, и «в кэше нет» от «кэш не прочитался» он
    отличает сам (`client.py`).
    """
    try:
        with open(path_for(root, key), encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != FILE_FORMAT or payload.get("key") != key:
        return None
    text = payload.get("response")
    return text if isinstance(text, str) else None


def write(root: str, key: str, response: str, *, model: str, mode: str, label: str = "") -> str:
    """Кладёт ответ в кэш и возвращает путь.

    Метки времени не пишутся: они дали бы шум в каждом коммите и не сообщали бы
    ничего. Порядок ключей фиксирован, перевод строки `\\n` на всех платформах —
    файл обязан быть байт в байт одинаковым.
    """
    os.makedirs(root, exist_ok=True)
    payload = {
        "version": FILE_FORMAT,
        "key": key,
        "model": model,
        "mode": mode,
        "label": label,
        "response": response,
    }
    target = path_for(root, key)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return target
