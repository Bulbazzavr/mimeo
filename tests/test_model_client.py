"""Клиент к модели: транспорт, кэш, откат. `Z-07`, `PLAN-2.6`, шаги 1, 2, 4.

Живой модели здесь нет и не нужно: всё проверяется поддельным сервером
(`tests/fake_model.py`), который умеет отвечать плохо — обрывом, пустотой,
рассуждением вместо ответа. От живой модели такого по заказу не добьёшься.

Тесты написаны так, чтобы **они могли провалиться**. Два места, где это не само
собой:

* разделение кэша проверяется **на обе стороны** — что `tz/…` игнорируется
  git-ом и что `cache/…` не игнорируется. Проверка одной стороны прошла бы и в
  случае, когда игнорируется всё;
* `git check-ignore` спрашивается **про файл**, а не про каталог: на пути с
  завершающим слэшем он отвечает «игнорируется» для чего угодно (замер 7,
  `WORKLOG/2026-09-17-llm-client.md`).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import replace

import pytest

from mimeo.analyze import analyze_template
from mimeo.plan import cache, parse_markdown
from mimeo.plan.client import (
    Access,
    Answer,
    ClientConfig,
    Endpoint,
    ModelClient,
    ModelError,
    chat_body,
    content_of,
    load_config,
    post_chat,
)
from mimeo.plan.matching import rank
from mimeo.plan.prompt import Mode, build_request
from mimeo.plan.validate import check, extract_json, repair
from tests.fake_model import FakeModel

CONTENT = "## Что умеет система\n\n- Первое\n- Второе\n- Третье\n"


@pytest.fixture(scope="module")
def context(multi_template_path):
    analysis = analyze_template(multi_template_path)
    section = parse_markdown(CONTENT).sections[0]
    matches = rank(section, analysis.patterns.patterns)
    by_id = {p.id: p for p in analysis.patterns.patterns}
    return section, matches, by_id


@pytest.fixture(scope="module")
def request_and_pattern(context):
    section, matches, by_id = context
    request = build_request(section, matches, by_id)
    return request, by_id[matches[0].pattern_id]


def _answer_for(pattern) -> dict:
    """Годный ответ модели под конкретный паттерн фикстуры."""
    fills = []
    for slot in pattern.slots:
        if slot.content_type == "text":
            fills.append({"slot_id": slot.id, "kind": "text", "text": "Коротко"})
        elif slot.content_type == "list":
            fills.append({"slot_id": slot.id, "kind": "list", "items": ["Раз", "Два"]})
        elif slot.content_type == "number":
            fills.append({"slot_id": slot.id, "kind": "number", "text": "42"})
    return {"pattern_id": pattern.id, "fills": fills, "reason": "подходит"}


def _config(fake: FakeModel, tmp_path, access=Access.ON, **kw) -> ClientConfig:
    return ClientConfig(
        access=access,
        endpoint=Endpoint(base_url=fake.base_url, model=fake.model, **kw),
        mode=Mode.JSON_SCHEMA,
        params={"temperature": 0, "seed": 0},
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        cache_root=str(tmp_path / "cache"),
        tz_cache_root=str(tmp_path / "tzcache"),
        loaded=True,
        source="тест",
    )


# --- что уходит --------------------------------------------------------


def test_schema_goes_into_response_format(request_and_pattern, tmp_path):
    """Режим json_schema кладёт схему в response_format. ADR-0010."""
    request, _ = request_and_pattern
    body = chat_body(request, ClientConfig(mode=Mode.JSON_SCHEMA))
    assert body["response_format"]["json_schema"]["schema"] == request.schema
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in body


def test_schema_goes_into_tool_parameters(context):
    section, matches, by_id = context
    request = build_request(section, matches, by_id, mode=Mode.TOOL_CALL)
    body = chat_body(request, ClientConfig(mode=Mode.TOOL_CALL))
    assert body["tools"][0]["function"]["parameters"] == request.schema
    assert "response_format" not in body


def test_free_text_carries_schema_in_the_prompt(context):
    section, matches, by_id = context
    request = build_request(section, matches, by_id, mode=Mode.FREE_TEXT)
    body = chat_body(request, ClientConfig(mode=Mode.FREE_TEXT))
    assert "response_format" not in body and "tools" not in body
    assert "pattern_id" in request.user


def test_thinking_switch_reaches_the_server(request_and_pattern, tmp_path):
    """Без него чемпион нерабочий вовсе, поэтому проверяется то, что УШЛО.

    23 751 знак рассуждения и пустой ответ — WORKLOG/2026-09-17-model-championship.
    """
    request, pattern = request_and_pattern
    with FakeModel(answer=_answer_for(pattern)) as fake:
        client = ModelClient(_config(fake, tmp_path))
        assert client.complete(request)
    sent = fake.requests[0]
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}
    assert sent["temperature"] == 0


def test_config_comments_do_not_leak_into_the_request(tmp_path):
    """Пояснения `_…` из конфига не должны попасть ни в тело, ни в ключ кэша."""
    path = tmp_path / "model.json"
    path.write_text(json.dumps({
        "access": "on",
        "params": {"_": "пояснение", "temperature": 0},
        "extra_body": {"_x": "пояснение", "chat_template_kwargs": {"enable_thinking": False}},
    }, ensure_ascii=False), encoding="utf-8")
    config = load_config(str(path))
    assert config.params == {"temperature": 0}
    assert config.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_missing_config_is_not_an_error(tmp_path):
    """Файла нет — работают встроенные значения, и это ВИДНО по `loaded`."""
    config = load_config(str(tmp_path / "нет-такого.json"))
    assert config.loaded is False
    assert config.access is Access.CACHE          # по умолчанию сети нет
    assert "127.0.0.1" in config.endpoint.base_url


def test_shipped_config_is_readable_and_offline_by_default():
    """Коммиченный `config/model.json` обязан читаться и не ходить в сеть."""
    config = load_config()
    assert config.loaded is True
    assert config.access is Access.CACHE
    assert "localhost" not in config.endpoint.base_url    # замер 6: вдвое дороже
    assert config.extra_body["chat_template_kwargs"]["enable_thinking"] is False


# --- что приходит назад ------------------------------------------------


def test_full_contract_from_request_to_fills(request_and_pattern, tmp_path):
    """Сквозной путь ADR-0010: запрос → транспорт → разбор → проверка → Fill."""
    request, pattern = request_and_pattern
    with FakeModel(answer=_answer_for(pattern)) as fake:
        answer = ModelClient(_config(fake, tmp_path)).complete(request)
    payload = extract_json(answer.text)
    assert check(payload, request, pattern) == []
    fills, fixes = repair(payload, request, pattern)
    assert fills and fixes == []


def test_answer_in_a_fence_still_works(request_and_pattern, tmp_path):
    """«Конечно! Вот результат:» вокруг ответа — не повод его терять."""
    request, pattern = request_and_pattern
    with FakeModel("в-заборе", answer=_answer_for(pattern)) as fake:
        answer = ModelClient(_config(fake, tmp_path)).complete(request)
    assert check(extract_json(answer.text), request, pattern) == []


def test_tool_call_arguments_are_read(context, tmp_path):
    section, matches, by_id = context
    pattern = by_id[matches[0].pattern_id]
    request = build_request(section, matches, by_id, mode=Mode.TOOL_CALL)
    with FakeModel("инструмент", answer=_answer_for(pattern)) as fake:
        answer = ModelClient(_config(fake, tmp_path)).complete(request)
    assert check(extract_json(answer.text), request, pattern) == []


def test_broken_json_is_not_an_exception(request_and_pattern, tmp_path):
    """Кривой JSON — свойство запроса, а не среды: следующую секцию пробуем."""
    request, _ = request_and_pattern
    with FakeModel("кривой-json") as fake:
        client = ModelClient(_config(fake, tmp_path))
        answer = client.complete(request)
    assert extract_json(answer.text) is None
    assert client.failure is None                 # среда исправна, не выключаемся


def test_empty_answer_names_thinking_as_the_cause(request_and_pattern, tmp_path):
    """Отказ измерителя — не приговор подопытному: ошибка обязана назвать причину.

    Ровно на этом чуть не была выброшена лучшая модель — CLAUDE.md.
    """
    request, _ = request_and_pattern
    with FakeModel("думание") as fake:
        client = ModelClient(_config(fake, tmp_path))
        answer = client.complete(request)
    assert answer.text is None
    assert "enable_thinking" in (answer.note or "")


def test_torn_response_falls_back_instead_of_crashing(request_and_pattern, tmp_path):
    """IncompleteRead — не наследник URLError (замер 5). Ловится отдельно."""
    request, _ = request_and_pattern
    with FakeModel("cut") as fake:
        client = ModelClient(_config(fake, tmp_path))
        answer = client.complete(request)
    assert answer.text is None and "оборв" in (answer.note or "")


def test_http_error_body_is_shown(request_and_pattern, tmp_path):
    """Сервер сказал, почему отказал, — незачем это скрывать."""
    request, _ = request_and_pattern
    with FakeModel("500") as fake:
        answer = ModelClient(_config(fake, tmp_path)).complete(request)
    assert "модель не загружена" in (answer.note or "")


def test_non_json_body_is_a_transport_error(request_and_pattern, tmp_path):
    request, _ = request_and_pattern
    with FakeModel("не-json-в-теле") as fake:
        answer = ModelClient(_config(fake, tmp_path)).complete(request)
    assert answer.text is None and "JSON" in (answer.note or "")


def test_timeout_is_respected(request_and_pattern, tmp_path):
    request, _ = request_and_pattern
    with FakeModel("slow", slow_sec=2.0) as fake:
        answer = ModelClient(_config(fake, tmp_path, timeout_sec=0.3)).complete(request)
    assert answer.text is None and "не ответила" in (answer.note or "")


def test_content_of_rejects_a_shapeless_answer():
    with pytest.raises(ModelError):
        content_of({"нет": "choices"})


# --- откат слышен ------------------------------------------------------


def test_first_network_failure_stops_the_rest(request_and_pattern, tmp_path):
    """Отказ подключения стоит 2.0 с (замер 6): девять секций — 18 с впустую."""
    request, _ = request_and_pattern
    with FakeModel() as fake:
        config = _config(fake, tmp_path)
    # сервер уже остановлен: адрес есть, слушать некому
    client = ModelClient(config)
    first = client.complete(request)
    second = client.complete(request)
    assert first.text is None and second.text is None
    assert client.failure is not None
    assert "уже отказала" in (second.note or "")
    assert any("не отвечает" in note for note in client.notes)


def test_budget_stops_the_run(request_and_pattern, tmp_path):
    """Щедрый таймаут без общего бюджета превращается в зависание.

    Часы подменены: первый вызов «съедает» 100 с при бюджете 50, и второй
    запрос — другой, в кэше его нет — до сети уже не доходит.
    """
    request, pattern = request_and_pattern
    other = replace(request, user=request.user + " ещё абзац")
    ticks = iter([0.0, 100.0] + [100.0] * 8)
    with FakeModel(answer=_answer_for(pattern)) as fake:
        client = ModelClient(_config(fake, tmp_path, budget_sec=50.0),
                             clock=lambda: next(ticks))
        assert client.complete(request).source == "model"
        sent = len(fake.requests)
        second = client.complete(other)
        assert len(fake.requests) == sent          # второго запроса не было
    assert second.text is None
    assert "бюджет" in (second.note or "")
    assert client.spent >= 50.0


def test_access_off_never_touches_cache_or_network(request_and_pattern, tmp_path):
    request, pattern = request_and_pattern
    with FakeModel(answer=_answer_for(pattern)) as fake:
        config = _config(fake, tmp_path)
        ModelClient(config).complete(request)      # наполнили кэш
        off = ModelClient(_config(fake, tmp_path, access=Access.OFF))
        assert off.cache_root == config.cache_root  # кэш тот же, и он не читается
        answer = off.complete(request)
    assert answer.text is None
    assert "выключена" in (answer.note or "")


def test_cache_mode_reads_but_does_not_call(request_and_pattern, tmp_path):
    """Режим по умолчанию: эксперт без видеокарты получает НАШ ответ."""
    request, pattern = request_and_pattern
    with FakeModel(answer=_answer_for(pattern)) as fake:
        filled = _config(fake, tmp_path)
        ModelClient(filled).complete(request)
        calls_before = len(fake.requests)
        reader = ModelClient(ClientConfig(
            access=Access.CACHE, endpoint=filled.endpoint, mode=filled.mode,
            params=filled.params, extra_body=filled.extra_body,
            cache_root=filled.cache_root, tz_cache_root=filled.tz_cache_root))
        answer = reader.complete(request)
        assert len(fake.requests) == calls_before   # сети не было
    assert answer.source == "cache"
    assert any("из кэша" in note for note in reader.notes)


def test_cache_miss_is_audible(request_and_pattern, tmp_path):
    """Молчаливый промах превратил бы «модели не было» в «модель плохая»."""
    request, _ = request_and_pattern
    client = ModelClient(ClientConfig(
        access=Access.CACHE,
        cache_root=str(tmp_path / "пусто"), tz_cache_root=str(tmp_path / "пусто-тз")))
    answer = client.complete(request)
    assert answer.text is None
    assert "нет в кэше" in (answer.note or "")
    assert any("В кэше нет ответа" in note for note in client.notes)


# --- кэш ---------------------------------------------------------------


def test_cache_key_is_stable_and_specific():
    base = dict(model="м", mode="json_schema", system="с", user="п",
                schema={"a": 1}, params={"temperature": 0}, extra_body={})
    key = cache.key_for(**base)
    assert key == cache.key_for(**{**base, "schema": {"a": 1}})
    assert key != cache.key_for(**{**base, "model": "другая"})
    assert key != cache.key_for(**{**base, "user": "другой"})
    assert key != cache.key_for(**{**base, "params": {"temperature": 0.7}})
    assert key != cache.key_for(**{**base, "extra_body": {"chat_template_kwargs": {}}})


def test_cache_file_is_byte_stable(tmp_path):
    """Детерминированность обязательна: тот же ответ — тот же файл."""
    root = str(tmp_path / "c")
    first = cache.write(root, "ключ", "ответ", model="м", mode="json_schema", label="s1")
    blob = open(first, "rb").read()
    cache.write(root, "ключ", "ответ", model="м", mode="json_schema", label="s1")
    assert open(first, "rb").read() == blob
    assert b"\r\n" not in blob                     # перевод строки один на всех платформах


def test_cache_ignores_a_file_from_another_key(tmp_path):
    root = str(tmp_path / "c")
    path = cache.write(root, "ключ", "ответ", model="м", mode="json_schema")
    payload = json.loads(open(path, encoding="utf-8").read())
    payload["key"] = "подменённый"
    open(path, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False))
    assert cache.read(root, "ключ") is None


def test_broken_cache_file_is_a_miss_not_a_crash(tmp_path):
    root = str(tmp_path / "c")
    os.makedirs(root, exist_ok=True)
    open(cache.path_for(root, "ключ"), "w", encoding="utf-8").write("{это не json")
    assert cache.read(root, "ключ") is None


# --- разделение кэша: данные ТЗ не утекают -----------------------------


def test_tz_input_picks_the_closed_cache():
    repo = cache.repo_root()
    inside = os.path.join(repo, "tz", "templates", "шаблон.pptx")
    outside = os.path.join(repo, "examples", "content-prose.md")
    assert cache.root_for([inside]).endswith(os.path.join("tz", "cache", "llm"))
    assert not cache.root_for([outside]).startswith(os.path.join(repo, "tz"))
    # Смешанный набор идёт в закрытый кэш: ошибаться надо в сторону молчания.
    assert cache.root_for([outside, inside]).endswith(os.path.join("tz", "cache", "llm"))


def test_tz_rule_survives_both_path_separators():
    """На Windows приходит `F:\\…\\tz\\templates\\x`, а проверка по `endswith`
    молча не сработала бы. В этом проекте так уже терялась правка."""
    repo = cache.repo_root()
    with_native = os.path.join(repo, "tz", "templates", "x.pptx")
    with_slashes = repo.replace(os.sep, "/") + "/tz/templates/x.pptx"
    assert cache.is_tz_path(with_native)
    assert cache.is_tz_path(with_slashes)
    assert not cache.is_tz_path(os.path.join(repo, "samples", "x.pptx"))


def test_a_name_that_merely_starts_with_tz_is_not_tz():
    repo = cache.repo_root()
    assert not cache.is_tz_path(os.path.join(repo, "tz-заметки", "x.pptx"))


@pytest.mark.skipif(shutil.which("git") is None, reason="git недоступен")
def test_git_ignores_the_closed_cache_and_keeps_the_open_one():
    """Проверка действием, и обязательно на ОБЕ стороны.

    Замер 7: `git check-ignore` на путь с завершающим слэшем отвечает
    «игнорируется» для чего угодно. Поэтому спрашиваем про ФАЙЛ. И проверяем обе
    стороны: тест только на `tz/` прошёл бы и в случае, когда игнорируется всё,
    то есть ровно тогда, когда коммиченный кэш молча исчез бы из репозитория.

    Это **не дубль** `test_docs.test_tz_derived_cache_can_never_be_committed`:
    тот проверяет, что правило записано в `.gitignore`, этот — что git ведёт
    себя так, как правило обещает. Разные вопросы, и второй ловит то, чего не
    видит первый: постороннее правило, накрывшее открытый кэш.
    """
    repo = cache.repo_root()
    if not os.path.isdir(os.path.join(repo, ".git")):
        pytest.skip("не репозиторий git")

    def ignored(rel: str) -> bool:
        done = subprocess.run(["git", "check-ignore", "-q", rel],
                              cwd=repo, capture_output=True)
        return done.returncode == 0

    assert ignored("tz/cache/llm/пример.json"), (
        "ответы модели по данным ТЗ обязаны быть исключены: п. 7.3.5 Положения")
    assert not ignored("cache/llm/пример.json"), (
        "коммиченный кэш обязан коммититься, иначе критерий 2 не выполнить")


def test_answer_is_falsy_when_there_is_none():
    assert not Answer(None, "none")
    assert Answer("{}", "cache")


def test_post_chat_reports_a_dead_address():
    with pytest.raises(ModelError) as exc:
        post_chat("http://127.0.0.1:9/v1/chat/completions", {"m": 1}, timeout=2)
    assert exc.value.kind in ("сеть", "таймаут")
