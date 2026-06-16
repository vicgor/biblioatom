"""Use case: структурный анализ загруженной книги.

Тонкая оркестрация: страницы (уже разобранные парсером в ``fetch_book``) и
оглавление передаются в ``StructureAnalyzerProtocol``, который строит главы.
Результат дополняется метаданными книги (заголовок, ``book_id``, источник).

Use case не зависит от конкретных реализаций — анализатор внедряется через
Protocol (Dependency Inversion).
"""

from __future__ import annotations

from biblioatom.models import PageModel, StructuredDocument, TocEntry
from biblioatom.services import StructureAnalyzerProtocol


def analyze_structure(
    analyzer: StructureAnalyzerProtocol,
    pages: list[PageModel],
    toc: list[TocEntry],
    *,
    title: str,
    book_id: str,
    source: str | None = None,
) -> StructuredDocument:
    """Построить структурированный документ из страниц и оглавления.

    :param analyzer: реализация :class:`StructureAnalyzerProtocol`.
    :param pages: страницы книги (с уже извлечёнными блоками).
    :param toc: оглавление; при наличии используется для разбивки на главы.
    :param title: заголовок книги (метаданные).
    :param book_id: идентификатор книги.
    :param source: источник (URL/файл), опционально.
    """

    document = analyzer.analyze(pages, toc)
    # Анализатор не знает метаданных книги (работает только со страницами/TOC),
    # поэтому проставляем их здесь.
    document.title = title
    document.book_id = book_id
    document.source = source
    return document


__all__ = ["analyze_structure"]
