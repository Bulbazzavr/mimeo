"""Готова ли модель отвечать по нашему контракту. Шаг Ш6 плана `PLAN-2.6`.

Отвечает на вопрос **«готова ли модель»**, а не «отвечает ли сервер», и разница
не теоретическая. 17 сентября первый опрос LM Studio показал загруженную модель,
второй через минуту — ноль загруженных: сервер грузит по требованию и выгружает
по простою (`WORKLOG/2026-09-17-llm-client.md`, замер 2). Проба, смотрящая
только на каталог `/v1/models`, соврала бы.

Поэтому каталог читается ради подсказки имени, а вердикт выносится **настоящим
вызовом** по нашему контракту.

    python tools/llm_probe.py                     адрес и модель из config/model.json
    python tools/llm_probe.py --model qwen3.5-9b  перебить имя модели
    python tools/llm_probe.py --url http://127.0.0.1:8080/v1
    python tools/llm_probe.py --list              только каталог, без вызова

Код возврата: 0 — модель ответила по контракту, 1 — нет. Годится для README и
для проверки перед пополнением кэша.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mimeo.plan.client import (  # noqa: E402
    ClientConfig,
    ModelError,
    chat_body,
    content_of,
    load_config,
    post_chat,
)
from mimeo.plan.prompt import Mode, Request  # noqa: E402

#: Запрос ровно по нашему контракту, но крошечный: проверяем готовность, а не
#: качество. Схема — та же по устройству, что в `prompt.RESPONSE_SCHEMA`.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pattern_id", "fills"],
    "properties": {
        "pattern_id": {"type": "string"},
        "fills": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["slot_id", "kind", "text"],
                "properties": {
                    "slot_id": {"type": "string"},
                    "kind": {"type": "string", "enum": ["text"]},
                    "text": {"type": "string"},
                },
            },
        },
    },
}

PROBE = Request(
    mode=Mode.JSON_SCHEMA,
    system=("Ты раскладываешь контент по слайдам. Отвечай только JSON по схеме, "
            "без пояснений."),
    user=json.dumps(
        {
            "content": {"heading": "Проверка связи", "blocks": [{"kind": "text",
                                                                 "text": "Раз, два, три."}]},
            "patterns": [{"pattern_id": "p1", "kind": "title",
                          "slots": [{"slot_id": "s1", "role": "title", "kind": "text",
                                     "max_chars": 40}]}],
        },
        ensure_ascii=False,
        indent=1,
    ),
    schema=SCHEMA,
    section_id="проба",
    candidates=("p1",),
)


def catalogue(config: ClientConfig, timeout: float = 10.0) -> list[str] | None:
    """Имена моделей у сервера. `None` — сервер не ответил вовсе."""
    try:
        with urllib.request.urlopen(config.endpoint.models_url, timeout=timeout) as response:
            data = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    names = [str(m.get("id")) for m in (data.get("data") or []) if m.get("id")]
    return names


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="адрес вида http://127.0.0.1:1234/v1")
    ap.add_argument("--model", help="имя модели у сервера")
    ap.add_argument("--list", action="store_true", help="только каталог, без вызова")
    ap.add_argument("--timeout", type=float, help="таймаут одного вызова, с")
    args = ap.parse_args()

    config = load_config()
    print(f"конфиг    {config.source}" + ("" if config.loaded else "  (файл не прочитан!)"))
    endpoint = config.endpoint
    if args.url:
        endpoint = replace(endpoint, base_url=args.url)
    if args.model:
        endpoint = replace(endpoint, model=args.model)
    if args.timeout:
        endpoint = replace(endpoint, timeout_sec=args.timeout)
    config = replace(config, endpoint=endpoint)
    print(f"адрес     {endpoint.base_url}")
    print(f"модель    {endpoint.model}")
    print(f"режим     {config.mode.value}, доступ {config.access.value}")

    names = catalogue(config)
    if names is None:
        print("каталог   сервер не отвечает")
        print("\nВЕРДИКТ: сервера нет. Поднять llama-server или LM Studio и повторить.")
        return 1
    print(f"каталог   моделей {len(names)}")
    for name in names[:10]:
        mark = " ←" if name == endpoint.model else ""
        print(f"          {name}{mark}")
    if len(names) > 10:
        print(f"          … ещё {len(names) - 10}")
    if endpoint.model not in names:
        print(f"ВНИМАНИЕ  имени «{endpoint.model}» в каталоге нет: сервер может отказать "
              "или подставить свою модель")

    if args.list:
        print("\nКаталог прочитан. Это НЕ значит, что модель готова отвечать: "
              "сервер грузит её по требованию. Вердикт даёт вызов без --list.")
        return 0

    print("\nвызов по контракту…", flush=True)
    started = time.monotonic()
    try:
        data = post_chat(endpoint.chat_url, chat_body(PROBE, config),
                         timeout=endpoint.timeout_sec)
        text = content_of(data)
    except ModelError as exc:
        print(f"ОТКАЗ     {exc.kind}: {exc.detail}")
        print(f"\nВЕРДИКТ: модель не готова, {time.monotonic() - started:.1f} с.")
        return 1
    elapsed = time.monotonic() - started

    usage = data.get("usage") or {}
    print(f"ответ     {len(text)} знаков за {elapsed:.1f} с, "
          f"токенов {usage.get('completion_tokens', '?')}")
    try:
        parsed = json.loads(text)
    except ValueError:
        print(f"ответ не разобрался как JSON: {text[:200]!r}")
        print("\nВЕРДИКТ: модель отвечает, но строгий JSON не держит — "
              "это режим free_text, а не json_schema.")
        return 1
    ok = parsed.get("pattern_id") == "p1" and isinstance(parsed.get("fills"), list)
    print(f"строгий JSON: да, pattern_id={parsed.get('pattern_id')!r}")
    print(f"\nВЕРДИКТ: модель готова{'' if ok else ', но ответ не по контракту'}, "
          f"{elapsed:.1f} с на пробу.")
    # Умножать это время на число секций нельзя: проба намеренно крошечная
    # (~220 токенов), а настоящий запрос доходит до 15 тыс. знаков — замер 3.
    # Считать намерение вместо результата здесь особенно легко: число выйдет
    # красивое и неверное.
    print(f"Это НЕ оценка колоды: в пробе {usage.get('prompt_tokens', '?')} токенов "
          "запроса, а настоящий доходит до 15 тыс. знаков. Цену колоды мерит "
          "`python tools/llm_cache.py fill <шаблон> <контент>`.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
