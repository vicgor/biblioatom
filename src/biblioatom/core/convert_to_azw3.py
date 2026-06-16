"""Use case: конвертация EPUB → AZW3 через внешний конвертер.

Тонкая оркестрация: путь к EPUB передаётся реализации
:class:`~biblioatom.services.ConverterProtocol`, которая выполняет конвертацию и
возвращает путь к результату. Use case оборачивает результат в
:class:`~biblioatom.models.BuildResult`.

Зависимость (converter) внедряется через Protocol (Dependency Inversion) — use
case не знает про subprocess/Calibre.
"""

from __future__ import annotations

from pathlib import Path

from biblioatom.logging_config import get_logger
from biblioatom.models import BuildResult
from biblioatom.services import ConverterProtocol

_logger = get_logger(__name__)


def convert_to_azw3(
    converter: ConverterProtocol,
    source: Path,
    target: Path,
    *,
    book_id: str = "",
) -> BuildResult:
    """Сконвертировать EPUB в AZW3 и вернуть результат сборки.

    :param converter: реализация :class:`ConverterProtocol`.
    :param source: путь к исходному ``.epub``.
    :param target: путь к итоговому ``.azw3``.
    :param book_id: идентификатор книги для :class:`BuildResult` (опционально).
    """

    _logger.info("convert_to_azw3.start", source=str(source), target=str(target))
    out = converter.convert(source, target)
    _logger.info("convert_to_azw3.done", target=str(out))
    return BuildResult(book_id=book_id, outputs=[out])


__all__ = ["convert_to_azw3"]
