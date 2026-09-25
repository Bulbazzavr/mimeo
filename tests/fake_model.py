"""Поддельный сервер модели. Шаг Ш5 плана `PLAN-2.6`.

OpenAI-совместимая заглушка, которая умеет отвечать **плохо**. Живой модели для
проверки клиента не нужно, а нужного сорта поломки от живой модели не
дождёшься: обрыв посреди тела и пустой ответ «думающей» модели по заказу не
воспроизводятся.

Замер 5 (`WORKLOG/2026-09-17-llm-client.md`) показал, что вызов стоит 1.4 мс,
поэтому сценарии живут в обычных тестах, а не в отдельном медленном наборе.

Сценарий выбирается очередью: `FakeModel("ok", "cut", "500")` ответит на первый
запрос правильно, на второй оборвёт связь, на третий откажет. Когда очередь
кончилась, повторяется последний — иначе тест на повторные вызовы пришлось бы
расписывать поштучно.

Все запросы складываются в `.requests`: тест проверяет не только что вернулось,
но и **что ушло** — например, дошло ли отключение «думания».
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: Ответ, годный по контракту `ADR-0010`. Паттерн и слоты подставляет тест.
GOOD = {
    "pattern_id": "p1",
    "fills": [{"slot_id": "title", "kind": "text", "text": "Заголовок"}],
    "reason": "единственная подходящая раскладка",
}


def _envelope(content: str) -> dict:
    return {
        "id": "fake",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeModel/1"

    def log_message(self, *args):        # тесты не должны сорить в вывод
        pass

    @property
    def fake(self) -> FakeModel:
        return self.server.fake          # type: ignore[attr-defined]

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                    # noqa: N802
        if self.path.endswith("/models"):
            self._json(200, {"object": "list",
                             "data": [{"id": self.fake.model, "object": "model"}]})
            return
        self._json(404, {"error": "нет такого пути"})

    def do_POST(self):                   # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        self.fake.headers.append(dict(self.headers))
        try:
            self.fake.requests.append(json.loads(raw.decode("utf-8")))
        except ValueError:
            self.fake.requests.append({"_нечитаемое_тело": raw[:200].decode("utf-8", "replace")})

        step = self.fake.next_step()
        if callable(step):
            step = step(self.fake.requests[-1])

        if step == "500":
            self._json(500, {"error": {"message": "модель не загружена"}})
            return
        if step == "эхо-ключа":
            # Бывают серверы, повторяющие присланный ключ в тексте отказа.
            self._json(401, {"error": {"message": f"не принят {self.headers.get('Authorization')}"}})
            return
        if step == "slow":
            time.sleep(self.fake.slow_sec)
            step = "ok"
        if step == "cut":
            # Обещаем длинное тело и обрываем связь: `http.client` поднимет
            # IncompleteRead, а он НЕ наследник URLError (замер 5).
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "10000")
            self.end_headers()
            self.wfile.write(b'{"choices": [{"mes')
            self.wfile.flush()
            self.close_connection = True
            return
        if step == "не-json-в-теле":
            self.send_response(200)
            body = b"<html>502 Bad Gateway</html>"
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if step == "думание":
            payload = _envelope("")
            payload["choices"][0]["message"]["reasoning_content"] = "Хм. " * 6000
            self._json(200, payload)
            return
        if step == "пусто":
            self._json(200, _envelope("   "))
            return
        if step == "инструмент":
            payload = _envelope("")
            payload["choices"][0]["message"]["tool_calls"] = [
                {"id": "c1", "type": "function",
                 "function": {"name": "place_slide",
                              "arguments": json.dumps(self.fake.answer, ensure_ascii=False)}}
            ]
            self._json(200, payload)
            return
        if step == "кривой-json":
            self._json(200, _envelope('{"pattern_id": "p1", "fills": [  '))
            return
        if step == "в-заборе":
            self._json(200, _envelope(
                "Конечно! Вот результат:\n```json\n"
                + json.dumps(self.fake.answer, ensure_ascii=False)
                + "\n```\n"))
            return
        if isinstance(step, str) and step.startswith("текст:"):
            self._json(200, _envelope(step[len("текст:"):]))
            return
        self._json(200, _envelope(json.dumps(self.fake.answer, ensure_ascii=False)))


class FakeModel:
    """Сервер на случайном свободном порту. Используется как контекст-менеджер."""

    def __init__(self, *plan, answer: dict | None = None, model: str = "поддельная",
                 slow_sec: float = 3.0):
        self.plan = list(plan)
        self.answer = {**(answer or GOOD)}
        self.model = model
        self.slow_sec = slow_sec
        self.requests: list[dict] = []
        #: Заголовки каждого запроса — ради ключа API (`PLAN-9.0`, Ш2).
        self.headers: list[dict] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.fake = self         # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def next_step(self):
        if not self.plan:
            return "ok"
        return self.plan.pop(0) if len(self.plan) > 1 else self.plan[0]

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self) -> FakeModel:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
