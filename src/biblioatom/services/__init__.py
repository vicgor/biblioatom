"""Protocol-интерфейсы сервисного слоя (Dependency Inversion).

Здесь объявлены только контракты (``typing.Protocol``) — без реализаций.
Конкретные реализации (httpx-fetcher, selectolax-parser, OpenCV-extractor и
т.д.) появятся в следующих этапах миграции и будут проверяться на соответствие
этим протоколам структурно.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from biblioatom.models import (
    BuildResult,
    EmbeddedContent,
    ImageAsset,
    PageModel,
    StructuredDocument,
    TocEntry,
)


@runtime_checkable
class FetcherProtocol(Protocol):
    """Источник данных книги (сеть)."""

    def fetch_book_meta(self, book_id: str) -> tuple[str, int]:
        """Вернуть ``(title, max_page)`` для книги."""
        ...

    def fetch_toc(self, book_id: str) -> list[TocEntry]:
        """Вернуть оглавление книги."""
        ...

    def fetch_page(self, book_id: str, page: int) -> EmbeddedContent:
        """Вернуть содержимое одной страницы."""
        ...

    def fetch_image(self, book_id: str, page: int) -> bytes:
        """Вернуть байты изображения страницы (скан)."""
        ...


@runtime_checkable
class ParserProtocol(Protocol):
    """Разбор сырого HTML/JSON в доменные модели."""

    def parse_embedded_content(self, raw: str | dict[str, object]) -> EmbeddedContent:
        """Разобрать поле ``content`` страницы."""
        ...

    def page_to_model(self, page: int, content: EmbeddedContent) -> PageModel:
        """Построить :class:`PageModel` из содержимого страницы."""
        ...


@runtime_checkable
class StructureAnalyzerProtocol(Protocol):
    """Структурный анализ: разбивка страниц на главы."""

    def analyze(self, pages: list[PageModel], toc: list[TocEntry]) -> StructuredDocument:
        """Построить структурированный документ из страниц и оглавления."""
        ...


@runtime_checkable
class EpubBuilderProtocol(Protocol):
    """Сборка EPUB из структурированного документа."""

    def build(
        self,
        document: StructuredDocument,
        out_path: Path,
        images: list[ImageAsset] | None = None,
    ) -> BuildResult:
        """Собрать EPUB и вернуть результат сборки."""
        ...


@runtime_checkable
class ConverterProtocol(Protocol):
    """Конвертация EPUB в другой формат (например, AZW3)."""

    def convert(self, source: Path, target: Path) -> Path:
        """Сконвертировать ``source`` в ``target`` и вернуть путь результата."""
        ...


__all__ = [
    "ConverterProtocol",
    "EpubBuilderProtocol",
    "FetcherProtocol",
    "ParserProtocol",
    "StructureAnalyzerProtocol",
]
