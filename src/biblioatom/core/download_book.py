"""Use case: скачивание сырья книги в рабочий каталог (кэш для оффлайн-сборки).

Тянет сырые ответы сервера (HTML метаданных/TOC, JSON RPC страниц, JPEG-сканы)
через :class:`RawFetcherProtocol` и раскладывает их в ``workspace.raw_dir``.
После загрузки собирает распарсенный артефакт ``book.json`` — тем же
``fetch_book`` поверх переданного оффлайн-фетчера (``local``), поэтому формат
и best-effort-семантика полностью совпадают с командой ``fetch``.

Идемпотентность: уже скачанные файлы пропускаются (resume недокачанной книги);
``refresh=True`` перекачивает всё заново. Сбой отдельной страницы/скана не
обрывает загрузку (best-effort, как в ``fetch_book``).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from biblioatom.core.extract_scan_images import select_photo_pages
from biblioatom.core.fetch_book import book_payload, fetch_book, validate_page_range
from biblioatom.errors import FetchError, WorkspaceError
from biblioatom.logging_config import get_logger
from biblioatom.services import FetcherProtocol, ParserProtocol, RawFetcherProtocol
from biblioatom.services.workspace import BookWorkspace

_logger = get_logger(__name__)


@dataclass(slots=True)
class DownloadResult:
    """Итог загрузки сырья книги в рабочий каталог."""

    book_id: str
    title: str
    max_page: int
    pages_downloaded: int = 0
    pages_skipped: int = 0
    scans_downloaded: int = 0
    scans_skipped: int = 0
    failed_pages: list[int] = field(default_factory=list)
    failed_scans: list[int] = field(default_factory=list)


def _write_text(path: Path, text: str) -> None:
    """Записать текст в кэш; сбой I/O → :class:`WorkspaceError`."""

    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise WorkspaceError(
            "Failed to write to book workspace.", context={"path": str(path)}
        ) from exc


def _write_bytes(path: Path, data: bytes) -> None:
    """Записать байты в кэш; сбой I/O → :class:`WorkspaceError`."""

    try:
        path.write_bytes(data)
    except OSError as exc:
        raise WorkspaceError(
            "Failed to write to book workspace.", context={"path": str(path)}
        ) from exc


def download_book(
    network: RawFetcherProtocol,
    local: FetcherProtocol,
    parser: ParserProtocol,
    workspace: BookWorkspace,
    book_id: str,
    *,
    from_page: int = 0,
    to_page: int | None = None,
    delay_ms: int = 0,
    refresh: bool = False,
) -> DownloadResult:
    """Скачать сырьё книги в ``workspace`` и записать ``book.json``.

    :param network: сетевой источник сырых ответов (:class:`RawFetcherProtocol`).
    :param local: оффлайн-фетчер поверх того же workspace — для сборки
        ``book.json`` через общий ``fetch_book``.
    :param parser: парсер метаданных/TOC/страниц.
    :param workspace: рабочий каталог книги.
    :param book_id: идентификатор книги.
    :param from_page: первая страница диапазона (0-based, включительно).
    :param to_page: последняя страница (включительно); ``None`` → до ``max_page``.
    :param delay_ms: пауза между сетевыми запросами, мс.
    :param refresh: перекачать заново даже при наличии файлов в кэше.
    :raises InputValidationError: при некорректном диапазоне страниц.
    :raises WorkspaceError: при сбое записи в рабочий каталог.
    """

    started = time.perf_counter()
    workspace.ensure_dirs()

    if refresh or not workspace.meta_path.is_file():
        _write_text(workspace.meta_path, network.fetch_book_meta_raw(book_id))
    meta = parser.parse_book_meta(workspace.meta_path.read_text(encoding="utf-8"), book_id)
    resolved_to = meta.max_page if to_page is None else to_page
    validate_page_range(from_page, resolved_to, meta.max_page)

    if refresh or not workspace.toc_path.is_file():
        _write_text(workspace.toc_path, network.fetch_toc_raw(book_id))

    result = DownloadResult(book_id=book_id, title=meta.title, max_page=meta.max_page)
    _logger.info(
        "download_book.start",
        book_id=book_id,
        from_page=from_page,
        to_page=resolved_to,
        refresh=refresh,
    )

    for page in range(from_page, resolved_to + 1):
        path = workspace.page_path(page)
        if path.is_file() and not refresh:
            result.pages_skipped += 1
            continue
        try:
            raw = network.fetch_page_raw(book_id, page)
        except FetchError as exc:
            # Best-effort: сбой страницы не обрывает загрузку книги.
            result.failed_pages.append(page)
            _logger.warning("download_book.page_failed", page=page, error=str(exc))
            continue
        _write_text(path, raw)
        result.pages_downloaded += 1
        if delay_ms > 0 and page < resolved_to:
            time.sleep(delay_ms / 1000.0)

    # Распарсенный артефакт book.json — через общий fetch_book поверх кэша.
    book = fetch_book(local, parser, book_id, from_page=from_page, to_page=resolved_to)
    result.failed_pages = sorted(set(result.failed_pages) | set(book.failed_pages))
    _write_text(
        workspace.book_json_path,
        json.dumps(book_payload(book), ensure_ascii=False, indent=2),
    )

    # Сканы: обложка + фото-страницы (по подписи CAPTION).
    cdn_pages = [p.page for p in book.pages if p.is_cover][:1]
    cdn_pages += [photo.cdn_page for photo in select_photo_pages(book.pages)]
    for cdn in cdn_pages:
        scan = workspace.scan_path(cdn)
        if scan.is_file() and not refresh:
            result.scans_skipped += 1
            continue
        try:
            data = network.fetch_image(book_id, cdn)
        except FetchError as exc:
            result.failed_scans.append(cdn)
            _logger.warning("download_book.scan_failed", cdn_page=cdn, error=str(exc))
            continue
        _write_bytes(scan, data)
        result.scans_downloaded += 1
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    _logger.info(
        "download_book.done",
        book_id=book_id,
        pages_downloaded=result.pages_downloaded,
        pages_skipped=result.pages_skipped,
        scans_downloaded=result.scans_downloaded,
        failed_pages=len(result.failed_pages),
        failed_scans=len(result.failed_scans),
        duration_ms=duration_ms,
    )
    return result


__all__ = ["DownloadResult", "download_book"]
