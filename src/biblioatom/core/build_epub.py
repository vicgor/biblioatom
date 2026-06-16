"""Use case: сборка EPUB из структурированного документа.

Тонкая оркестрация: структурированный документ (+ ассеты изображений)
передаётся реализации :class:`~biblioatom.services.EpubBuilderProtocol`,
которая собирает EPUB и возвращает :class:`~biblioatom.models.BuildResult`.

Зависимость (builder) внедряется через Protocol (Dependency Inversion) — use
case не зависит от EbookLib напрямую.
"""

from __future__ import annotations

from pathlib import Path

from biblioatom.logging_config import get_logger
from biblioatom.models import BuildResult, ImageAsset, StructuredDocument
from biblioatom.services import EpubBuilderProtocol

_logger = get_logger(__name__)


def build_epub(
    builder: EpubBuilderProtocol,
    document: StructuredDocument,
    out_path: Path,
    *,
    images: list[ImageAsset] | None = None,
) -> BuildResult:
    """Собрать EPUB из структурированного документа.

    :param builder: реализация :class:`EpubBuilderProtocol`.
    :param document: структурированный документ (главы, TOC, метаданные).
    :param out_path: путь итогового ``.epub``.
    :param images: ассеты изображений для встраивания (опционально).
    """

    _logger.info(
        "build_epub.start",
        book_id=document.book_id,
        chapters=len(document.chapters),
        out_path=str(out_path),
    )
    result = builder.build(document, out_path, images)
    _logger.info(
        "build_epub.done",
        book_id=document.book_id,
        outputs=[str(p) for p in result.outputs],
        images=len(result.images),
    )
    return result


__all__ = ["build_epub"]
