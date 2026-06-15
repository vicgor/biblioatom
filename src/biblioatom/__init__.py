"""Пакет biblioatom: скачивание и конвертация книг с elib.biblioatom.ru."""

from __future__ import annotations

import warnings
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("biblioatom")
except PackageNotFoundError:  # пакет не установлен (например, запуск из исходников)
    warnings.warn(
        "Не удалось определить версию пакета biblioatom (PackageNotFoundError); "
        "используется dev-версия '0.0.0+unknown'.",
        stacklevel=2,
    )
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
