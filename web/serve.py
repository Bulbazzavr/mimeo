"""Локальный веб-интерфейс к движку. Задача `Z-29`, план `PLAN-8.0`.

    python web/serve.py

**Зависимостей ноль, и это не поза.** `SPEC-WEB` разрешает веб-слою свой стек,
и для будущего публичного стенда это остаётся в силе. Здесь выбрано иначе:
локальный интерфейс запускает **эксперт из сданного репозитория**, а каждая
зависимость — шаг, на котором чужой сетап ломается. У движка зависимостей ноль
(`ADR-0001`), и весь рассказ о продукте на этом стоит; веб, требующий
`pip install`, противоречил бы собственному питчу.

**Движок зовётся только через командную строку.** `import mimeo` здесь
запрещён и **проверяется тестом** (`tests/test_web_boundary.py`), а не
обещанием: иначе граница продержится до первого неудобства. Побочная польза
названа в самой спецификации — так веб заодно проверяет, что продукт пригоден
для встраивания. Если интерфейса неудобно построить, плох CLI, и чинить надо
CLI.

**Слушаем только `127.0.0.1`.** Локальный значит локальный: ни аутентификации,
ни защиты от чужого здесь нет, и выставлять это наружу нельзя. Правила
`SPEC-WEB` про публичный стенд (пароль, выключенный `--verify`) к этому файлу
не относятся — стенда мы не поднимаем.
"""

from __future__ import annotations

import argparse
import glob
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
#: Корень репозитория. Движок зовётся отсюда, чтобы `python -m mimeo` его нашёл
#: и чтобы пути вроде `examples/img/schema.png`, названные в тексте прозой,
#: разрешались так же, как из консоли.
ROOT = os.path.dirname(HERE)
STATIC = os.path.join(HERE, "static")
#: Каталоги прогонов. Под `out/`, который уже в `.gitignore`.
RUNS_DIR = os.path.join(ROOT, "out", "web")

#: Что принимаем на вход. Всё остальное отвергаем **до** записи на диск.
ALLOWED_TEMPLATE = (".pptx", ".potx")
#: Потолок на шаблон. Самый тяжёлый в нашем корпусе — 25 МБ; сто даёт запас и
#: не даёт забить диск одной командой.
MAX_TEMPLATE_BYTES = 100 * 1024 * 1024
#: Потолок на текст. Основной вход корпуса — 1651 знак; миллион это заведомо
#: больше всего разумного и заведомо меньше того, чем можно навредить.
MAX_TEXT_CHARS = 1_000_000
#: Картинки контент-пакета (`Z-73`, ТЗ, раздел 2, п. 1: «импорт… контент-
#: пакетов»). Расширения — те, по которым движок узнаёт картинку в прозе
#: (`plan/content.py`, `_IMAGE_SUFFIXES`); продублированы, потому что
#: `import mimeo` здесь запрещён.
ALLOWED_IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGES = 30
#: Путь к картинке внутри прозы — тот же признак, что у движка
#: (`plan/content.py`, `_PATH_IN_PROSE`). Знаки, которые его обрывают, из имени
#: загруженного файла заменяются подчёркиванием: иначе имя «схема 1.png»
#: движок в тексте не узнал бы.
_PATH_IN_PROSE = re.compile(r"""[^\s,;:()«»"'\[\]]+\.(?:png|jpe?g|gif|bmp|webp)""", re.IGNORECASE)
_NAME_BREAKERS = re.compile(r"""[\s,;:()«»"'\[\]]+""")


def safe_image_name(name: str) -> str:
    """Имя загруженной картинки, которое движок узнает в тексте. То же правило
    — в `app.js`, `safeName`: строка «Приложенные картинки» пишется там."""
    return _NAME_BREAKERS.sub("_", os.path.basename(name).strip())


def place_uploads(text: str, run_dir: str, uploads: list[str]) -> list[str]:
    """Картинка, названная в тексте путём с каталогами (`img/схема.png`), а
    загруженная файлом `схема.png`, копируется по этому пути внутри прогона:
    движок ищет её от каталога текста (`plan/content.py`, `resolve_image`).
    Путь наружу прогона не выходит — абсолютный и с `..` пропускаются.
    Возвращает положенные пути."""
    placed = []
    root = os.path.abspath(run_dir)
    for ref in dict.fromkeys(_PATH_IN_PROSE.findall(text)):
        base = ref.replace("\\", "/").rsplit("/", 1)[-1]
        if base == ref or base not in uploads or os.path.isabs(ref) or ":" in ref:
            continue
        target = os.path.abspath(os.path.join(root, ref))
        if not target.startswith(root + os.sep):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(root, base), target)
        placed.append(ref)
    return placed
#: Сколько вариантов вёрстки можно попросить. Потолок — **число политик ранга**
#: в `config/variants.json`: больше колод движку просто не из чего породить.
#: Замер 22 сентября: при 27 запрошенных реально отбирается 9 на VK Tech, 5 на
#: WorkSpace, 6 на VK Education — попарное расхождение в 30% выдерживают не все.
#: Движок называет причину словами, и она доезжает до отчёта.
#:
#: Читаем из конфига, а не пишем числом: конфиг правят, и разойтись им нельзя.
MAX_VARIANTS = 27

#: Потолок ТЗ — пять минут на колоду. Берём вдвое: три варианта с проверкой
#: вёрстки идут дольше одной, а висеть вечно нельзя.
BUILD_TIMEOUT_SECONDS = 600

#: **PowerPoint — исключительный ресурс на весь продукт, и это не наше
#: ограничение.** Приложение одноэкземплярное: `New-Object` подключается к уже
#: открытому экземпляру, а `Quit()` закрыл бы документы пользователя. Поэтому
#: оба зонда сторожат вход и выходят с кодом 3, увидев чужой `POWERPNT`:
#: `tools/com_probe.ps1` — `ABORTED_USER_INSTANCE_RUNNING`,
#: `mimeo/verify/powerpoint_probe.ps1` — `result=BUSY`.
#:
#: Замер столкновением 22 сентября: два растровых прогона с разницей 1.2 с дают
#: первый 4.5 с и код 0, второй **0.3 с и код 3**.
#:
#: Замок упорядочивает **наши** обращения. От открытого руками PowerPoint он не
#: спасает — от этого спасает только внятное сообщение (`PLAN-8.2`, логическая
#: проверка 1, дыра 1).
_POWERPOINT = threading.Lock()

#: Ширина растра превью. Замер: время от неё почти не зависит — 13 слайдов дают
#: 4.3 с при 400, 3.5 с при 800 и 5.1 с при 1280, и разброс это шум запуска
#: приложения. Значит мельчить незачем: платим только памятью браузера, около
#: 2.8 МБ на колоду из тринадцати слайдов. В ленте миниатюры уменьшаются
#: стилями, а клик открывает картинку целиком.
PREVIEW_WIDTH = 900

#: Прогоны: токен -> что мы о нём знаем. Ключи выдаёт сервер, клиент их только
#: возвращает. **Путь от страницы не принимается никогда** — это и есть защита
#: от обхода каталога: снаружи ходят токен и номер, путь живёт здесь.
_RUNS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _new_run() -> tuple[str, str]:
    token = secrets.token_urlsafe(16)
    path = os.path.join(RUNS_DIR, token)
    os.makedirs(path, exist_ok=True)
    with _LOCK:
        _RUNS[token] = {"token": token, "dir": path, "template": None,
                        "decks": [], "previews": {}}
    return token, path


def _run(token: str) -> dict | None:
    with _LOCK:
        return _RUNS.get(token)


def _engine(args: list[str]) -> subprocess.CompletedProcess:
    """Зовёт движок командной строкой.

    **Кодировка задана явно, и это не перестраховка.** Машина русская, локаль
    `cp1251`, и на этом уже спотыкались: `tools/render_probe.py` несёт про это
    комментарий, потому что путь к выданному шаблону содержит кириллицу.
    Без `encoding="utf-8"` вывод падает на первом же таком пути.

    `sys.executable`, а не `python`: в `PATH` может стоять другой интерпретатор,
    и тогда движок запустится не тем, чем его тестировали.
    """
    return subprocess.run(
        [sys.executable, "-m", "mimeo", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=BUILD_TIMEOUT_SECONDS,
    )


def _load_max_variants() -> int:
    """Потолок из `config/variants.json`, а не из головы.

    Единственное место, где веб заглядывает в файл движка, и заглядывает он в
    **настройку**, а не в код: `ADR-0022` для того настройки и вынес наружу.
    Файла нет или он сломан — остаётся встроенное число, и это «не смогли
    прочесть», а не «столько и есть».
    """
    try:
        with open(os.path.join(ROOT, "config", "variants.json"), encoding="utf-8") as fh:
            policies = json.load(fh).get("policies")
        return len(policies) if policies else MAX_VARIANTS
    except (OSError, ValueError, TypeError):
        return MAX_VARIANTS


#: EMU в дюйм. Число из спецификации OOXML, а не из движка: тянуть ради него
#: `mimeo.oxml.units` значило бы сломать границу слоя ради одной константы
#: (`SPEC-WEB`, раздел 2).
_EMU_PER_INCH = 914400

def _inch(emu):
    """EMU в дюймы, округлённо. `None` остаётся `None` — «не задано»."""
    return round(emu / _EMU_PER_INCH, 2) if isinstance(emu, (int, float)) else None


def _is_pictogram(text: str) -> bool:
    """Текст набран пиктограммным шрифтом, а не буквами.

    Знаки **области частного использования** Unicode (U+E000–U+F8FF) сами по
    себе ничего не значат: их рисует конкретная гарнитура, и в браузере на
    месте «примера» будут квадратики. У `business_plan` роль `subtitle` — это
    шрифт `linecons`, и её пример состоит из них целиком.

    Тот же признак, которым движок опознаёт пиктограммный слот (`Z-43`,
    `Slot.typeface_kind`), только здесь он нужен не для выбора раскладки, а
    чтобы не показать человеку мусор вместо текста. Диапазон — из стандарта
    Unicode, а не из движка: границу слоя ради него не ломаем.
    """
    letters = [ch for ch in text if not ch.isspace()]
    if not letters:
        return False
    private = sum(1 for ch in letters if "" <= ch <= "")
    return private * 2 > len(letters)


def _font_of(entry: dict) -> str | None:
    """Имя гарнитуры типо-роли.

    В OOXML их две — латинская и кириллическая, и в шаблонах сплошь и рядом
    заполнена одна. Обе пусты — гарнитура наследуется от темы, и это
    **«не задано»**, а не «нет шрифта»: подставлять сюда что-то от себя значит
    выдумывать за шаблон.
    """
    return entry.get("latin") or entry.get("cyrl") or None


def _summarise(system: dict, patterns: dict | None, filename: str) -> dict:
    """Сворачивает дизайн-систему до показываемого.

    **Ничего не вычисляет — только выбирает и переводит единицы.** Всё, что
    здесь есть, движок уже посчитал; задача веба — не соврать при сокращении.
    """
    palette = system.get("palette") or {}
    observed = palette.get("observed") or []
    slide = system.get("slide") or {}
    grid = system.get("grid") or {}
    source = system.get("source") or {}

    scale = []
    for entry in system.get("type_scale") or []:
        examples = [
            e for e in (entry.get("examples") or [])
            if e and e.strip() and not _is_pictogram(e)
        ]
        scale.append({
            "role": entry.get("role"),
            "font": _font_of(entry),
            "size_pt": entry.get("size_pt"),
            "color_hex": entry.get("color_hex"),
            "bold": bool(entry.get("bold")),
            "count": entry.get("count"),
            # Пример — настоящий текст шаблона, и он тут главный: им видно, что
            # размеры сняты с живого файла, а не придуманы. `None` значит
            # «показать нечего» — либо примеров нет, либо они пиктограммные.
            "example": (examples[0][:60] if examples else None),
            "pictogram": bool(
                (entry.get("examples") or []) and not examples
            ),
        })

    return {
        "ok": True,
        "template": filename,
        "slide": {
            "aspect": slide.get("aspect"),
            "width_in": _inch(slide.get("cx_emu")),
            "height_in": _inch(slide.get("cy_emu")),
        },
        "source": {
            "slides": source.get("slides"),
            "layouts": source.get("layouts"),
            "masters": source.get("masters"),
        },
        # Раскладок может не быть вовсе: у `.potx` без слайдов доноров нет, и
        # движок уходит на макеты (`ADR-0006`). Ноль здесь — законный ответ.
        "patterns": len((patterns or {}).get("patterns") or []),
        "palette": {
            "core": palette.get("core") or [],
            "theme": [
                {"role": t.get("role"), "hex": t.get("hex")}
                for t in (palette.get("theme") or [])
            ],
            # Список наблюдаемых цветов не отдаём: страница его не рисует, а
            # отданное и непоказанное рано или поздно порождает подпись
            # «показаны 12 из 24» под пустым местом. Так и вышло при первой
            # проверке глазами 22 сентября. Нужно число — оно ниже.
            "observed_total": len(observed),
        },
        "type_scale": scale,
        "grid": {
            "margin_left_in": _inch(grid.get("margin_left_emu")),
            "margin_right_in": _inch(grid.get("margin_right_emu")),
            "margin_top_in": _inch(grid.get("margin_top_emu")),
            "margin_bottom_in": _inch(grid.get("margin_bottom_emu")),
            "gutter_in": _inch(grid.get("gutter_emu")),
            "columns": grid.get("columns"),
            "samples": grid.get("samples"),
        },
    }


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


#: Режимы текста модели — те же, что у `build --text` (`mimeo.plan.outline.MODES`);
#: импортировать движок веб не вправе, совпадение стережёт тест.
TEXT_MODES = ("keep", "improve")
#: Назначения презентации — те же, что `outline.PURPOSES`; `import mimeo` здесь
#: запрещён (веб зовёт движок только командной строкой), сверяет тест.
PURPOSES = ("feature", "product", "project", "initiative")


def _model_summary(run_dir: str) -> dict | None:
    """Каким путём собран текст колоды — из `<out>/outline.json` по его схеме
    (`contracts/outline.schema.json`), а не из печатной сводки (`Z-46`).

    `failed` — что не прошло проверки в последней попытке (повтор, если был):
    веб обязан сказать, почему колода собрана без модели и что модель
    потеряла. `None` — файла нет: сборка до пути модели не дошла."""
    record = _read_json(os.path.join(run_dir, "outline.json"))
    if not isinstance(record, dict):
        return None
    retry = record.get("retry") if isinstance(record.get("retry"), dict) else None
    last = (retry or {}).get("checks") or record.get("checks") or []
    status = record.get("status")
    return {
        "status": status,
        "by_model": status == "accepted",
        "text_mode": record.get("text_mode"),
        "retried": retry is not None,
        "line": record.get("line") or "",
        "failed": [] if status == "accepted" else
                  [f"{c.get('name')}: {c.get('detail')}" for c in last if not c.get("ok")],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "mimeo-web"

    # --- служебное -----------------------------------------------------

    def log_message(self, fmt, *args):            # noqa: A003
        sys.stderr.write("  %s\n" % (fmt % args))

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Локальный инструмент, но привычки дешевле заводить сразу.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _fail(self, code: int, message: str) -> None:
        self._json({"ok": False, "error": message}, code)

    def _body(self, limit: int) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > limit:
            return None
        return self.rfile.read(length)

    # --- маршруты ------------------------------------------------------

    def do_GET(self) -> None:                      # noqa: N802
        url = urllib.parse.urlparse(self.path)
        if url.path == "/":
            return self._static("index.html")
        if url.path == "/api/export":
            return self._export(urllib.parse.parse_qs(url.query))
        if url.path == "/api/deck":
            return self._deck(urllib.parse.parse_qs(url.query))
        if url.path == "/api/design":
            return self._design(urllib.parse.parse_qs(url.query))
        if url.path == "/api/preview":
            return self._preview(urllib.parse.parse_qs(url.query))
        if url.path == "/api/preview-image":
            return self._preview_image(urllib.parse.parse_qs(url.query))
        if url.path == "/api/progress":
            return self._progress(urllib.parse.parse_qs(url.query))
        if url.path.startswith("/static/"):
            return self._static(url.path[len("/static/"):])
        self._fail(404, "нет такого адреса")

    def do_POST(self) -> None:                     # noqa: N802
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/template":
            return self._upload(urllib.parse.parse_qs(url.query))
        if url.path == "/api/content":
            return self._content(urllib.parse.parse_qs(url.query))
        if url.path == "/api/build":
            return self._build()
        if url.path == "/api/fix":
            return self._fix()
        self._fail(404, "нет такого адреса")

    # --- отдача страницы -----------------------------------------------

    def _static(self, name: str) -> None:
        # Имя приходит снаружи, поэтому нормализуем и проверяем, что не вышли
        # из каталога. `..` в адресе — самая старая дыра в вебе.
        target = os.path.normpath(os.path.join(STATIC, name))
        if not target.startswith(STATIC) or not os.path.isfile(target):
            return self._fail(404, "нет такого файла")
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(target, "rb") as fh:
            self._send(200, fh.read(), ctype)

    # --- шаблон --------------------------------------------------------

    def _upload(self, query: dict) -> None:
        """Тело запроса — **сырые байты** шаблона, имя в параметре.

        `multipart` мы не разбираем намеренно: `cgi.FieldStorage` объявлен
        устаревшим в 3.11 и удалён в 3.13, а свой разбор — лишний код с лишними
        дырами ради формата, который здесь ничего не даёт.
        """
        name = (query.get("name") or [""])[0]
        name = os.path.basename(urllib.parse.unquote(name)).strip()
        if not name.lower().endswith(ALLOWED_TEMPLATE):
            return self._fail(400, "шаблон должен быть .pptx или .potx")
        payload = self._body(MAX_TEMPLATE_BYTES)
        if payload is None:
            return self._fail(400, "пустой файл или больше 100 МБ")

        token, path = _new_run()
        target = os.path.join(path, name)
        with open(target, "wb") as fh:
            fh.write(payload)
        with _LOCK:
            _RUNS[token]["template"] = target
        self._json({"ok": True, "token": token, "name": name, "bytes": len(payload)})

    # --- контент-пакет -------------------------------------------------

    def _content(self, query: dict) -> None:
        """Картинка контент-пакета (`Z-73`): сырые байты, имя и токен прогона
        в параметрах — как у шаблона. Ложится в каталог прогона рядом с
        `content.md`: движок ищет картинки, названные в тексте, от каталога
        текста. Текст файлом сервер не принимает — страница читает его сама и
        кладёт в поле, где его видно и можно поправить."""
        run = _run(str((query.get("token") or [""])[0]))
        if not run:
            return self._fail(400, "шаблон не загружен — начните с него")
        name = safe_image_name(urllib.parse.unquote((query.get("name") or [""])[0]))
        if not name.lower().endswith(ALLOWED_IMAGES) or name.startswith("."):
            return self._fail(400, "картинка должна быть .png, .jpg, .gif, .bmp или .webp")
        with _LOCK:
            uploads = run.setdefault("uploads", [])
            too_many = name not in uploads and len(uploads) >= MAX_IMAGES
        if too_many:
            return self._fail(400, f"картинок больше {MAX_IMAGES}")
        payload = self._body(MAX_IMAGE_BYTES)
        if payload is None:
            return self._fail(400, "пустой файл или больше 20 МБ")
        with open(os.path.join(run["dir"], name), "wb") as fh:
            fh.write(payload)
        with _LOCK:
            if name not in uploads:
                uploads.append(name)
        self._json({"ok": True, "name": name, "bytes": len(payload)})

    # --- сборка --------------------------------------------------------

    def _build(self) -> None:
        payload = self._body(MAX_TEXT_CHARS * 4)
        if payload is None:
            return self._fail(400, "пустой запрос")
        try:
            request = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._fail(400, "тело не разобралось как JSON")

        run = _run(str(request.get("token") or ""))
        if not run or not run["template"]:
            return self._fail(400, "шаблон не загружен — начните с него")

        text = str(request.get("text") or "").strip()
        if not text:
            return self._fail(400, "текст пустой: движку нечего раскладывать")
        if len(text) > MAX_TEXT_CHARS:
            return self._fail(400, "текст длиннее миллиона знаков")

        content = os.path.join(run["dir"], "content.md")
        with open(content, "w", encoding="utf-8") as fh:
            fh.write(text)
        # Картинки контент-пакета, названные в тексте путём с каталогами.
        place_uploads(text, run["dir"], list(run.get("uploads") or ()))

        report_path = os.path.join(run["dir"], "report.json")
        raw = request.get("variants")
        variants = int(raw) if str(raw).isdigit() else 1
        ceiling = _load_max_variants()
        if not 1 <= variants <= ceiling:
            # **Не урезаем молча.** Попросили 40, отдали 27 без слова — и
            # пользователь считает, что получил всё, что просил. Это тот же
            # «молчаливый ноль», что и показанный ноль дефектов у
            # непроверенной вёрстки (`Z-20`).
            return self._fail(
                400,
                f"вариантов можно просить от 1 до {ceiling} — "
                f"столько политик ранга в config/variants.json",
            )
        verify_requested = bool(request.get("verify"))

        argv = [
            "build", run["template"], content,
            "-o", run["dir"],
            "--output", os.path.join(run["dir"], "deck.pptx"),
            "--report", report_path,
            "-q",
        ]
        slides = str(request.get("slides") or "").strip()
        if slides:
            argv += ["--slides", slides]
        # Режим текста модели (`ADR-0023`): «доработать» — умолчание движка,
        # но страница шлёт выбор всегда, чтобы итог не зависел от умолчания.
        text_mode = str(request.get("text_mode") or "")
        if text_mode in TEXT_MODES:
            argv += ["--text", text_mode]
        # Назначение (`Z-37`): пусто — не задано, порядок по тексту.
        purpose = str(request.get("purpose") or "")
        if purpose in PURPOSES:
            argv += ["--purpose", purpose]
        if variants > 1:
            argv += ["--variants", str(variants)]
        # Рисовать ли картинки генератором (`Z-28`): страница шлёт выбор
        # всегда; нет поля — умолчание движка. Картинки автора встают в обоих случаях.
        if "images" in request:
            argv += ["--images", "on" if request.get("images") else "off"]
        if verify_requested:
            # Аудит слайдов (`Z-34`) идёт с проверкой вёрстки: обоим нужен
            # PowerPoint, и оба про то, что увидит зритель.
            argv += ["--verify", "--audit"]
        with _LOCK:
            # Для «исправить отмеченное»: та же сборка плюс выбор (`/api/fix`).
            run["argv"] = list(argv)
            run["verify_requested"] = verify_requested
            run["variants"] = variants
            run["images"] = bool(request.get("images", True))
        return self._run_engine(run, argv, verify_requested, variants, report_path)

    def _run_engine(self, run: dict, argv: list, verify_requested: bool, variants: int,
                    report_path: str) -> None:
        """Сборка движком и ответ странице — общий у `/api/build` и `/api/fix`."""
        # Проверка вёрстки поднимает PowerPoint, а он одноэкземплярный. Сборка
        # **ждёт** замок, в отличие от превью: её попросили кнопкой, и ответить
        # «занято» на прямое действие хуже, чем подождать. Предел ожидания есть
        # — иначе зависший зонд подвесил бы и сборку (`PLAN-8.2`, дыра 3).
        holding = False
        if verify_requested:
            holding = _POWERPOINT.acquire(timeout=BUILD_TIMEOUT_SECONDS)
            if not holding:
                return self._fail(
                    503, "PowerPoint занят другой операцией дольше допустимого")
        started = time.perf_counter()
        with _LOCK:
            # Для хода сборки (`/api/progress`): свежими считаются файлы новее этой метки.
            run["started_at"] = time.time()
            run["building"] = True
        try:
            proc = _engine(argv)
        except subprocess.TimeoutExpired:
            return self._fail(504, f"движок не уложился в {BUILD_TIMEOUT_SECONDS} с")
        finally:
            with _LOCK:
                run["building"] = False
            if holding:
                _POWERPOINT.release()
        elapsed = time.perf_counter() - started

        report = _read_json(report_path)
        if report is None:
            # Отчёта нет — значит сборка не дошла до конца. Судим по этому, а
            # **не** по коду возврата: код 2 означает «структурные проблемы», то
            # есть штатный исход, и читать его как отказ значит ломать
            # нормальную работу (`mimeo/cli.py`, комментарий у `return 2`).
            return self._json({
                "ok": False,
                "error": "движок не собрал колоду",
                "returncode": proc.returncode,
                "stderr": (proc.stderr or "")[-4000:],
                "stdout": (proc.stdout or "")[-4000:],
            }, 500)

        decks = report.get("decks") or []
        # Отчёты аудита (`Z-34`) — как есть, по схеме `contracts/audit.schema.json`:
        # `null` — аудит не запускался, а не «находок нет».
        audits = []
        for deck in decks:
            path = deck.get("audit")
            audits.append(_read_json(os.path.join(ROOT, path)) if path else None)
        with _LOCK:
            run["decks"] = [d.get("path") for d in decks]
            run["audits"] = audits
            # Колоды пересобраны — старые картинки к ним больше не относятся.
            # Оставить их значило бы показать прошлую вёрстку как нынешнюю
            # (`PLAN-8.2`, логическая проверка 1, дыра 5).
            run["previews"] = {}
        # И выгрузки в .html и .pdf — они от прежних колод (`Z-27`).
        for stale in glob.glob(os.path.join(run["dir"], "preview-*")) + glob.glob(
                os.path.join(run["dir"], "export-*")):
            shutil.rmtree(stale, ignore_errors=True)

        # Отчёт проверки вёрстки читаем и отдаём **как есть**: у него своя схема
        # (`contracts/render-report.schema.json`) с обязательным `status`, и
        # пересказывать его своими словами значит потерять различие
        # «не смогли» / «чисто».
        verify = []
        for deck in decks:
            path = deck.get("render_report")
            verify.append(_read_json(os.path.join(ROOT, path)) if path else None)

        self._json({
            "ok": True,
            "report": report,
            "model": _model_summary(run["dir"]),
            "verify": verify,
            "verify_requested": verify_requested,
            "audit": audits,
            # Сколько вариантов просили — говорит **сервер**, а не поле формы.
            # Страница читала поле в момент показа, и стоило его тронуть после
            # сборки, как подпись «вышло 1 из 3» начинала врать о том, чего не
            # просили. Найдено проверкой глазами 22 сентября.
            "variants_asked": variants,
            "returncode": proc.returncode,
            "seconds": round(elapsed, 2),
            "stderr": (proc.stderr or "")[-4000:],
        })

    def _progress(self, query: dict) -> None:
        """Докуда дошла идущая сборка — по файлам, которые движок уже положил в
        каталог прогона, а не по таймеру: `outline.json` — колода от модели,
        `images/*.png` — готовые картинки, `deck*.pptx` — собранные варианты,
        `render-report*.json` и `audit*.json` — проверка вёрстки и аудит.
        Свежими считаются файлы новее начала сборки: в том же каталоге лежат и
        файлы прошлой. Движок пишет их по вариантам по очереди — стадии идут
        внахлёст, и страница показывает счёт у каждой, а не одну «текущую»."""
        run = _run(str((query.get("token") or [""])[0]))
        if not run:
            return self._fail(400, "шаблон не загружен — начните с него")
        with _LOCK:
            since = run.get("started_at")
            building = bool(run.get("building"))
        if since is None:
            return self._json({"ok": True, "building": False})
        folder = run["dir"]

        def fresh(pattern: str) -> int:
            count = 0
            for path in glob.glob(os.path.join(folder, pattern)):
                try:
                    if os.path.getmtime(path) >= since:
                        count += 1
                except OSError:
                    continue
            return count

        model = _model_summary(folder) if fresh("outline.json") else None
        self._json({
            "ok": True,
            "building": building,
            "elapsed": round(time.time() - since, 1),
            "model": None if model is None else {"by_model": model["by_model"]},
            # Папка картинок — `images` (`mimeo/plan/images.py`, FOLDER); движок
            # очищает её в начале сборки, заготовок файлами не пишет.
            "pictures": fresh(os.path.join("images", "*.png")),
            "decks": fresh("deck*.pptx"),
            "verified": fresh("render-report*.json"),
            "audited": fresh("audit*.json"),
            "variants": run.get("variants") or 1,
            "verify": bool(run.get("verify_requested")),
            "images": bool(run.get("images", True)),
        })

    def _fix(self) -> None:
        """«Исправить отмеченное» (`Z-34`): та же сборка, что прошлая, плюс
        выбранные находки аудита файлом — `build --fix`. Смысловые переписывает
        модель, вёрсточные ужимаются; после — аудит заново."""
        payload = self._body(1 << 20)
        if payload is None:
            return self._fail(400, "пустой запрос")
        try:
            request = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._fail(400, "тело не разобралось как JSON")
        run = _run(str(request.get("token") or ""))
        if not run or not run.get("argv"):
            return self._fail(400, "сначала соберите колоду")
        ids = {str(i) for i in request.get("ids") or ()}
        picked = [f for audit in run.get("audits") or () if audit
                  for f in audit.get("findings") or () if f.get("id") in ids and f.get("fix")]
        if not picked:
            return self._fail(400, "не отмечено ни одной исправимой находки")
        fix_path = os.path.join(run["dir"], "fix.json")
        with open(fix_path, "w", encoding="utf-8") as fh:
            json.dump({"findings": picked}, fh, ensure_ascii=False, indent=2)
        argv = list(run["argv"]) + ["--fix", fix_path]
        report_path = argv[argv.index("--report") + 1]
        return self._run_engine(run, argv, bool(run.get("verify_requested")),
                                int(run.get("variants") or 1), report_path)

    # --- что вынули из шаблона -----------------------------------------

    def _design(self, query: dict) -> None:
        """Дизайн-система шаблона: то, что движок из него достал.

        **Данные не считаются здесь, а читаются.** `analyze` уже кладёт
        `design-system.json` и `patterns.json`; веб их сворачивает до
        показываемого и ничего не выводит сам. Замер 22 сентября: разбор стоит
        0.4 с на нашем `stilnyj` и **4.6 с** на выданном VK Tech с его 54
        слайдами, поэтому страница запрашивает это отдельно и не держит форму.

        Результат кладётся в прогон: второй раз тот же шаблон не разбираем.
        """
        run = _run((query.get("token") or [""])[0])
        if not run:
            return self._fail(404, "прогон не найден")
        if run.get("design"):
            return self._json(run["design"])

        started = time.perf_counter()
        try:
            proc = _engine(["analyze", run["template"], "-o", run["dir"], "-q"])
        except subprocess.TimeoutExpired:
            return self._fail(504, "разбор шаблона не уложился во время")

        system = _read_json(os.path.join(run["dir"], "design-system.json"))
        patterns = _read_json(os.path.join(run["dir"], "patterns.json"))
        if system is None:
            # Судим по наличию артефакта, а не по коду возврата: у движка код
            # осмысленный и «не ноль» не равно «не сработало» (`cli.py`).
            return self._json({
                "ok": False,
                "error": "шаблон не разобрался",
                "stderr": (proc.stderr or "")[-2000:],
            }, 500)

        payload = _summarise(system, patterns, os.path.basename(run["template"]))
        payload["seconds"] = round(time.perf_counter() - started, 1)
        with _LOCK:
            run["design"] = payload
        self._json(payload)

    # --- превью слайдов ------------------------------------------------

    def _deck_index(self, query: dict, run: dict) -> int | None:
        """Номер колоды из запроса. Снаружи приходит **только он**."""
        raw = (query.get("n") or ["0"])[0]
        if not raw.isdigit() or int(raw) >= len(run["decks"]):
            return None
        return int(raw)

    def _preview(self, query: dict) -> None:
        """Растр колоды: то же, чем стадия VERIFY меряет вёрстку.

        **Рисуем по требованию и по одной колоде.** Три варианта — это три
        запуска PowerPoint по 4–5 с, а попросить пользователь может и девять
        (`PLAN-8.2`, логическая проверка 1, дыра 7).

        Замок берётся **без ожидания**: если идёт проверка вёрстки, ждать её
        можно до восьмидесяти секунд, и браузер всё это время висел бы.
        Честнее ответить сразу.
        """
        run = _run((query.get("token") or [""])[0])
        if not run:
            return self._fail(404, "прогон не найден")
        n = self._deck_index(query, run)
        if n is None:
            return self._fail(404, "нет такой колоды")

        with _LOCK:
            ready = run["previews"].get(n)
        if ready:
            return self._json({"ok": True, "slides": self._image_urls(run, n, len(ready)),
                               "cached": True})

        if not _POWERPOINT.acquire(blocking=False):
            return self._json({
                "ok": False, "busy": True,
                "error": "PowerPoint сейчас занят другой операцией — идёт проверка "
                         "вёрстки. Дождитесь её и повторите.",
            })
        try:
            outcome = self._rasterise(run, n)
        finally:
            _POWERPOINT.release()
        self._json(outcome, 200 if outcome.get("ok") else 200)

    def _rasterise(self, run: dict, n: int) -> dict:
        """Зовёт растровый зонд и переводит его код возврата в понятный ответ.

        Кодов четыре, и **они означают разное**: 0 — нарисовано, 3 — PowerPoint
        занят чужим экземпляром, 2 — не Windows, остальное — отказ. Свести их в
        одно «не смогли» значило бы отнять у человека единственное действие,
        которое он может совершить: закрыть PowerPoint.
        """
        target = os.path.join(run["dir"], f"preview-{n}")
        # Каталог чистим: колода могла стать короче, и лишние картинки прошлого
        # прогона выдали бы себя за нынешние слайды.
        shutil.rmtree(target, ignore_errors=True)
        os.makedirs(target, exist_ok=True)

        deck = run["decks"][n]
        deck = deck if os.path.isabs(deck) else os.path.join(ROOT, deck)
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "tools", "render_probe.py"),
                 deck, "-o", target, "--width", str(PREVIEW_WIDTH)],
                cwd=ROOT, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=BUILD_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "растр не уложился во время"}

        if proc.returncode == 3:
            return {
                "ok": False, "busy": True,
                "error": "PowerPoint уже открыт — закройте его и повторите. "
                         "Приложение одноэкземплярное, и мы не вправе закрывать "
                         "ваши документы.",
            }
        if proc.returncode == 2:
            return {"ok": False,
                    "error": "растр рисует PowerPoint, а он есть только на Windows"}

        # Список строим **обходом каталога**, а не счётчиком: имена вида
        # `slide-01.png` перестают сортироваться как числа после сотого слайда
        # (`PLAN-8.2`, дыра 4).
        found = sorted(glob.glob(os.path.join(target, "slide-*.png")))
        if proc.returncode != 0 or not found:
            return {
                "ok": False,
                "error": "нарисовать слайды не вышло",
                "stderr": ((proc.stdout or "") + (proc.stderr or ""))[-2000:],
            }
        with _LOCK:
            run["previews"][n] = [os.path.basename(f) for f in found]
        return {"ok": True, "slides": self._image_urls(run, n, len(found)),
                "seconds": round(time.perf_counter() - started, 1), "cached": False}

    def _image_urls(self, run: dict, n: int, count: int) -> list:
        """Адреса картинок. **По номеру, а не по пути** — как у скачивания."""
        # `safe=""` обязателен: по умолчанию `quote` **не трогает `/`**, и токен
        # с косой чертой развалил бы адрес. Сегодня `token_urlsafe` таких не
        # выдаёт, но полагаться на это значит оставить мину под сменой способа
        # выдачи токенов. Нашёл тест.
        token = urllib.parse.quote(run["token"], safe="")
        return [f"/api/preview-image?token={token}&n={n}&i={i}" for i in range(count)]

    def _preview_image(self, query: dict) -> None:
        run = _run((query.get("token") or [""])[0])
        if not run:
            return self._fail(404, "прогон не найден")
        n = self._deck_index(query, run)
        raw = (query.get("i") or [""])[0]
        with _LOCK:
            names = run["previews"].get(n) if n is not None else None
        if not names or not raw.isdigit() or int(raw) >= len(names):
            return self._fail(404, "нет такой картинки")
        path = os.path.join(run["dir"], f"preview-{n}", names[int(raw)])
        if not os.path.isfile(path):
            return self._fail(404, "картинка пропала")
        with open(path, "rb") as fh:
            self._send(200, fh.read(), "image/png")

    # --- выгрузка в .html и .pdf ---------------------------------------

    def _export(self, query: dict) -> None:
        """Колода номер `n` — в `.html` или `.pdf` (ТЗ, п. 7; `Z-27`).

        **По требованию и один раз.** Выгрузка идёт командой `mimeo export`
        (граница слоя, `tests/test_web_boundary.py`), готовый файл лежит в
        `export-<n>/` прогона до пересборки колод. PDF рисует PowerPoint — он
        берёт тот же замок, что проверка вёрстки и превью, и ждёт его, как
        сборка: это прямое действие пользователя (`PLAN-8.2`, дыра 1)."""
        run = _run((query.get("token") or [""])[0])
        if not run:
            return self._fail(404, "прогон не найден")
        n = self._deck_index(query, run)
        if n is None:
            return self._fail(404, "нет такой колоды")
        fmt = (query.get("format") or [""])[0]
        if fmt not in ("html", "pdf"):
            return self._fail(400, "формат — html или pdf")
        deck = run["decks"][n]
        deck = deck if os.path.isabs(deck) else os.path.join(ROOT, deck)
        if not os.path.isfile(deck):
            return self._fail(404, "колода пропала")
        out_dir = os.path.join(run["dir"], f"export-{n}")
        target = os.path.join(out_dir, os.path.splitext(os.path.basename(deck))[0] + "." + fmt)
        if not os.path.isfile(target):
            holding = False
            if fmt == "pdf":
                holding = _POWERPOINT.acquire(timeout=BUILD_TIMEOUT_SECONDS)
                if not holding:
                    return self._fail(503, "PowerPoint занят другой операцией дольше допустимого")
            try:
                proc = _engine(["export", deck, f"--{fmt}", "-o", out_dir])
            except subprocess.TimeoutExpired:
                return self._fail(504, f"выгрузка не уложилась в {BUILD_TIMEOUT_SECONDS} с")
            finally:
                if holding:
                    _POWERPOINT.release()
            if proc.returncode == 3:
                return self._fail(503, "PowerPoint уже открыт — закройте его и повторите: "
                                       "приложение одноэкземплярное, закрывать ваши документы мы не вправе")
            if not os.path.isfile(target):
                return self._fail(500, "выгрузка не удалась: "
                                       + ((proc.stdout or "") + (proc.stderr or ""))[-600:])
        ctype = "text/html; charset=utf-8" if fmt == "html" else "application/pdf"
        with open(target, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        name = urllib.parse.quote(os.path.basename(target))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{name}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # --- скачивание ----------------------------------------------------

    def _deck(self, query: dict) -> None:
        run = _run((query.get("token") or [""])[0])
        if not run:
            return self._fail(404, "прогон не найден")
        raw = (query.get("n") or ["0"])[0]
        if not raw.isdigit() or int(raw) >= len(run["decks"]):
            return self._fail(404, "нет такой колоды")
        # Путь берётся из нашего списка по номеру. Снаружи он не приходит
        # никогда — ни целиком, ни частью.
        path = run["decks"][int(raw)]
        path = path if os.path.isabs(path) else os.path.join(ROOT, path)
        if not os.path.isfile(path):
            return self._fail(404, "файл пропал")
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{os.path.basename(path)}"',
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(port: int, fresh: bool) -> None:
    if fresh and os.path.isdir(RUNS_DIR):
        shutil.rmtree(RUNS_DIR, ignore_errors=True)
    os.makedirs(RUNS_DIR, exist_ok=True)
    # `ThreadingHTTPServer`, а не `HTTPServer`: проверка вёрстки идёт до
    # восьмидесяти секунд, и однопоточный сервер на это время переставал бы
    # отвечать вовсе — даже отдавать страницу.
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as error:
        # Найдено запуском, а не рассуждением: на Windows порт бывает занят
        # чужой программой (10048) или **зарезервирован системой** (10013,
        # диапазоны Hyper-V), и голый трейсбек ничего эксперту не объясняет.
        # `netsh int ipv4 show excludedportrange protocol=tcp` показывает
        # зарезервированные.
        sys.exit(
            f"Порт {port} занять не вышло: {error}."
            + chr(10)
            + f"Попробуйте другой: python web/serve.py --port {port + 1}"
        )
    print(f"Цифровой дизайнер презентаций: http://127.0.0.1:{port}")
    print("Ctrl+C чтобы остановить")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлен")
    finally:
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Локальный веб-интерфейс к mimeo. Слушает только 127.0.0.1.",
    )
    parser.add_argument("--port", type=int, default=8000, help="порт (по умолчанию 8000)")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help=f"стереть каталоги прошлых прогонов ({os.path.relpath(RUNS_DIR, ROOT)}) при запуске",
    )
    serve(**vars(parser.parse_args()))


if __name__ == "__main__":
    main()
