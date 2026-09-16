from __future__ import annotations

import pytest

from tests.fixtures.build_fixture import build, build_multi


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
