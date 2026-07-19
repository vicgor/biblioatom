"""Protocol-интерфейсы сервисного слоя (Dependency Inversion).

Здесь объявлены только контракты (``typing.Protocol``) — без реализаций.
Конкретные реализации появятся в следующих этапах миграции и будут проверяться
на соответствие этим протоколам структурно.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from biblioatom.models import (
    BookMeta,
    BuildResult,
    EmbeddedContent,
    ExtractedImage,
    ImageAsset,
    PageModel,
    StructuredDocument,
    TocEntry,
)


@runtime_checkable
class FetcherProtocol(Protocol):
    """Источник данных книги (сеть)."""

    def fetch_book_meta(self, book_id: str) -> BookMeta:
        """Вернуть метаданные книги (:class:`BookMeta`)."""
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
class RawFetcherProtocol(Protocol):
    """Источник сырых (неразобранных) ответов сервера — для кэша на диске."""

    def fetch_book_meta_raw(self, book_id: str) -> str:
        """Вернуть сырой HTML страницы книги."""
        ...

    def fetch_toc_raw(self, book_id: str) -> str:
        """Вернуть сырой HTML страницы p0 (оглавление)."""
        ...

    def fetch_page_raw(self, book_id: str, page: int) -> str:
        """Вернуть сырой текст RPC-ответа страницы."""
        ...

    def fetch_image(self, book_id: str, page: int) -> bytes:
        """Вернуть байты изображения страницы (скан)."""
        ...


@runtime_checkable
class ParserProtocol(Protocol):
    """Разбор сырого HTML/JSON в доменные модели."""

    def parse_book_meta(self, html: str, book_id: str) -> BookMeta:
        """Извлечь метаданные книги из HTML."""
        ...

    def parse_toc(self, html: str) -> list[TocEntry]:
        """Разобрать оглавление книги из HTML."""
        ...

    def parse_embedded_content(self, raw: str | dict[str, object]) -> EmbeddedContent:
        """Разобрать поле ``content`` страницы.

        ``raw`` — либо JSON-строка, либо уже распарсенный словарь
        (поддерживаются оба формата).
        """
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


@runtime_checkable
class ScanExtractorProtocol(Protocol):
    """Извлечение фото/иллюстраций со скана страницы (OpenCV, без OCR)."""

    def extract(self, image: bytes, page: int) -> list[ExtractedImage]:
        """Найти прямоугольные иллюстрации на скане и вернуть кропы.

        :param image: закодированные байты исходного скана (PNG/JPEG).
        :param page: номер страницы (проставляется в результат).
        """
        ...


@runtime_checkable
class ImageProcessorProtocol(Protocol):
    """Постобработка извлечённого кропа (Pillow): нормализация/ресайз/сохранение."""

    def process(self, image: ExtractedImage, out_path: Path) -> ImageAsset:
        """Постобработать кроп и сохранить его в ``out_path``, вернуть ассет."""
        ...


__all__ = [
    "ConverterProtocol",
    "EpubBuilderProtocol",
    "FetcherProtocol",
    "ImageProcessorProtocol",
    "ParserProtocol",
    "RawFetcherProtocol",
    "ScanExtractorProtocol",
    "StructureAnalyzerProtocol",
]
