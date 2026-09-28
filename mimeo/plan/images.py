"""Картинки по идеям модели — `Z-28`, курс пользователя 26 сентября (вечер).

Модель пишет на слайд сюжет рисунка (`image_idea`, `PLAN-9.0`, Ш6); здесь он
становится картинкой в месте под иллюстрацию. Порядок такой:

1. **До плана** разделу с идеей добавляется блок картинки, файла которой ещё
   нет (`add_placeholders`). Пропорция неизвестна (`imagesize.image_size` даёт
   `None`), и планировщик ставит картинку в первое место под иллюстрацию
   раскладки, которую выбрал, — тем же путём, что картинку автора (`Z-28a`).
2. **После плана** место известно, и картинка рисуется ровно в его пропорции
   (`Painter.paint`): обрезать и растягивать нечего. Заливка плана начинает
   указывать на готовый файл.

«Что изображено» пишет модель, «как» — код: суффикс запроса из конфига держит
одну технику на всю колоду (`Z-28`, «Промпт пишут двое»). Генератор —
`sd-server` из stable-diffusion.cpp с Z-Image-Turbo, за HTTP, как и языковая
модель: продукт остаётся на стандартной библиотеке (`ADR-0001`). Настройки —
`config/generator.json`.

**Видеокарта одна.** Gemma и генератор вместе в 12 ГБ не помещаются, поэтому
`llama-server` поднят с `--sleep-idle-seconds` и через несколько секунд
простоя отдаёт память; перед первой картинкой ждём, пока `/props` скажет
`is_sleeping` (замер 26 сентября: 9.5 ГБ → 1.2 ГБ за 3 с, пробуждение — 4 с).
`sd-server` с `--offload-to-cpu` держит веса в памяти машины и в простое
занимает около 0.7 ГБ видеопамяти.

**Отказ слышен.** Генератор не отвечает — заготовки не ставятся, и план говорит
об этом словами; картинка не нарисовалась посреди сборки — вызывающий
перестраивает план без неё (`without`), чтобы в месте под иллюстрацию не
осталась картинка донора (`Z-62`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, fields, replace

from ..model import Fill
from . import cache as llm_cache

CONFIG_NAME = "generator.json"

#: Доступ поверх конфига: `on` или `off`. Ею тесты держат `off` (`tests/conftest.py`).
ACCESS_ENV = "MIMEO_IMAGES_ACCESS"

#: Блоки картинок, которые нарисует генератор, помечены по `id`: `_pick_ideas`
#: отличает их от картинок автора — у раздела с картинкой автора идея не нужна,
#: а у раздела с заготовкой идея и есть то, что нарисовано.
GENERATED_PREFIX = "gen-"

#: Каталог готовых картинок сборки — внутри каталога артефактов.
FOLDER = "images"


@dataclass(frozen=True)
class GeneratorConfig:
    """Настройки генератора. Числа и адреса по умолчанию — те же, что в
    `config/generator.json`. Тексты для моделей — хвост запроса картинки и
    промпт сцен — только в файле (`Z-72`, ТЗ, раздел 4): без файла
    `load_config` падает, без промпта сцен — `scenes`."""

    access: str = "on"
    base_url: str = "http://127.0.0.1:8081"
    model: str = "z-image-turbo-q8_0"
    steps: int = 8
    cfg_scale: float = 1.0
    seed: int = 42
    megapixels: float = 1.0
    multiple: int = 64
    min_side: int = 512
    max_side: int = 2048
    min_aspect: float = 0.25
    max_aspect: float = 4.0
    min_place_side: float = 0.3
    prompt_suffix: str = ""
    scene_system: str = ""
    scene_batch: int = 10
    scene_max_tokens: int = 6000
    timeout_sec: float = 180.0
    llm_sleep_wait_sec: float = 30.0
    cache_root: str = "cache/images"
    access_source: str = "умолчание config/generator.json"
    loaded: bool = False


_SETTINGS = tuple(f.name for f in fields(GeneratorConfig) if f.name not in ("access_source", "loaded"))


def config_path() -> str:
    return os.path.join(llm_cache.repo_root(), "config", CONFIG_NAME)


def load_config(path: str | None = None) -> GeneratorConfig:
    """Читает `config/generator.json`; значение не того типа — встроенное.
    Файла нет — `MissingConfig` (`Z-72`): в нём тексты для моделей."""
    from ..config import missing

    path = path or config_path()
    config = GeneratorConfig()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise missing(CONFIG_NAME, f"файла нет или он не читается ({exc.__class__.__name__})") from exc
    if isinstance(raw, dict):
        values = {}
        for name in _SETTINGS:
            if name not in raw:
                continue
            default = getattr(config, name)
            try:
                values[name] = type(default)(raw[name])
            except (TypeError, ValueError):
                continue
        config = replace(config, loaded=True, **values)
    env = os.environ.get(ACCESS_ENV, "").strip()
    if env in ("on", "off"):
        config = replace(config, access=env, access_source=f"переменная {ACCESS_ENV}")
    return config


def _opener(url: str):
    from .client import _opener as opener                    # свой сервер — мимо прокси

    return opener(url)


def reachable(gen: GeneratorConfig) -> str | None:
    """Причина не рисовать, если генератор не отвечает; `None` — отвечает."""
    url = gen.base_url.rstrip("/") + "/sdapi/v1/options"
    try:
        with _opener(url).open(url, timeout=3) as response:
            response.read()
        return None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return f"генератор картинок не отвечает ({gen.base_url}: {reason})"


#: Схема ответа на запрос сцен — контракт, в коде (`ADR-0022`); промпт — в конфиге.
#: `plan` — место и занятие каждой картинки одной строкой: модель сперва
#: распределяет набор целиком, чтобы картинки колоды вышли разными; `ru` —
#: сцена по-русски, на нём Gemma пишет лучше; `scenes` — её перевод на
#: английский для генератора. Порядок полей — порядок, в котором модель их
#: пишет. Без годного плана и русского сцены всё равно берутся.
SCENE_SCHEMA = {
    "type": "object",
    "properties": {"plan": {"type": "array", "items": {"type": "string"}},
                   "ru": {"type": "array", "items": {"type": "string"}},
                   "scenes": {"type": "array", "items": {"type": "string"}}},
    "required": ["plan", "ru", "scenes"],
}


def deck_outline(doc) -> list[dict]:
    """Колода целиком для запроса сцен: слайды по порядку — тип, заголовок,
    тезисы. Картинку модель придумывает к мысли слайда в контексте всей
    презентации, а не по одной фразе идеи (решение пользователя 27 сентября)."""
    out = []
    for n, section in enumerate(doc.sections, 1):
        theses = []
        for block in section.blocks:
            if block.kind == "image":
                continue
            if block.items:
                theses.extend(i for i in block.items if i)
            elif block.kind == "metric":
                theses.append(" ".join(x for x in (block.value, block.label) if x))
            elif block.text:
                theses.append(block.text)
        out.append({"slide": n, "kind": section.kind or "", "heading": section.heading or "",
                    "theses": theses})
    return out


def scenes(ideas: list[str], gen: GeneratorConfig, model_config, inputs=(),
           outage=None, translations: dict | None = None, deck=None,
           sections=(), taken=()) -> tuple[list[str] | None, str]:
    """Сюжеты без текста — второй короткий вызов языковой модели (`Z-28`).

    Замер 26 сентября: текст на картинке рисуется, когда он есть в самой идее
    («двигает блоки текста на слайде», «читает сообщение на телефоне»), и
    отрицания в запросе картинки его не гасят. Переписать сюжет — смысл, а
    смысл делает модель. Возвращает (сцены или `None`, откуда или почему нет);
    ответ идёт в кэш ответов модели, как и колода. `outage` — отказ сервера,
    общий на сборку (`client.Outage`). `translations` — словарь, куда лягут
    переводы «сцена → по-русски», если модель их дала: их видит человек.
    `deck` — документ колоды, `sections` — раздел каждой идеи: с ними модель
    получает всю презентацию и номер слайда каждой картинки; без них — только
    список идей. `taken` — сцены, уже написанные для этой колоды прошлыми
    запросами: модель их не повторяет, иначе все картинки выходят про одно
    (замер 27 сентября: пять из пяти — человек за компьютером)."""
    from .client import ModelClient
    from .prompt import Mode, Request
    from .validate import extract_json

    if not gen.scene_system.strip():
        from ..config import missing

        raise missing(CONFIG_NAME, "нет промпта сцен картинок (scene_system)")
    batch = max(1, gen.scene_batch)
    if len(ideas) > batch:
        # Сцена на двух языках — сотни токенов: пачками, чтобы ответ не упёрся
        # в max_tokens модели; вся колода уходит с каждой пачкой.
        out: list[str] = []
        how = ""
        for i in range(0, len(ideas), batch):
            part = list(sections[i:i + batch]) if len(sections) == len(ideas) else ()
            got, how = scenes(ideas[i:i + batch], gen, model_config, inputs, outage,
                              translations, deck, part, tuple(taken) + tuple(out))
            if got is None:
                return None, how
            out.extend(got)
        return out, how
    if deck is not None and len(sections) == len(ideas):
        number = {s.id: n for n, s in enumerate(deck.sections, 1)}
        message = {"presentation": deck_outline(deck),
                   "pictures": [{"slide": number.get(s.id), "idea": idea}
                                for s, idea in zip(sections, ideas)]}
        if taken:
            message["taken"] = list(taken)
        user = json.dumps(message, ensure_ascii=False)
    else:
        user = json.dumps(ideas, ensure_ascii=False)
    if model_config.mode is Mode.FREE_TEXT:
        user += "\n\nОтветь одним JSON-объектом по схеме:\n" + json.dumps(SCENE_SCHEMA, ensure_ascii=False)
    request = Request(mode=model_config.mode, system=gen.scene_system, user=user, schema=SCENE_SCHEMA,
                      section_id="сцены картинок", candidates=(), name="scenes", tool="rewrite_scenes",
                      purpose="Переписать сюжеты рисунков без текста.")
    # Свой потолок ответа: план, сцена и перевод на десяток картинок длиннее
    # общего max_tokens модели (`config/generator.json`, `scene_max_tokens`).
    if gen.scene_max_tokens > 0:
        model_config = replace(model_config, endpoint=replace(
            model_config.endpoint, max_tokens=gen.scene_max_tokens))
    answer = ModelClient(model_config, inputs=inputs, outage=outage).complete(request)
    if not answer:
        return None, answer.note or "ответа нет"
    parsed = extract_json(answer.text)
    got = parsed.get("scenes") if isinstance(parsed, dict) else None
    if not (isinstance(got, list) and len(got) == len(ideas)
            and all(isinstance(s, str) and s.strip() for s in got)):
        return None, "ответ не по форме: нужен список сцен той же длины"
    got = [s.strip() for s in got]
    ru = parsed.get("ru")
    if translations is not None and isinstance(ru, list) and len(ru) == len(got) and all(
            isinstance(s, str) and s.strip() for s in ru):
        translations.update(zip(got, (s.strip() for s in ru)))
    return got, "из кэша" if answer.source == "cache" else "от модели"


def add_placeholders(doc, out_dir: str, gen: GeneratorConfig, rules=None,
                     slide_size: tuple[int, int] | None = None):
    """Разделам с идеями — блок картинки, файла которой ещё нет.

    Правила отбора те же, что у `_pick_ideas` (`config/outline.json`,
    `image_ideas`): тип слайда из списка, у раздела нет картинки автора, не
    больше одной идеи на `slides_per_idea` разделов, по порядку колоды.
    `slide_size` задаёт заготовке наименьшее место (`min_place_side`): ранг
    ищет раскладку, где картинка встанет крупно, а не миниатюрой. Сцены по
    идеям пишет художник после вёрстки, одним запросом на все картинки колоды
    (`Painter.prepare`): до вёрстки неизвестно, где будут рамки под фото.

    Возвращает (документ, {путь заготовки: идея}, заметка или `None`)."""
    from .outline import idea_rules

    if doc.planner != "mixed":
        return doc, {}, None                    # колоду строила не модель — идей нет
    rules = rules or idea_rules()
    ideas = [s for s in doc.sections
             if s.image_idea.strip() and s.kind in rules.kinds
             and not any(b.kind == "image" for b in s.blocks)]
    chosen = {s.id for s in ideas[: len(doc.sections) // rules.slides_per_idea]}
    if not chosen:
        return doc, {}, None
    if gen.access != "on":
        return doc, {}, (f"Картинки не нарисованы: генератор выключен ({gen.access_source}); "
                         f"идей к рисованию {len(chosen)} остались в плане колоды словами (Z-28).")
    problem = reachable(gen)
    if problem:
        return doc, {}, (f"Картинки не нарисованы: {problem}. Идеи модели остались в плане "
                         "колоды словами (Z-28).")

    from .content import ContentBlock

    folder = os.path.join(out_dir, FOLDER)
    # Файл прошлой сборки с именем заготовки дал бы плану пропорцию чужой картинки.
    shutil.rmtree(folder, ignore_errors=True)
    min_side = int(gen.min_place_side * min(slide_size)) if slide_size else None
    wanted: dict[str, str] = {}
    sections = []
    for s in doc.sections:
        if s.id in chosen:
            ref = os.path.join(folder, f"{s.id}.png")
            wanted[ref] = s.image_idea.strip()
            s = replace(s, blocks=tuple(s.blocks) + (ContentBlock(
                id=f"{GENERATED_PREFIX}{s.id}", kind="image", ref=ref, min_side=min_side),))
        sections.append(s)
    return replace(doc, sections=sections), wanted, None


def without(doc, refs) -> object:
    """Документ без заготовок `refs` — план без картинок, которых не вышло."""
    drop = set(refs)
    return replace(doc, sections=[
        replace(s, blocks=tuple(b for b in s.blocks if not (b.kind == "image" and b.ref in drop)))
        for s in doc.sections
    ])


def placed(plan, slides_written, prompts: dict[str, str], ru: dict[str, str] | None = None) -> list[dict]:
    """Нарисованные генератором картинки колоды: номер слайда в файле
    (1-based, по `slides_written` — сборка могла пропустить слайд), промпт,
    с которым картинку нарисовали, и его перевод, если он есть (`ru`: файл →
    по-русски). Для отчёта сборки: в саму колоду промпт не идёт — на слайде
    он был бы служебным текстом (Приложение 1 ТЗ, вопрос 7)."""
    order = list(slides_written) or [s.index for s in plan.slides]
    by_index = {s.index: s for s in plan.slides}
    out = []
    for position, index in enumerate(order, 1):
        slide = by_index.get(index)
        for fill in slide.fills if slide is not None else ():
            if fill.kind == "image" and fill.ref in prompts:
                item = {"slide": position, "prompt": prompts[fill.ref]}
                if ru and fill.ref in ru:
                    item["ru"] = ru[fill.ref]
                out.append(item)
    return out


def size_for(aspect: float, gen: GeneratorConfig) -> tuple[int, int]:
    """Размер картинки в пропорции места: площадь около `megapixels`, стороны
    кратны `multiple` и не выходят за `min_side`–`max_side`."""
    aspect = min(max(aspect, gen.min_aspect), gen.max_aspect)
    area = gen.megapixels * 1_000_000
    width, height = (area * aspect) ** 0.5, (area / aspect) ** 0.5
    m = gen.multiple

    def snap(v: float) -> int:
        return int(min(max(round(v / m) * m, gen.min_side), gen.max_side))

    return snap(width), snap(height)


@dataclass
class Painter:
    """Картинки одной сборки: кэш, ожидание сна языковой модели, счётчики.
    Один на сборку — и на три варианта: одинаковая картинка не рисуется дважды."""

    gen: GeneratorConfig
    wanted: dict[str, str]
    llm_base_url: str | None = None
    #: Размер слайда в EMU: по нему судится, не мелко ли место (`min_place_side`).
    slide_size: tuple[int, int] | None = None
    #: Сюжеты → сцены без текста (`scenes` с настройками модели): для рамок под
    #: фото (`Z-55`, `frames`). Без него рамки не заполняются.
    rewrite: object = None
    #: Каталог готовых картинок сборки (`<out>/images`).
    folder: str | None = None
    drawn: int = 0
    cached: int = 0
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)
    too_small: set[str] = field(default_factory=set)
    #: Рамки под фото (`Z-55`): заполнено по вариантам и почему не заполнялись.
    framed: int = 0
    frames_small: int = 0
    frames_note: str | None = None
    #: Какие сцены написала модель и почему нет — строкой для предупреждений.
    scenes_note: str | None = None
    #: Готовый файл → промпт, с которым его нарисовал генератор: человеку видно,
    #: что просили у генератора для каждой картинки (`placed`, отчёт сборки).
    prompts: dict[str, str] = field(default_factory=dict)
    #: Сцена → перевод по-русски (заполняет `scenes`) и готовый файл → перевод
    #: его сцены: промпт генератора английский, человеку нужен и русский.
    translations: dict[str, str] = field(default_factory=dict)
    ru: dict[str, str] = field(default_factory=dict)
    _scenes: dict[str, str] = field(default_factory=dict)
    _reach: str | None = "?"

    def _cache_path(self, key: str) -> str:
        root = self.gen.cache_root
        if not os.path.isabs(root):
            root = os.path.join(llm_cache.repo_root(), root)
        return os.path.join(root, key + ".png")

    def _key(self, prompt: str, width: int, height: int, seed: int | None = None) -> str:
        blob = json.dumps({"model": self.gen.model, "prompt": prompt, "width": width, "height": height,
                           "steps": self.gen.steps, "cfg": self.gen.cfg_scale,
                           "seed": self.gen.seed if seed is None else seed},
                          ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _wait_llm_sleep(self) -> None:
        """Ждём, пока `llama-server` отдаст видеопамять (`/props` → `is_sleeping`).
        Другой сервер этого поля не знает — ждать нечего.

        Спрашиваем перед **каждой** картинкой, а не перед первой: между ними
        модель просыпается — сцены рамок (`Z-55`), зрение `Z-62` между
        вариантами, — и генератор встал бы рядом с ней в 12 ГБ. Спящий сервер
        отвечает сразу: вопрос стоит один запрос."""
        if not self.llm_base_url:
            return
        root = self.llm_base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        url = root + "/props"
        deadline = time.monotonic() + self.gen.llm_sleep_wait_sec
        while True:
            try:
                with _opener(url).open(url, timeout=3) as response:
                    props = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, OSError, ValueError):
                return                                # сервера нет — память свободна
            if not isinstance(props, dict) or "is_sleeping" not in props or props["is_sleeping"]:
                return
            if time.monotonic() >= deadline:
                return
            time.sleep(0.5)

    def _draw(self, prompt: str, width: int, height: int, seed: int | None = None) -> bytes:
        self._wait_llm_sleep()
        url = self.gen.base_url.rstrip("/") + "/sdapi/v1/txt2img"
        body = {"prompt": prompt, "width": width, "height": height, "steps": self.gen.steps,
                "cfg_scale": self.gen.cfg_scale, "seed": self.gen.seed if seed is None else seed,
                "batch_size": 1}
        request = urllib.request.Request(
            url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        started = time.monotonic()
        with _opener(url).open(request, timeout=self.gen.timeout_sec) as response:
            data = json.loads(response.read().decode("utf-8"))
        self.seconds += time.monotonic() - started
        png = base64.b64decode(data["images"][0])
        if not png.startswith(b"\x89PNG"):
            raise ValueError("генератор вернул не PNG")
        return png

    def picture(self, idea: str, width: int, height: int, target: str, seed: int | None = None) -> bool:
        """Файл `target` с картинкой по идее: из кэша или от генератора."""
        prompt = idea.rstrip(" .") + self.gen.prompt_suffix
        cached = self._cache_path(self._key(prompt, width, height, seed))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isfile(cached):
            shutil.copyfile(cached, target)
            self.cached += 1
            self._remember(target, idea, prompt)
            return True
        try:
            png = self._draw(prompt, width, height, seed)
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as exc:
            reason = getattr(exc, "reason", exc)
            self.failures.append(f"«{idea}» {width}x{height}: {reason}")
            return False
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        with open(cached, "wb") as fh:
            fh.write(png)
        shutil.copyfile(cached, target)
        self.drawn += 1
        self._remember(target, idea, prompt)
        return True

    def _remember(self, target: str, idea: str, prompt: str) -> None:
        """Что просили у генератора для файла `target` — и перевод сцены."""
        self.prompts[target] = prompt
        if idea in self.translations:
            self.ru[target] = self.translations[idea]

    def paint(self, plan, library) -> tuple[object, set[str]]:
        """Заготовки плана — в готовые файлы в пропорции своего места.

        Возвращает план с заливками на готовые файлы и пути заготовок, которые
        нарисовать не вышло: без них вызывающий перестраивает план."""
        if not self.wanted:
            return plan, set()
        patterns = {p.id: p for p in library.patterns}
        failed: set[str] = set()
        slides = []
        for slide in plan.slides:
            pattern = patterns.get(slide.pattern_id)
            fills = []
            for fill in slide.fills:
                raw = self.wanted.get(fill.ref) if fill.kind == "image" else None
                # Сцена, написанная по всей колоде (`prepare`); нет её — идея как есть.
                idea = self._scenes.get(raw, raw) if raw is not None else None
                slot = next((s for s in pattern.slots if s.id == fill.slot_id), None) if pattern else None
                if idea is None or slot is None or not slot.rect.cy:
                    fills.append(fill)
                    continue
                if self.slide_size and min(slot.rect.cx, slot.rect.cy) < (
                        self.gen.min_place_side * min(self.slide_size)):
                    # Миниатюра в рамке донора хуже, чем слайд без картинки.
                    self.too_small.add(idea)
                    failed.add(fill.ref)
                    fills.append(fill)
                    continue
                width, height = size_for(slot.rect.cx / slot.rect.cy, self.gen)
                stem = os.path.splitext(fill.ref)[0]
                target = f"{stem}-{width}x{height}.png"
                if self.picture(idea, width, height, target):
                    fills.append(replace(fill, ref=target))
                else:
                    failed.add(fill.ref)
                    fills.append(fill)
            slides.append(replace(slide, fills=tuple(fills)))
        return replace(plan, slides=tuple(slides)), failed

    # --- рамки под фото (`Z-55`) ---------------------------------------------

    def _frame_jobs(self, plan, library, doc) -> list[tuple[int, object, object, int]]:
        """Пустые рамки плана: (номер слайда в плане, слот, раздел, часть раздела).

        Рамка — место под фото с подсказкой дизайнера (`analyze/picture.py`,
        `find_frames`). Заготовка встаёт в неё ещё в плане; остальные пустуют."""
        from ..analyze.picture import FRAME

        patterns = {p.id: p for p in library.patterns}
        sections = {s.id: s for s in doc.sections}
        jobs = []
        for n, slide in enumerate(plan.slides):
            pattern = patterns.get(slide.pattern_id)
            section = sections.get(slide.origin_section)
            if pattern is None or section is None:
                continue
            used = {f.slot_id for f in slide.fills}
            for slot in pattern.slots:
                if slot.picture_kind == FRAME and slot.id not in used and slot.rect.cy:
                    jobs.append((n, slot, section, slide.origin_part or 1))
        return jobs

    def _subject(self, section) -> str:
        """Сюжет рамки: идея модели, если тип слайда её допускает
        (`config/outline.json`, `image_ideas.kinds`), иначе заголовок слайда —
        вывод, который модель уже написала (AB3). Сцену без текста из него
        сделает та же модель: заголовок «Движок работает на стандартной
        библиотеке» запросом картинки дал бы выдуманные буквы (`ADR-0024`)."""
        from .outline import idea_rules

        if section.image_idea.strip() and section.kind in idea_rules().kinds:
            return section.image_idea.strip()
        return (section.heading or "").strip()

    def _ready(self, doc, jobs) -> bool:
        """Можно ли рисовать рамки; нельзя — причина в `frames_note`, один раз."""
        if not jobs:
            return False
        why = None
        if doc.planner != "mixed":
            why = "колоду строила не модель — сюжетов нет"
        elif self.gen.access != "on":
            why = f"генератор выключен ({self.gen.access_source})"
        elif self.rewrite is None or self.folder is None:
            why = "сцены писать некому"
        else:
            if self._reach == "?":
                self._reach = reachable(self.gen)
            why = self._reach
        if why and not self.frames_note:
            self.frames_note = f"Рамки под фото не заполнены: {why} (Z-55)."
        return why is None

    def _big_enough(self, slot) -> bool:
        """Рамка не мельче `min_place_side`: миниатюра хуже пустой рамки."""
        floor = self.gen.min_place_side * min(self.slide_size) if self.slide_size else 0
        return min(slot.rect.cx, slot.rect.cy) >= floor

    def prepare(self, plans, library, doc) -> None:
        """Сцены для всех картинок колоды — заготовок по идеям и пустых рамок
        всех вариантов — одним вызовом модели (решение пользователя
        27 сентября): модель видит набор целиком и сама делает картинки
        разными, каждая — к мысли своего слайда. Прежде идеи и рамки шли двумя
        вызовами, и пять картинок из пяти вышли «за компьютером». Каждый лишний
        вызов к тому же будит модель, и генератор ждёт, пока она уснёт. Рамки
        мельче порога сцен не получают — рисовать их не будут; уже написанные
        сцены второй раз не спрашиваются."""
        subjects: list[str] = []
        owners: list = []                   # раздел каждого сюжета — его слайд в колоде
        sections = {s.id: s for s in doc.sections}

        def want(subject, section):
            if subject and subject not in self._scenes and subject not in subjects:
                subjects.append(subject)
                owners.append(section)

        for plan in plans:
            for slide in plan.slides:
                for fill in slide.fills:
                    if fill.kind == "image" and fill.ref in self.wanted:
                        # Заготовка — `<раздел>.png` (`add_placeholders`).
                        stem = os.path.splitext(os.path.basename(fill.ref))[0]
                        want(self.wanted[fill.ref], sections.get(stem))
        ideas = len(subjects)
        jobs = [j for plan in plans for j in self._frame_jobs(plan, library, doc)
                if self._big_enough(j[1])]
        if self._ready(doc, jobs):
            for _n, _slot, section, _part in jobs:
                want(self._subject(section), section)
        if not subjects or self.rewrite is None:
            return
        got, how = self.rewrite(subjects, owners if all(owners) else ())
        if got is None:
            if ideas:
                self.scenes_note = f"Сюжеты картинок не переписаны ({how}) — рисуется идея модели как есть (Z-28)."
            if len(subjects) > ideas and not self.frames_note:
                self.frames_note = (f"Рамки под фото не заполнены: сцены без текста не написаны "
                                    f"({how}) — заголовок запросом картинки дал бы выдуманные буквы (Z-55).")
            return
        self._scenes.update(zip(subjects, got))
        # Что нарисовано и почему — в отчёт: сцену пишет модель, и видеть её
        # решение должен человек (замер 26 сентября: «колоду» она прочла как карты).
        pairs = "; ".join(f"«{a}» → «{b}»" for a, b in zip(subjects, got))
        self.scenes_note = (self.scenes_note + " " if self.scenes_note else "") + (
            f"Сцены картинок написала модель по всей колоде ({how}): {pairs} (Z-28).")

    def frames(self, plan, library, doc):
        """Пустые рамки под фото — картинкой по сцене слайда, в пропорции рамки.

        Возвращает план с заливками рамок. Рамка мельче `min_place_side` или
        картинка не нарисовалась — рамка остаётся как была: это не повод
        перестраивать план, ранг и так считал её пустой (`matching.empty_places`).
        Части одного раздела на рамках получают разный `seed` — иначе на двух
        слайдах подряд стояла бы одна и та же картинка."""
        jobs = self._frame_jobs(plan, library, doc)
        if not self._ready(doc, jobs):
            return plan
        self.prepare([plan], library, doc)
        added: dict[int, list] = {}
        for n, slot, section, part in jobs:
            if not self._big_enough(slot):
                self.frames_small += 1
                continue
            scene = self._scenes.get(self._subject(section))
            if not scene:
                continue
            width, height = size_for(slot.rect.cx / slot.rect.cy, self.gen)
            target = os.path.join(self.folder, f"{section.id}-frame{part}-{width}x{height}.png")
            if self.picture(scene, width, height, target, seed=self.gen.seed + part - 1):
                self.framed += 1
                added.setdefault(n, []).append(Fill(slot_id=slot.id, kind="image", ref=target))
        if not added:
            return plan
        slides = tuple(replace(s, fills=tuple(s.fills) + tuple(added.get(n, ())))
                       for n, s in enumerate(plan.slides))
        return replace(plan, slides=slides)

    def note(self) -> str | None:
        """Строка для предупреждений плана: сколько нарисовано и чем."""
        if not (self.drawn or self.cached or self.failures or self.too_small or self.frames_small):
            return " ".join(x for x in (self.frames_note, self.scenes_note) if x) or None
        parts = []
        if self.drawn:
            parts.append(f"нарисовано {self.drawn} за {self.seconds:.1f} с")
        if self.cached:
            parts.append(f"из кэша {self.cached} ({self.gen.cache_root})")
        line = f"Картинки по идеям модели ({self.gen.model}): " + (
            f"{', '.join(parts)}; каждая — в пропорции своего места под иллюстрацию (Z-28)."
            if parts else "не нарисовано ни одной (Z-28).")
        if self.failures:
            line += (f" Не нарисовано {len(self.failures)}: {'; '.join(self.failures)} — эти слайды "
                     "собраны без картинки.")
        if self.too_small:
            names = ", ".join(f"«{i}»" for i in sorted(self.too_small))
            line += (f" Не поставлено там, где место под иллюстрацию мельче "
                     f"{self.gen.min_place_side:.0%} стороны слайда: {names} — слайд перестроен без "
                     "картинки.")
        if self.framed:
            line += (f" В рамки под фото шаблона — {self.framed} из {self.drawn + self.cached} "
                     "(по всем вариантам): сцену по идее или заголовку слайда написала модель (Z-55).")
        if self.frames_small:
            line += (f" Рамок мельче {self.gen.min_place_side:.0%} стороны слайда осталось пустыми "
                     f"{self.frames_small}.")
        if self.frames_note:
            line += " " + self.frames_note
        if self.scenes_note:
            line += " " + self.scenes_note
        return line
