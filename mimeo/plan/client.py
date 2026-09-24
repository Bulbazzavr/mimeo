"""Клиент к языковой модели. Шаги Ш1, Ш2, Ш4 плана `PLAN-2.6`, задача `Z-07`.

Единственное место в продукте, где есть сеть, и только в стадии PLAN
(`ADR-0002`). Стандартная библиотека, ноль зависимостей (`ADR-0001`).

Что здесь есть и чего нет. Здесь — **труба**: взять `Request` из `prompt.py`,
отправить одним из трёх режимов `ADR-0010`, вернуть строку. Здесь **нет**
формулировок (они в `prompt.py`), проверки ответа (она в `validate.py`) и
решений о том, что куда влезет (это код, `ADR-0009`).

**По умолчанию сети нет вовсе** (`ADR-0021`). Режим `cache` читает коммиченные
ответы и никуда не ходит: эксперт без видеокарты обязан получить наш результат,
а не тихо другой. Сеть включает только `on`.

**Отказ обязан быть слышен.** Модель не ответила или ответа нет в кэше — план
собирается как раньше, но `notes` возвращает строку об этом. Молчаливый откат
превратил бы «модель не работала» в «модель работала плохо», и мы бы этого не
заметили (`PLAN-2.6`, шаг 4).

Числа, на которых стоит устройство модуля, — `WORKLOG/2026-09-17-llm-client.md`.
"""

from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum

from . import cache
from .prompt import Mode, Request


class Access(str, Enum):
    """Что клиенту разрешено. Порядок — от закрытого к открытому."""

    OFF = "off"       # кэш не читается, сети нет: чистый детерминированный путь
    CACHE = "cache"   # кэш читается, сети нет — по умолчанию
    ON = "on"         # при промахе зовём модель и пополняем кэш


@dataclass(frozen=True)
class Endpoint:
    """Куда и как стучаться. Значения по умолчанию — из замеров, не с потолка."""

    #: 127.0.0.1, а не localhost: отказ подключения стоит 2.0 с против 4.0 с
    #: (замер 6). Причина в Windows, а не в urllib — голый сокет даёт то же.
    #: Те же значения, что в `config/model.json`: без файла поведение не должно
    #: молча меняться. Порт 8080 — `llama-server`, другой среды у нас нет.
    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "gemma-4-12b-it-qat-q4_0"
    #: Щедрый и на ОДИН вызов: колода строится за 27–30 с (`ADR-0023`), запас —
    #: под машину медленнее.
    timeout_sec: float = 180.0
    #: Потолок на ВЕСЬ прогон: без него щедрый таймаут превращается в
    #: зависание, а ТЗ даёт 300 с на колоду.
    budget_sec: float = 240.0
    #: Ответ-колода — до 1446 токенов (замер 24 сентября); 1200 его обрезали бы.
    max_tokens: int = 4000
    context_tokens: int = 8192

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def models_url(self) -> str:
        return self.base_url.rstrip("/") + "/models"


@dataclass(frozen=True)
class ClientConfig:
    """Настройки доступа. `loaded` отличает «конфиг прочитан» от «работают
    встроенные значения» — правило `CLAUDE.md` про измеритель, который обязан
    отличать «проверено и чисто» от «проверить не смог»."""

    access: Access = Access.CACHE
    endpoint: Endpoint = field(default_factory=Endpoint)
    mode: Mode = Mode.JSON_SCHEMA
    params: dict = field(default_factory=lambda: {"temperature": 0, "seed": 0})
    #: То, чего нет в OpenAI-интерфейсе, но без чего конкретная модель не
    #: работает. У чемпиона это отключение «думания»: без него 23 751 знак
    #: рассуждения и пустой ответ.
    extra_body: dict = field(default_factory=dict)
    cache_root: str = "cache/llm"
    tz_cache_root: str = "tz/cache/llm"
    loaded: bool = False
    source: str = "встроенные значения"


@dataclass(frozen=True)
class Answer:
    """Что вернулось. `source` отвечает на вопрос «откуда», а не «получилось ли»."""

    text: str | None
    source: str            # "cache" | "model" | "none"
    note: str | None = None
    elapsed: float = 0.0

    def __bool__(self) -> bool:
        return self.text is not None


class ModelError(RuntimeError):
    """Транспорт не смог. `kind` — что именно случилось, для диагностики."""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def config_path() -> str:
    return os.path.join(cache.repo_root(), "config", "model.json")


def _strip_comments(value):
    """Выкидывает ключи-комментарии `_…` на всех уровнях.

    В конфиге пояснения лежат рядом со значениями — так их читают. Но `params` и
    `extra_body` уходят **в тело запроса и в ключ кэша**: комментарий, попавший
    туда, изменил бы ключ и мог бы быть отвергнут сервером.
    """
    if isinstance(value, dict):
        return {k: _strip_comments(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, list):
        return [_strip_comments(v) for v in value]
    return value


def load_config(path: str | None = None) -> ClientConfig:
    """Читает `config/model.json`. Отсутствие файла — не ошибка."""
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return ClientConfig()
    if not isinstance(raw, dict):
        return ClientConfig()

    default = ClientConfig()
    ep = _strip_comments(raw.get("endpoint") or {})
    endpoint = Endpoint(
        base_url=str(ep.get("base_url") or default.endpoint.base_url),
        model=str(ep.get("model") or default.endpoint.model),
        timeout_sec=float(ep.get("timeout_sec") or default.endpoint.timeout_sec),
        budget_sec=float(ep.get("budget_sec") or default.endpoint.budget_sec),
        max_tokens=int(ep.get("max_tokens") or default.endpoint.max_tokens),
        context_tokens=int(ep.get("context_tokens") or default.endpoint.context_tokens),
    )
    store = _strip_comments(raw.get("cache") or {})
    return ClientConfig(
        access=_enum(Access, raw.get("access"), default.access),
        endpoint=endpoint,
        mode=_enum(Mode, raw.get("contract"), default.mode),
        params=_strip_comments(raw.get("params") or default.params),
        extra_body=_strip_comments(raw.get("extra_body") or {}),
        cache_root=str(store.get("root") or default.cache_root),
        tz_cache_root=str(store.get("tz_root") or default.tz_cache_root),
        loaded=True,
        source=path,
    )


def _enum(cls, value, fallback):
    try:
        return cls(str(value))
    except ValueError:
        return fallback


def _short(path: str) -> str:
    """Путь покороче — для строки диагностики.

    `relpath` падает `ValueError`, когда пути на разных дисках: кэш в `C:\\Temp`,
    репозиторий на `F:`. Падать здесь нельзя вдвойне — это тот самый механизм,
    которым откат становится слышен.
    """
    try:
        return os.path.relpath(path, cache.repo_root())
    except ValueError:
        return path


def chat_body(request: Request, config: ClientConfig) -> dict:
    """Тело запроса. Режим решает, куда лечь схеме (`ADR-0010`).

    Схема уезжает по-разному, а проверяется ответ одинаково во всех трёх
    режимах: доверять режиму нельзя ни в одном.
    """
    body: dict = {
        "model": config.endpoint.model,
        "messages": request.as_messages(),
        "max_tokens": config.endpoint.max_tokens,
        "stream": False,
    }
    body.update(config.params)
    if request.mode is Mode.JSON_SCHEMA:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "slide_plan", "schema": request.schema, "strict": True},
        }
    elif request.mode is Mode.TOOL_CALL:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "place_slide",
                    "description": "Разложить кусок контента по слотам выбранной раскладки.",
                    "parameters": request.schema,
                },
            }
        ]
        body["tool_choice"] = {"type": "function", "function": {"name": "place_slide"}}
    # FREE_TEXT: схема уже вписана в текст запроса самим `build_request`.
    body.update(config.extra_body)
    return body


def content_of(data: dict) -> str:
    """Достаёт текст ответа. Пустота — это ошибка, а не пустой ответ.

    Пустой `content` при непустом рассуждении — известный отказ «думающей»
    модели: чемпион без `enable_thinking: false` выдаёт 23 751 знак рассуждения
    и до ответа не доходит. Ошибка называет причину, а не молчит.
    """
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelError("разбор", f"в ответе нет choices[0].message: {data!r:.200}") from exc

    text = message.get("content")
    if isinstance(text, str) and text.strip():
        return text

    calls = message.get("tool_calls") or []
    if calls:
        args = calls[0].get("function", {}).get("arguments")
        if isinstance(args, str) and args.strip():
            return args

    thinking = message.get("reasoning_content") or message.get("reasoning") or ""
    if thinking:
        raise ModelError(
            "думание",
            f"модель вернула {len(thinking)} знаков рассуждения и пустой ответ: "
            "в extra_body нужен chat_template_kwargs.enable_thinking = false "
            "(приём «/no_think» в тексте не действует)",
        )
    raise ModelError("пусто", "модель вернула пустой ответ")


def post_chat(url: str, body: dict, timeout: float) -> dict:
    """Один запрос. Всё, чем транспорт может отказать, превращается в `ModelError`.

    Перечень исключений задан замером 5, а не догадкой. Отдельно про
    `IncompleteRead`: он наследуется от `HTTPException`, **а не от `URLError`**,
    и обрыв ответа прошёл бы мимо `except urllib.error.URLError`, уронив сборку
    вместо отката.
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        # Чтение тела — попытка, а не обязанность: сервер сказал, почему
        # отказал, и это стоит показать. Но если тело не читается, теряться
        # должно ОНО, а не сам отказ, поэтому исключение здесь глотается молча.
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:                            # noqa: BLE001, S110 — см. выше
            pass
        raise ModelError("http", f"сервер ответил {exc.code} {exc.reason}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ModelError("сеть", f"нет связи с {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ModelError("таймаут", f"модель не ответила за {timeout:.0f} с") from exc
    except http.client.IncompleteRead as exc:
        raise ModelError("обрыв", f"ответ оборвался, прочитано {len(exc.partial)} байт") from exc
    except http.client.HTTPException as exc:
        raise ModelError("обрыв", f"соединение разорвано: {type(exc).__name__}") from exc
    except ValueError as exc:
        raise ModelError("разбор", f"ответ сервера не разобрался как JSON: {exc}") from exc
    except OSError as exc:
        raise ModelError("сеть", f"ошибка ввода-вывода: {exc}") from exc


class ModelClient:
    """Кэш, сеть и бюджет вокруг одного запроса.

    Живёт один прогон. Помнит три вещи, каждая из замера:

    * **первая неудача выключает остальные вызовы.** Отказ подключения стоит
      2.0 с (замер 6), секций 7–9 — это 18 с впустую на колоду;
    * **бюджет прогона.** Щедрый таймаут на девяти секциях иначе превращается в
      зависание при потолке ТЗ в 300 с;
    * **счётчики.** Сколько взято из кэша, сколько промахнулось, сколько
      пропущено, — чтобы `notes` отличал «модели не было» от «модель ответила».
    """

    def __init__(self, config: ClientConfig | None = None, inputs=(), clock=time.monotonic):
        self.config = config or ClientConfig()
        self.cache_root = cache.root_for(
            inputs, self.config.cache_root, self.config.tz_cache_root
        )
        self._clock = clock
        self._spent = 0.0
        self.hits = 0
        self.misses = 0
        self.calls = 0
        self.written = 0
        self.skipped = 0
        self.failure: ModelError | None = None

    @property
    def spent(self) -> float:
        return self._spent

    def key(self, request: Request) -> str:
        return cache.key_for(
            model=self.config.endpoint.model,
            mode=request.mode.value,
            system=request.system,
            user=request.user,
            schema=request.schema,
            params=self.config.params,
            extra_body=self.config.extra_body,
        )

    def complete(self, request: Request) -> Answer:
        """Ответ на один запрос: из кэша, от модели или ниоткуда.

        «Ниоткуда» — не исключение и не молчание: `Answer.text` равен `None`, а
        `note` говорит, почему. Вызывающий кладёт `notes` в диагностику плана.
        """
        access = self.config.access
        if access is Access.OFF:
            self.skipped += 1
            return Answer(None, "none", "модель выключена (access = off)")

        key = self.key(request)
        hit = cache.read(self.cache_root, key)
        if hit is not None:
            self.hits += 1
            return Answer(hit, "cache")
        self.misses += 1

        if access is not Access.ON:
            return Answer(
                None,
                "none",
                f"ответа на запрос {key[:12]} нет в кэше, а сеть выключена (access = cache)",
            )
        if self.failure is not None:
            self.skipped += 1
            return Answer(None, "none", f"модель уже отказала: {self.failure.detail}")
        left = self.config.endpoint.budget_sec - self._spent
        if left <= 0:
            self.skipped += 1
            return Answer(None, "none", "бюджет прогона на обращения к модели исчерпан")

        started = self._clock()
        try:
            data = post_chat(
                self.config.endpoint.chat_url,
                chat_body(request, self.config),
                timeout=min(self.config.endpoint.timeout_sec, left),
            )
            text = content_of(data)
        except ModelError as exc:
            self._spent += self._clock() - started
            # Сеть и таймаут выключают остальные вызовы: сервера нет, и каждая
            # следующая попытка стоит те же секунды. Отказ по содержанию
            # (пустой ответ, кривой JSON) — свойство запроса, а не среды, и
            # следующую секцию пробовать стоит.
            if exc.kind in ("сеть", "таймаут"):
                self.failure = exc
            return Answer(None, "none", f"модель не ответила ({exc.kind}): {exc.detail}", 0.0)

        elapsed = self._clock() - started
        self._spent += elapsed
        self.calls += 1
        cache.write(
            self.cache_root,
            key,
            text,
            model=self.config.endpoint.model,
            mode=request.mode.value,
            label=request.section_id,
        )
        self.written += 1
        return Answer(text, "model", None, elapsed)

    @property
    def notes(self) -> tuple[str, ...]:
        """Строки для `diagnostics.warnings`. Пусто — значит сказать нечего.

        Различает «взято из кэша» и «модели не было»: измеритель обязан
        отличать «проверено и чисто» от «проверить не смог».
        """
        out: list[str] = []
        if self.hits:
            out.append(f"Ответов модели взято из кэша: {self.hits} ({_short(self.cache_root)}).")
        if self.calls:
            out.append(
                f"Модель вызвана {self.calls} раз, {self._spent:.1f} с; "
                f"записано в кэш: {self.written}."
            )
        if self.misses and not self.calls and self.config.access is Access.CACHE:
            out.append(
                f"В кэше нет ответа на {self.misses} запросов, сеть выключена: "
                "план собран детерминированным путём."
            )
        if self.failure is not None:
            out.append(
                f"Модель не отвечает ({self.failure.kind}: {self.failure.detail}). "
                f"Запросов ушло на детерминированный путь: {self.skipped + 1}."
            )
        if self.skipped and self.failure is None and self.config.access is Access.OFF:
            out.append(f"Модель выключена, запросов не отправлено: {self.skipped}.")
        return tuple(out)
