"""Версии конфигов: что за файл, какой он версии и каким был байт в байт.

Задача `Z-33`, план `PLAN-7.0`, решение `ADR-0022`. Требование ТЗ, раздел 2,
п. 4: «Версионирование скиллов и агентов, лежащих в основе воркфлоу».

Носителей версии **три** (`ADR-0022`), и здесь живут два из них:

* `version` внутри файла — её ставит человек, и она отмечает, что изменение
  было **намеренным**, и даёт ему имя, на которое можно сослаться;
* `sha256` файла — его снимает машина, и он ловит **всякое** изменение, в том
  числе случайное и забытое.

Третий носитель — замо́к `config/versions.lock.json` — лежит не здесь, а в
`tools/config_version.py`: он помнит, каким было содержимое в момент, когда
номер объявляли, и потому ловит расхождение между первыми двумя.

Оба уезжают в `deck-plan.json`, в `source.configs`. Тогда «воспроизводимый
прогон» из критерия 2 перестаёт быть обещанием: по артефакту видно не только,
какой шаблон разложили, но и **чем**.

Здесь нет чтения самих настроек — только их паспорт. Значения читают
`plan/prose.py`, `plan/variants.py`, `plan/client.py`, `plan/prompt.py`,
`analyze/typeface.py`, `analyze/picture.py` и `analyze/captions.py`, каждый по-своему и
каждый со своими встроенными запасными.
"""

from __future__ import annotations

import hashlib
import json
import os

from .model import ConfigStamp

#: Конфиги движка. Порядок здесь не важен — наружу они уходят отсортированными
#: по имени, чтобы артефакт был побайтово одинаков от прогона к прогону.
#:
#: Список намеренно перечислен руками, а не собран обходом каталога: файл,
#: который движок не читает, не должен попадать в паспорт прогона и создавать
#: впечатление, будто он на что-то влиял.
KNOWN = (
    "audit.json",
    "generator.json",
    "images.json",
    "kinds.json",
    "model.json",
    "outline.json",
    "prompt.json",
    "prose.json",
    # Движок его не читает — читает установка (`tools/runtime.py`, `Z-76`), — но
    # он называет веса и серверы, которыми колода собрана: по паспорту прогона
    # видно, какой Gemma и каким генератором (sha256 файлов в манифесте).
    "runtime.json",
    "typography.json",
    "variants.json",
)


def config_root() -> str:
    """`config/` рядом с пакетом: `mimeo/` лежит в корне репозитория.

    То же допущение, что в `plan/prose.py` и `plan/client.py`, и оно верно,
    пока проект сдаётся репозиторием, а не колесом (`ADR-0022`)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "config")


def path_for(name: str) -> str:
    return os.path.join(config_root(), name)


def declared_version(raw: object) -> str:
    """Версия из уже прочитанного JSON. Не строка — значит не объявлена."""
    if isinstance(raw, dict):
        value = raw.get("version")
        if isinstance(value, str):
            return value
    return ""


def stamp_file(path: str, name: str | None = None) -> ConfigStamp:
    """Паспорт произвольного файла. Годится и для конфига прогона, который
    лежит не в `config/` (флаг `--config`, `PLAN-7.0` Ш10)."""
    name = name if name is not None else os.path.basename(path)
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
    except OSError:
        return ConfigStamp(name=name, source=path)

    digest = hashlib.sha256(blob).hexdigest()
    try:
        version = declared_version(json.loads(blob.decode("utf-8")))
    except (ValueError, UnicodeDecodeError):
        # Файл есть и хэш честный, а прочитать не смогли. Версия неизвестна —
        # это и пишем, вместо того чтобы выдать отсутствие за ноль.
        version = ""
    return ConfigStamp(name=name, version=version, sha256=digest, loaded=True, source=path)


def stamp(name: str) -> ConfigStamp:
    """Паспорт конфига из `config/` по имени файла."""
    return stamp_file(path_for(name), name)


def stamps(names: tuple[str, ...] = KNOWN) -> tuple[ConfigStamp, ...]:
    """Паспорта всех конфигов движка, отсортированные по имени.

    Отдаются **все** известные, а не только прочитанные на этом прогоне:
    чтобы повторить прогон, нужны они все, а какой из них на что повлиял —
    вопрос другой и отвечает на него `diagnostics`."""
    return tuple(stamp(n) for n in sorted(names))
