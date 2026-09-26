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
    """Настройки генератора. Значения по умолчанию — те же, что в
    `config/generator.json`: без файла поведение не должно молча меняться."""

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
    prompt_suffix: str = ". Фотография, естественный свет."
    scene_system: str = (
        "Тебе дают сюжеты для рисунков к слайдам презентации. Перепиши каждый так, чтобы на"
        " рисунке не было ни текста, ни букв, ни цифр, ни экранов, ни телефонов с сообщениями,"
        " ни документов, ни слайдов, ни вывесок: только люди, предметы и места в действии,"
        " которые передают ту же мысль. Одна фраза до 15 слов на сюжет, по-русски, в том же"
        " порядке и столько же, сколько дали. Отвечай строго JSON.")
    timeout_sec: float = 180.0
    llm_sleep_wait_sec: float = 30.0
    cache_root: str = "cache/images"
    access_source: str = "умолчание config/generator.json"
    loaded: bool = False


_SETTINGS = tuple(f.name for f in fields(GeneratorConfig) if f.name not in ("access_source", "loaded"))


def config_path() -> str:
    return os.path.join(llm_cache.repo_root(), "config", CONFIG_NAME)


def load_config(path: str | None = None) -> GeneratorConfig:
    """Читает `config/generator.json`; значение не того типа — встроенное."""
    path = path or config_path()
    config = GeneratorConfig()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raw = None
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
SCENE_SCHEMA = {
    "type": "object",
    "properties": {"scenes": {"type": "array", "items": {"type": "string"}}},
    "required": ["scenes"],
}


def scenes(ideas: list[str], gen: GeneratorConfig, model_config, inputs=()) -> tuple[list[str] | None, str]:
    """Сюжеты без текста — второй короткий вызов языковой модели (`Z-28`).

    Замер 26 сентября: текст на картинке рисуется, когда он есть в самой идее
    («двигает блоки текста на слайде», «читает сообщение на телефоне»), и
    отрицания в запросе картинки его не гасят. Переписать сюжет — смысл, а
    смысл делает модель. Возвращает (сцены или `None`, откуда или почему нет);
    ответ идёт в кэш ответов модели, как и колода."""
    from .client import ModelClient
    from .prompt import Mode, Request
    from .validate import extract_json

    if not gen.scene_system.strip():
        return None, "промпта сцен нет в config/generator.json"
    user = json.dumps(ideas, ensure_ascii=False)
    if model_config.mode is Mode.FREE_TEXT:
        user += "\n\nОтветь одним JSON-объектом по схеме:\n" + json.dumps(SCENE_SCHEMA, ensure_ascii=False)
    request = Request(mode=model_config.mode, system=gen.scene_system, user=user, schema=SCENE_SCHEMA,
                      section_id="сцены картинок", candidates=(), name="scenes", tool="rewrite_scenes",
                      purpose="Переписать сюжеты рисунков без текста.")
    answer = ModelClient(model_config, inputs=inputs).complete(request)
    if not answer:
        return None, answer.note or "ответа нет"
    parsed = extract_json(answer.text)
    got = parsed.get("scenes") if isinstance(parsed, dict) else None
    if not (isinstance(got, list) and len(got) == len(ideas)
            and all(isinstance(s, str) and s.strip() for s in got)):
        return None, "ответ не по форме: нужен список сцен той же длины"
    return [s.strip() for s in got], "из кэша" if answer.source == "cache" else "от модели"


def add_placeholders(doc, out_dir: str, gen: GeneratorConfig, rules=None,
                     slide_size: tuple[int, int] | None = None, rewrite=None):
    """Разделам с идеями — блок картинки, файла которой ещё нет.

    Правила отбора те же, что у `_pick_ideas` (`config/outline.json`,
    `image_ideas`): тип слайда из списка, у раздела нет картинки автора, не
    больше одной идеи на `slides_per_idea` разделов, по порядку колоды.
    `slide_size` задаёт заготовке наименьшее место (`min_place_side`): ранг
    ищет раскладку, где картинка встанет крупно, а не миниатюрой. `rewrite` —
    `scenes` с настройками модели: идеи → сцены без текста.

    Возвращает (документ, {путь заготовки: сюжет}, заметка или `None`)."""
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
    ideas = [s.image_idea.strip() for s in doc.sections if s.id in chosen]
    rewritten, how = rewrite(ideas) if rewrite else (None, "")
    subjects = iter(rewritten or ideas)
    wanted: dict[str, str] = {}
    sections = []
    for s in doc.sections:
        if s.id in chosen:
            ref = os.path.join(folder, f"{s.id}.png")
            wanted[ref] = next(subjects)
            s = replace(s, blocks=tuple(s.blocks) + (ContentBlock(
                id=f"{GENERATED_PREFIX}{s.id}", kind="image", ref=ref, min_side=min_side),))
        sections.append(s)
    note = None
    if rewritten:
        pairs = "; ".join(f"«{a}» → «{b}»" for a, b in zip(ideas, rewritten))
        note = f"Сюжеты картинок переписаны моделью без текста ({how}): {pairs} (Z-28)."
    elif rewrite:
        note = f"Сюжеты картинок не переписаны ({how}) — рисуется идея модели как есть (Z-28)."
    return replace(doc, sections=sections), wanted, note


def without(doc, refs) -> object:
    """Документ без заготовок `refs` — план без картинок, которых не вышло."""
    drop = set(refs)
    return replace(doc, sections=[
        replace(s, blocks=tuple(b for b in s.blocks if not (b.kind == "image" and b.ref in drop)))
        for s in doc.sections
    ])


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
    drawn: int = 0
    cached: int = 0
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)
    too_small: set[str] = field(default_factory=set)
    _slept: bool = False

    def _cache_path(self, key: str) -> str:
        root = self.gen.cache_root
        if not os.path.isabs(root):
            root = os.path.join(llm_cache.repo_root(), root)
        return os.path.join(root, key + ".png")

    def _key(self, prompt: str, width: int, height: int) -> str:
        blob = json.dumps({"model": self.gen.model, "prompt": prompt, "width": width, "height": height,
                           "steps": self.gen.steps, "cfg": self.gen.cfg_scale, "seed": self.gen.seed},
                          ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _wait_llm_sleep(self) -> None:
        """Ждём, пока `llama-server` отдаст видеопамять (`/props` → `is_sleeping`).
        Другой сервер этого поля не знает — ждать нечего."""
        if self._slept or not self.llm_base_url:
            return
        self._slept = True
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

    def _draw(self, prompt: str, width: int, height: int) -> bytes:
        self._wait_llm_sleep()
        url = self.gen.base_url.rstrip("/") + "/sdapi/v1/txt2img"
        body = {"prompt": prompt, "width": width, "height": height, "steps": self.gen.steps,
                "cfg_scale": self.gen.cfg_scale, "seed": self.gen.seed, "batch_size": 1}
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

    def picture(self, idea: str, width: int, height: int, target: str) -> bool:
        """Файл `target` с картинкой по идее: из кэша или от генератора."""
        prompt = idea.rstrip(" .") + self.gen.prompt_suffix
        cached = self._cache_path(self._key(prompt, width, height))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isfile(cached):
            shutil.copyfile(cached, target)
            self.cached += 1
            return True
        try:
            png = self._draw(prompt, width, height)
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as exc:
            reason = getattr(exc, "reason", exc)
            self.failures.append(f"«{idea}» {width}x{height}: {reason}")
            return False
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        with open(cached, "wb") as fh:
            fh.write(png)
        shutil.copyfile(cached, target)
        self.drawn += 1
        return True

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
                idea = self.wanted.get(fill.ref) if fill.kind == "image" else None
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

    def note(self) -> str | None:
        """Строка для предупреждений плана: сколько нарисовано и чем."""
        if not (self.drawn or self.cached or self.failures or self.too_small):
            return None
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
        return line
