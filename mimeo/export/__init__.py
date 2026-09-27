"""Выгрузка готовой колоды в другие форматы (ТЗ, п. 7).

`.html` — свой рендер пакета на стандартной библиотеке (`html.py`): эксперты
запускают движок на Linux (`ADR-0001`). `.pdf` рисует PowerPoint или
LibreOffice (`pdf.py`); оба формата сразу — `run.py`.
"""

from .html import HtmlReport, render_html, to_html

__all__ = ["HtmlReport", "render_html", "to_html"]
