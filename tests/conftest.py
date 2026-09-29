from __future__ import annotations

import os

import pytest

from tests.fixtures.build_fixture import build, build_multi

# С вечера 26 сентября сборка по умолчанию зовёт модель (`config/model.json`,
# `access: on`). Тесты модель не зовут: при поднятом сервере сборка прозы без
# `--llm` пошла бы в него и записала ответ в `cache/llm/`. Переменная старше
# конфига (`mimeo.plan.client.ACCESS_ENV`); тест, которому нужно умолчание
# конфига, снимает её сам.
os.environ["MIMEO_LLM_ACCESS"] = "off"
# То же для генератора картинок (`mimeo.plan.images.ACCESS_ENV`): тест с
# принятым ответом поддельной модели иначе пошёл бы в поднятый sd-server.
os.environ["MIMEO_IMAGES_ACCESS"] = "off"
# Подключение из окна «Модели» (29.09) тестам не нужно: заданное на машине
# подменило бы адрес и имя модели в ключах кэша и в сверках конфига.
for _name in ("MIMEO_LLM_BASE_URL", "MIMEO_LLM_MODEL", "MIMEO_LLM_CONTRACT",
              "MIMEO_LLM_EXTRA_BODY", "MIMEO_IMAGES_BASE_URL"):
    os.environ.pop(_name, None)


@pytest.fixture(scope="session")
def template_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Шаблон на два слайда: наследование, цвет, геометрия."""
    return build(tmp_path_factory.mktemp("fixture") / "minimal.pptx")


@pytest.fixture(scope="session")
def multi_template_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Шаблон на девять слайдов с двумя повторяющимися раскладками.

    Разметка по построению (см. tests/fixtures/build_fixture.py):
    0 обложка, 1-2 списки, 3-4 карточки, 5 метрика, 6 раздел,
    7 две колонки, 8 финал.
    """
    return build_multi(tmp_path_factory.mktemp("fixture") / "multi.pptx")
