"""Use case: полный пайплайн обработки книги.

Оркестрирует существующие core use cases в единый сквозной сценарий:

    [download_book] → fetch_book → analyze_structure → [extract_scan_images]
        → build_epub → [convert_to_azw3]

Все зависимости-сервисы внедряются через Protocol-интерфейсы (Dependency
Inversion) — use case не знает про httpx/selectolax/EbookLib/Calibre и не
содержит CLI/I/O-деталей (вывод, прогресс-бары и т.п. остаются в слое CLI).

Сборка всегда идёт из рабочего каталога книги (:class:`BookWorkspace`) через
оффлайн-фетчер (``fetcher``, обычно :class:`~biblioatom.services.local_fetcher.LocalFetcher`).
Если сырья ещё нет (``workspace.has_raw()`` — false) или запрошен ``refresh``,
пайплайн сначала авто-скачивает его через ``network_fetcher``
(:func:`~biblioatom.core.download_book.download_book`); без сети и без кэша —
:class:`InputValidationError`. Сканы для извлечения иллюстраций читаются
напрямую из ``workspace.scans_dir`` — промежуточные копии в ``images/`` больше
не создаются.

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
from biblioatom.core.download_book import download_book
from biblioatom.core.extract_scan_images import (
    ScanExtractionResult,
    extract_scan_images,
    select_photo_pages,
)
from biblioatom.core.fetch_book import FetchedBook, fetch_book
from biblioatom.errors import FetchError, ImageProcessingError, InputValidationError
from biblioatom.logging_config import get_logger, set_correlation_id
from biblioatom.models import BoundingBox, ExtractedImage, ImageAsset
from biblioatom.services import (
    ConverterProtocol,
    EpubBuilderProtocol,
    FetcherProtocol,
    ImageProcessorProtocol,
    ParserProtocol,
    RawFetcherProtocol,
    ScanExtractorProtocol,
    StructureAnalyzerProtocol,
)
from biblioatom.services.workspace import BookWorkspace

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
    workspace: BookWorkspace,
) -> ScanExtractionResult:
    """Извлечь иллюстрации из закэшированных сканов рабочего каталога.

    Сканы читаются напрямую из ``workspace.scans_dir`` (заполняется download) —
    промежуточные копии ``*_raw.jpg`` больше не создаются. Отсутствующий файл
    скана попадает в ``failed_scans`` (best-effort, внутри
    ``extract_scan_images``). Обложка приходит через ``fetcher.fetch_image``
    (оффлайн-фетчер) и проходит тот же ImageProcessor, что и сканы.
    """

    images_dir = workspace.images_dir
    images_dir.mkdir(parents=True, exist_ok=True)

    cover_assets: list[ImageAsset] = []
    cover_pages = [p for p in book.pages if p.is_cover]
    if len(cover_pages) > 1:
        _logger.warning(
            "run_pipeline.multiple_cover_pages",
            count=len(cover_pages),
            pages=[p.page for p in cover_pages],
        )
    for cover in cover_pages[:1]:
        try:
            data = fetcher.fetch_image(book.book_id, cover.page)
        except FetchError as exc:
            _logger.warning(
                "run_pipeline.cover_fetch_failed",
                cdn_page=cover.page,
                error=str(exc),
            )
            continue
        # Обложка проходит тот же ImageProcessor, что и остальные сканы
        # (ресайз/нормализация из ImageSettings). Поле box обязательно моделью,
        # но process() его не использует — ставим заглушку 1×1 (core-слой не
        # тянет Pillow и не может декодировать реальные размеры обложки).
        crop = ExtractedImage(
            page=cover.page,
            data=data,
            box=BoundingBox(x=0, y=0, width=1, height=1),
        )
        out_path = images_dir / f"{cover.page:04d}_cover"
        try:
            asset = image_processor.process(crop, out_path)
        except ImageProcessingError as exc:
            # Best-effort: сбой обработки обложки не рвёт пайплайн.
            _logger.warning(
                "run_pipeline.cover_process_failed",
                cdn_page=cover.page,
                error=str(exc),
            )
            continue
        cover_assets.append(asset)
        _logger.info("run_pipeline.cover_fetched", cdn_page=cover.page)

    photo_pages = select_photo_pages(book.pages)
    _logger.info("run_pipeline.scan_pages_selected", count=len(photo_pages))
    scans = [(photo.page, workspace.scan_path(photo.cdn_page)) for photo in photo_pages]

    result = extract_scan_images(scan_extractor, image_processor, scans, images_dir)
    result.images = cover_assets + result.images
    return result


def run_pipeline(
    *,
    fetcher: FetcherProtocol,
    parser: ParserProtocol,
    analyzer: StructureAnalyzerProtocol,
    epub_builder: EpubBuilderProtocol,
    workspace: BookWorkspace,
    book_id: str,
    out_path: Path | None = None,
    network_fetcher: RawFetcherProtocol | None = None,
    refresh: bool = False,
    source: str | None = None,
    from_page: int = 0,
    to_page: int | None = None,
    delay_ms: int = 0,
    extract_images: bool = False,
    scan_extractor: ScanExtractorProtocol | None = None,
    image_processor: ImageProcessorProtocol | None = None,
    convert_azw3: bool = False,
    converter: ConverterProtocol | None = None,
    azw3_path: Path | None = None,
) -> PipelineResult:
    """Прогнать полный пайплайн: [загрузка] → анализ → (сканы) → EPUB → (AZW3).

    :param fetcher: оффлайн-источник данных поверх ``workspace``
        (:class:`FetcherProtocol`, обычно ``LocalFetcher``).
    :param parser: парсер содержимого страниц (:class:`ParserProtocol`).
    :param analyzer: структурный анализатор (:class:`StructureAnalyzerProtocol`).
    :param epub_builder: сборщик EPUB (:class:`EpubBuilderProtocol`).
    :param workspace: рабочий каталог книги (сырьё/кэш/итоговые файлы).
    :param book_id: идентификатор книги.
    :param out_path: путь итогового ``.epub``; ``None`` → ``workspace.epub_path``.
    :param network_fetcher: сетевой источник сырых ответов
        (:class:`RawFetcherProtocol`); используется только для авто-download,
        когда в ``workspace`` ещё нет кэша (или запрошен ``refresh``).
    :param refresh: перекачать сырьё заново, даже если кэш уже есть
        (требует ``network_fetcher``).
    :param source: источник (URL/идентификатор) для метаданных, опционально.
    :param from_page: первая страница диапазона (0-based, включительно).
    :param to_page: последняя страница (включительно); ``None`` → до ``max_page``.
    :param delay_ms: пауза между сетевыми запросами при авто-download, мс.
    :param extract_images: извлекать ли иллюстрации со сканов.
    :param scan_extractor: извлекатель сканов; обязателен при ``extract_images``.
    :param image_processor: постобработчик; обязателен при ``extract_images``.
    :param convert_azw3: конвертировать ли EPUB в AZW3 после сборки.
    :param converter: конвертер; обязателен при ``convert_azw3``.
    :param azw3_path: путь итогового ``.azw3``; ``None`` → рядом с EPUB.
    :raises InputValidationError: при некорректной комбинации опций/зависимостей
        либо при отсутствии кэша и ``network_fetcher`` одновременно.
    """

    set_correlation_id(uuid.uuid4().hex)
    started = time.perf_counter()
    _logger.info("run_pipeline.start", book_id=book_id)

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

    if refresh or not workspace.has_raw():
        if network_fetcher is None:
            raise InputValidationError(
                "No cached raw data for this book; "
                "run `biblioatom download` first or provide a network fetcher.",
                context={"book_id": book_id, "raw_dir": str(workspace.raw_dir)},
            )
        download_book(
            network_fetcher,
            fetcher,
            parser,
            workspace,
            book_id,
            from_page=from_page,
            to_page=to_page,
            delay_ms=delay_ms,
            refresh=refresh,
        )

    # Оффлайн-сборка: fetcher — LocalFetcher, задержки не нужны.
    book = fetch_book(fetcher, parser, book_id, from_page=from_page, to_page=to_page)

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
        # Валидация уже выполнена выше (InputValidationError при None), но mypy
        # не умеет проследить эту связь через флаг — сужаем тип явно.
        if scan_extractor is None or image_processor is None:
            raise InputValidationError(
                "Image extraction requires both a scan extractor and an image processor.",
                context={"book_id": book_id},
            )
        scan_result = _extract_images(fetcher, scan_extractor, image_processor, book, workspace)
        images = scan_result.images
        failed_scans = scan_result.failed_scans

    epub_out = out_path or workspace.epub_path
    epub_out.parent.mkdir(parents=True, exist_ok=True)
    build_result = build_epub(epub_builder, document, epub_out, images=images)
    epub_path = build_result.outputs[0] if build_result.outputs else epub_out

    azw3_out: Path | None = None
    if convert_azw3:
        if converter is None:
            raise InputValidationError(
                "AZW3 conversion requires a converter service.",
                context={"book_id": book_id},
            )
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
