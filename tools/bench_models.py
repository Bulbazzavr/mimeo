"""Сравнение локальных моделей на НАШЕЙ задаче: разметка ролей и зрение.

Заведён 17 сентября (`PLAN-2.6`, шаг 6). Числа последнего прогона —
`WORKLOG/2026-09-17-model-championship.md`.

Модели будут пересматриваться — к топ-10 обязателен инференс VK, — поэтому
замер живёт в репозитории, а не в черновиках: иначе `WORKLOG` ссылался бы на
то, что нечем повторить.

Замер намеренно устроен так, чтобы его можно было провалить:

ТЕКСТ — два эталона, проверяющие ПРОТИВОПОЛОЖНЫЕ ошибки.
  content-prose  — проблемы во входе НЕТ: нельзя выдумать.
  content-demo   — проблема ЕСТЬ и поставлена прямо: нельзя пропустить.

ЗРЕНИЕ — вопросы с проверяемым ответом, среди них пара «один и тот же слайд
до и после ремонта». Модель, отвечающая «да» на оба, просто угадывает.

Запуск:
    python tools/bench_models.py                       все модели из списка ниже
    python tools/bench_models.py --only Q6_K           одна
    python tools/bench_models.py --only 3.5 --no-think --maxtok-text 3000

**`--no-think` обязателен для «думающих» моделей.** Без него Qwen3.5-9B выдаёт
23 751 знак рассуждения и до ответа не доходит вовсе; `/no_think` в тексте
запроса при этом НЕ действует — проверено. Отключается только параметром
шаблона `chat_template_kwargs: {"enable_thinking": false}`.

Пути к серверу и весам — переменные окружения `MIMEO_LLAMA_SERVER` и
`MIMEO_MODELS_DIR`, по умолчанию машинные пути автора замера.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import subprocess
import time
import urllib.request

ROOT = r"F:\03_PROGRAMMY_NOV\46_PLAN_10\mimeo"
LLAMA = r"D:\0_model_llm\llama.cpp\llama-server.exe"
MODELS_DIR = r"D:\0_model_llm"
PORT = 8080

ROLES = ["обложка", "проблема", "решение", "продукт", "рынок", "тяга", "модель",
         "конкуренты", "финансы", "команда", "риски", "планы", "просьба", "прочее"]

MODELS = [
    ("VL-8B-Q8_0",  r"qwen\Qwen3-VL-8B-Instruct-GGUF\Qwen3VL-8B-Instruct-Q8_0.gguf",
                    r"qwen\Qwen3-VL-8B-Instruct-GGUF\mmproj-Qwen3VL-8B-Instruct-F16.gguf"),
    ("VL-8B-Q6_K",  r"lmstudio-community\Qwen3-VL-8B-Instruct-GGUF\Qwen3-VL-8B-Instruct-Q6_K.gguf",
                    r"lmstudio-community\Qwen3-VL-8B-Instruct-GGUF\mmproj-Qwen3-VL-8B-Instruct-F16.gguf"),
    ("VL-8B-Q4_K_M", r"qwen\Qwen3-VL-8B-Instruct-GGUF\Qwen3VL-8B-Instruct-Q4_K_M.gguf",
                    r"qwen\Qwen3-VL-8B-Instruct-GGUF\mmproj-Qwen3VL-8B-Instruct-F16.gguf"),
    ("VL-4B-Q8-lms", r"lmstudio-community\Qwen3-VL-4B-Instruct-GGUF\Qwen3-VL-4B-Instruct-Q8_0.gguf",
                    r"lmstudio-community\Qwen3-VL-4B-Instruct-GGUF\mmproj-Qwen3-VL-4B-Instruct-F16.gguf"),
    ("VL-4B-Q8-nexa", r"NexaAI\Qwen3-VL-4B-Instruct-GGUF\Qwen3VL-4B-Instruct-Q8_0.gguf",
                    r"NexaAI\Qwen3-VL-4B-Instruct-GGUF\mmproj-Qwen3VL-4B-Instruct-F16.gguf"),
    ("3.5-9B-Q6_K", r"lmstudio-community\Qwen3.5-9B-GGUF\Qwen3.5-9B-Q6_K.gguf",
                    r"lmstudio-community\Qwen3.5-9B-GGUF\mmproj-Qwen3.5-9B-BF16.gguf"),
    ("3-4B-2507-Q8", r"unsloth\Qwen3-4B-Instruct-2507-GGUF\Qwen3-4B-Instruct-2507-UD-Q8_K_XL.gguf",
                    None),
]

#: Зрение: слайды, которые я смотрел сам, и ответ мне известен.
VISION = [
    ("наложение-до",  r"tz\out\render\rev-plain\slide-03.png",
     "Перекрывает ли на этом слайде какой-нибудь текст другой текст, налезая на него?",
     {"type": "object", "properties": {"answer": {"enum": ["да", "нет"]}}, "required": ["answer"]},
     "answer", "да"),
    ("наложение-после", r"tz\out\render\rev-verified\slide-03.png",
     "Перекрывает ли на этом слайде какой-нибудь текст другой текст, налезая на него?",
     {"type": "object", "properties": {"answer": {"enum": ["да", "нет"]}}, "required": ["answer"]},
     "answer", "нет"),
    ("карточек-3",    r"tz\out\render\rev-ws-plain\slide-02.png",
     "Сколько прямоугольных карточек на этом слайде? Ответь числом.",
     {"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]},
     "count", 3),
    ("обрезано",      r"tz\out\render\rev-ws-plain\slide-02.png",
     "Есть ли на слайде заголовок, вторая строка которого обрезана или уходит под тёмный прямоугольник?",
     {"type": "object", "properties": {"answer": {"enum": ["да", "нет"]}}, "required": ["answer"]},
     "answer", "да"),
    ("карточек-4",    r"tz\out\render\src-vk-tech\slide-14.png",
     "Сколько прямоугольных карточек на этом слайде? Ответь числом.",
     {"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]},
     "count", 4),
]

SYSTEM_ROLES = (
    "Ты размечаешь смысловые роли фрагментов будущей презентации. "
    "Отвечай строго JSON, без пояснений.\n\n"
    "Роли, только из этого списка: " + ", ".join(ROLES) + ".\n\n"
    "Правила:\n"
    "1. Каждому фрагменту — ровно одна роль, самая точная.\n"
    "2. Ничего не придумывай: если какой-то роли в тексте нет, её просто нет. "
    "Перечисли все отсутствующие роли в поле missing_roles.\n"
    "3. Роль «прочее» — только если фрагмент не относится к содержанию вовсе."
)

SCHEMA_ROLES = {
    "type": "object",
    "properties": {
        "units": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "role": {"type": "string", "enum": ROLES}},
            "required": ["id", "role"]}},
        "missing_roles": {"type": "array", "items": {"type": "string", "enum": ROLES}},
    },
    "required": ["units", "missing_roles"],
}


def post(body, timeout=1200):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r), time.time() - t


def vram():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.free",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        used, free = (int(x) for x in out.split(","))
        return used, free
    except Exception:                                   # noqa: BLE001
        return -1, -1


def start(model, mmproj, ctx=8192):
    subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"],
                   capture_output=True, text=True)
    time.sleep(3)
    args = [LLAMA, "-m", os.path.join(MODELS_DIR, model), "-c", str(ctx),
            "-ngl", "99", "--host", "127.0.0.1", "--port", str(PORT)]
    if mmproj:
        args += ["--mmproj", os.path.join(MODELS_DIR, mmproj)]
    log = open(os.path.join(os.environ["TEMP"], "bench-server.log"), "wb")
    p = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(90):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=3) as r:
                if json.load(r).get("status") == "ok":
                    return p
        except Exception:                               # noqa: BLE001
            pass
        if p.poll() is not None:
            return None
        time.sleep(4)
    return None


MAXTOK = {"text": 1200, "vision": 120}
#: Думающие модели тратят тысячи токенов на рассуждение и до ответа не доходят.
#: Отключается ТОЛЬКО параметром шаблона: «/no_think» в тексте не действует.
EXTRA = {}


def score_roles(gold_path):
    gold = json.load(io.open(os.path.join(ROOT, gold_path), encoding="utf-8"))
    prose = io.open(os.path.join(ROOT, gold["content"]), encoding="utf-8").read()
    units = gold["units"]
    listing = "\n".join(f'{u["id"]}: {u["text"]}' for u in units)
    body = {
        "model": "bench", "temperature": 0, "max_tokens": MAXTOK["text"],
        "messages": [{"role": "system", "content": SYSTEM_ROLES},
                     {"role": "user", "content":
                      f"Исходный текст целиком:\n\n{prose}\n\n"
                      f"Фрагменты, которые надо разметить:\n\n{listing}"}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "roles", "schema": SCHEMA_ROLES, "strict": True}},
        **EXTRA,
    }
    data, sec = post(body)
    got = json.loads(data["choices"][0]["message"]["content"])
    by_id = {u["id"]: u["role"] for u in got.get("units", [])}
    # Объявленные эквивалентные роли: пары, различить которые эталон не даёт
    # правила. Применяются симметрично ко всем единицам (см. эталон).
    classes = [set(c) for c in gold.get("_эквивалентные_роли", [])]

    def same_class(a, b):
        return any({a, b} <= c for c in classes)

    strict = soft = 0
    for u in units:
        r = by_id.get(u["id"])
        if r == u["role"]:
            strict += 1; soft += 1
        elif r in (u.get("also") or []) or same_class(r, u["role"]):
            soft += 1
    said = set(got.get("missing_roles", []))
    want = set(gold["missing_roles"])
    return {
        "n": len(units), "strict": strict, "soft": soft, "sec": sec,
        "missing_hit": len(said & want), "missing_true": len(want),
        "missing_false": len(said - want),
        "by_id": by_id,
    }


def ask_image(path, question, schema, key, truth):
    b64 = base64.b64encode(open(os.path.join(ROOT, path), "rb").read()).decode()
    body = {
        "model": "bench", "temperature": 0, "max_tokens": MAXTOK["vision"],
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "v", "schema": schema, "strict": True}},
        **EXTRA,
    }
    data, sec = post(body)
    got = json.loads(data["choices"][0]["message"]["content"])
    return got.get(key), got.get(key) == truth, sec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--no-think", action="store_true")
    ap.add_argument("--maxtok-text", type=int, default=1200)
    ap.add_argument("--maxtok-vision", type=int, default=120)
    args = ap.parse_args()
    if args.no_think:
        EXTRA["chat_template_kwargs"] = {"enable_thinking": False}
    MAXTOK["text"] = args.maxtok_text
    MAXTOK["vision"] = args.maxtok_vision
    rows = []
    for label, model, mmproj in MODELS:
        if args.only and args.only not in label:
            continue
        print(f"\n{'='*70}\n### {label}", flush=True)
        if not os.path.exists(os.path.join(MODELS_DIR, model)):
            print("   файла нет, пропуск"); continue
        p = start(model, mmproj)
        if p is None:
            print("   НЕ ЗАПУСТИЛАСЬ"); rows.append((label, None)); continue
        used, free = vram()
        print(f"   VRAM: занято {used} МиБ, свободно {free} МиБ", flush=True)
        row = {"label": label, "used": used, "free": free, "vision": [], "text": {}}
        for gold, name in (("examples/content-prose.roles.json", "prose"),
                           ("examples/content-demo.roles.json", "demo")):
            try:
                r = score_roles(gold)
                row["text"][name] = r
                print(f"   текст {name}: строго {r['strict']}/{r['n']} "
                      f"({100*r['strict']/r['n']:.0f}%), мягко {r['soft']}/{r['n']} "
                      f"({100*r['soft']/r['n']:.0f}%), пропуски {r['missing_hit']}/{r['missing_true']} "
                      f"(лишних {r['missing_false']}), {r['sec']:.1f} с", flush=True)
            except Exception as exc:                    # noqa: BLE001
                print(f"   текст {name}: ОТКАЗ — {type(exc).__name__}: {exc}", flush=True)
        if mmproj:
            for name, path, q, schema, key, truth in VISION:
                try:
                    val, ok, sec = ask_image(path, q, schema, key, truth)
                    row["vision"].append((name, val, ok, sec))
                    print(f"   зрение {name:16} → {val!r} {'✓' if ok else '✗ (надо ' + repr(truth) + ')'}"
                          f"  {sec:.1f} с", flush=True)
                except Exception as exc:                # noqa: BLE001
                    row["vision"].append((name, None, False, 0))
                    print(f"   зрение {name:16} → ОТКАЗ {type(exc).__name__}", flush=True)
        else:
            print("   зрения нет: проектор не поставлялся", flush=True)
        rows.append((label, row))
        p.terminate()
    subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True)

    out = os.path.join(os.environ["TEMP"], "bench-result.json")
    json.dump([r for _, r in rows if r], io.open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("\nсырое:", out)


if __name__ == "__main__":
    main()
