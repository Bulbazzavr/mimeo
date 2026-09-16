"""Чтение OPC-пакета.

Этот пакет знает про ZIP, части и связи — и не знает про презентации, слайды и
цвета. См. ARCH, таблицу границ модулей.
"""

from .package import Package, PackageError, Relationship, open_package

__all__ = ["Package", "PackageError", "Relationship", "open_package"]
