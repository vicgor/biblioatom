"""Use case: полный пайплайн обработки книги.

Оркестрирует существующие core use cases в единый сквозной сценарий:

    fetch_book → analyze_structure → [extract_scan_images] → build_epub → [convert_to_azw3]

Все зависимости-сервисы внедряются через Protocol-интерфейсы (Dependency
Inversion) — use case не знает про httpx/selectolax/EbookLib/Calibre и не
содержит CLI/I/O-деталей (вывод, прогресс-бары и т.п. остаются в слое CLI).

Извлечение иллюстраций со сканов и конвертация в AZW3 опциональны: они
выполняются только если переданы соответствующие зависимости и включены флагами.
Прогресс пишется через structlog с ``correlation_id`` и ``duration_ms``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from biblioatom.core.analyze_structure import analyze_structure
from biblioatom.core.build_epub import build_epub
from biblioatom.core.convert_to_azw3 import convert_to_azw3
from biblioatom.core.extract_scan_images import (
    ScanExtractionResult,
    extract_scan_images,
    select_photo_pages,
)
from biblioatom.core.fetch_book import FetchedBook, fetch_book
from biblioatom.errors import FetchError, InputValidationError
from biblioatom.logging_config import get_logger, set_correlation_id
from biblioatom.models import ImageAsset
from biblioatom.services import (
    ConverterProtocol,
    EpubBuilderProtocol,
    FetcherProtocol,
    ImageProcessorProtocol,
    ParserProtocol,
    ScanExtractorProtocol,
    StructureAnalyzerProtocol,
)

_logger = get_logger(__name__)


@dataclass(slots=True)
class PipelineResult:
    """Результат полного пайплайна.

    ``epub_path`` — собранный EPUB; ``azw3_path`` — результат конвертации (если
    выполнялась). ``images`` — встроенные ассеты иллюстраций. ``failed_pages`` и
    ``failed_scans`` отражают best-effort-сбои на этапах загрузки/извлечения.
    """

    book_id: str
    title: str
    epub_path: Path
    azw3_path: Path | None = None
    images: list[ImageAsset] = field(default_factory=list)
    chapters: int = 0
    failed_pages: list[int] = field(default_factory=list)
    failed_scans: list[Path] = field(default_factory=list)
    duration_ms: float = 0.0


def _extract_images(
    fetcher: FetcherProtocol,
    scan_extractor: ScanExtractorProtocol,
    image_processor: ImageProcessorProtocol,
    book: FetchedBook,
    images_dir: Path,
) -> ScanExtractionResult:
    """Скачать сканы фото-страниц и извлечь из них иллюстрации.

    Сканы тянутся через ``fetcher.fetch_image`` (best-effort: сбойный лист не
    рвёт пайплайн), сохраняются во временные файлы внутри ``images_dir`` и
    передаются в :func:`extract_scan_images`.
    """

    images_dir.mkdir(parents=True, exist_ok=True)
    photo_pages = select_photo_pages(book.pages)
    _logger.info("run_pipeline.scan_pages_selected", count=len(photo_pages))

    scans: list[tuple[int, Path]] = []
    for photo in photo_pages:
        try:
            data = fetcher.fetch_image(book.book_id, photo.cdn_page)
        except FetchError as exc:
            _logger.warning(
                "run_pipeline.scan_fetch_failed",
                page=photo.page,
                cdn_page=photo.cdn_page,
                error=str(exc),
            )
            continue
        raw_path = images_dir / f"{photo.page:04d}_raw.bin"
        raw_path.write_bytes(data)
        scans.append((photo.page, raw_path))

    return extract_scan_images(scan_extractor, image_processor, scans, images_dir)


def run_pipeline(
    *,
    fetcher: FetcherProtocol,
    parser: ParserProtocol,
    analyzer: StructureAnalyzerProtocol,
    epub_builder: EpubBuilderProtocol,
    book_id: str,
    out_path: Path,
    source: str | None = None,
    from_page: int = 0,
    to_page: int | None = None,
    delay_ms: int = 0,
    extract_images: bool = False,
    scan_extractor: ScanExtractorProtocol | None = None,
    image_processor: ImageProcessorProtocol | None = None,
    images_dir: Path | None = None,
    convert_azw3: bool = False,
    converter: ConverterProtocol | None = None,
    azw3_path: Path | None = None,
) -> PipelineResult:
    """Прогнать полный пайплайн: загрузка → анализ → (сканы) → EPUB → (AZW3).

    :param fetcher: источник данных (:class:`FetcherProtocol`).
    :param parser: парсер содержимого страниц (:class:`ParserProtocol`).
    :param analyzer: структурный анализатор (:class:`StructureAnalyzerProtocol`).
    :param epub_builder: сборщик EPUB (:class:`EpubBuilderProtocol`).
    :param book_id: идентификатор книги.
    :param out_path: путь итогового ``.epub``.
    :param source: источник (URL/идентификатор) для метаданных, опционально.
    :param from_page: первая страница диапазона (0-based, включительно).
    :param to_page: последняя страница (включительно); ``None`` → до ``max_page``.
    :param delay_ms: пауза между запросами страниц, мс.
    :param extract_images: извлекать ли иллюстрации со сканов.
    :param scan_extractor: извлекатель сканов; обязателен при ``extract_images``.
    :param image_processor: постобработчик; обязателен при ``extract_images``.
    :param images_dir: каталог для скачанных сканов и кропов.
    :param convert_azw3: конвертировать ли EPUB в AZW3 после сборки.
    :param converter: конвертер; обязателен при ``convert_azw3``.
    :param azw3_path: путь итогового ``.azw3``; ``None`` → рядом с EPUB.
    :raises InputValidationError: при некорректной комбинации опций/зависимостей.
    """

    set_correlation_id(uuid.uuid4().hex)
    started = time.perf_counter()
    _logger.info("run_pipeline.start", book_id=book_id, out_path=str(out_path))

    if extract_images and (scan_extractor is None or image_processor is None):
        raise InputValidationError(
            "Image extraction requires both a scan extractor and an image processor.",
            context={"book_id": book_id},
        )
    if convert_azw3 and converter is None:
        raise InputValidationError(
            "AZW3 conversion requires a converter service.",
            context={"book_id": book_id},
        )

    book = fetch_book(
        fetcher,
        parser,
        book_id,
        from_page=from_page,
        to_page=to_page,
        delay_ms=delay_ms,
    )

    document = analyze_structure(
        analyzer,
        book.pages,
        book.toc,
        title=book.title,
        book_id=book.book_id,
        source=source,
    )

    images: list[ImageAsset] = []
    failed_scans: list[Path] = []
    if extract_images:
        assert scan_extractor is not None
        assert image_processor is not None
        target_dir = images_dir or out_path.parent / "images"
        scan_result = _extract_images(fetcher, scan_extractor, image_processor, book, target_dir)
        images = scan_result.images
        failed_scans = scan_result.failed_scans

    build_result = build_epub(epub_builder, document, out_path, images=images)
    epub_path = build_result.outputs[0] if build_result.outputs else out_path

    azw3_out: Path | None = None
    if convert_azw3:
        assert converter is not None
        azw3_out = azw3_path or epub_path.with_suffix(".azw3")
        convert_to_azw3(converter, epub_path, azw3_out, book_id=book.book_id)

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    _logger.info(
        "run_pipeline.done",
        book_id=book.book_id,
        epub_path=str(epub_path),
        azw3_path=str(azw3_out) if azw3_out else None,
        chapters=len(document.chapters),
        images=len(images),
        failed_pages=len(book.failed_pages),
        duration_ms=duration_ms,
    )

    return PipelineResult(
        book_id=book.book_id,
        title=book.title,
        epub_path=epub_path,
        azw3_path=azw3_out,
        images=images,
        chapters=len(document.chapters),
        failed_pages=book.failed_pages,
        failed_scans=failed_scans,
        duration_ms=duration_ms,
    )


__all__ = ["PipelineResult", "run_pipeline"]
