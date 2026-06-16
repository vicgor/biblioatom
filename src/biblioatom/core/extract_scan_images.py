"""Use case: извлечение иллюстраций со сканов и отбор фото-страниц.

Содержит две части:

* :func:`select_photo_pages` — чистая доменная логика отбора страниц с
  иллюстрациями (перенос из legacy ``cli._download_images``), типизированная на
  :class:`~biblioatom.models.PageModel`;
* :func:`extract_scan_images` — оркестрация: для каждого скана вызывается
  ``ScanExtractorProtocol`` (поиск кропов) и ``ImageProcessorProtocol``
  (постобработка/сохранение). Зависимости внедряются через Protocol-интерфейсы
  (Dependency Inversion) — use case не знает про OpenCV/Pillow.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from biblioatom.errors import ImageProcessingError, ScanExtractionError
from biblioatom.logging_config import get_logger
from biblioatom.models import ElementKind, ImageAsset, PageModel
from biblioatom.services import ImageProcessorProtocol, ScanExtractorProtocol

_logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class PhotoPage:
    """Страница-кандидат с иллюстрацией.

    ``cdn_page`` — номер, под которым JPG лежит в CDN. Он равен печатному номеру
    страницы (``print_page``), который в исходных данных всегда на единицу меньше
    физического 0-based индекса RPC; при отсутствии печатного номера берётся
    ``page - 1`` (как в legacy-реализации).
    """

    page: int
    cdn_page: int
    caption: str


@dataclass(slots=True)
class ScanExtractionResult:
    """Результат извлечения иллюстраций со сканов.

    ``failed_scans`` — пути сканов, которые не удалось обработать; обработка
    остаётся best-effort, один сбойный скан не обрывает остальные.
    """

    images: list[ImageAsset] = field(default_factory=list)
    failed_scans: list[Path] = field(default_factory=list)


def _first_caption(page: PageModel) -> str | None:
    """Вернуть текст первой подписи-иллюстрации страницы или ``None``."""

    for element in page.elements:
        if element.kind == ElementKind.CAPTION and element.text.strip():
            return element.text.strip()
    return None


def _cdn_page_for(page: PageModel) -> int:
    """Вычислить CDN-номер страницы (печатный номер или ``page - 1``)."""

    if page.print_page is not None:
        stripped = page.print_page.strip()
        if stripped.isdigit():
            return int(stripped)
    return page.page - 1


def select_photo_pages(pages: Sequence[PageModel]) -> list[PhotoPage]:
    """Отобрать страницы с иллюстрациями и вычислить их CDN-номера.

    Страница считается фото-страницей, если содержит хотя бы один блок-подпись
    (``ElementKind.CAPTION``). Возвращается по одной записи на страницу с текстом
    первой подписи — порядок сохраняется.
    """

    photo_pages: list[PhotoPage] = []
    for page in pages:
        caption = _first_caption(page)
        if caption is None:
            continue
        photo_pages.append(PhotoPage(page=page.page, cdn_page=_cdn_page_for(page), caption=caption))
    return photo_pages


def extract_scan_images(
    extractor: ScanExtractorProtocol,
    processor: ImageProcessorProtocol,
    scans: Sequence[tuple[int, Path]],
    out_dir: Path,
) -> ScanExtractionResult:
    """Извлечь и постобработать иллюстрации из набора сканов.

    :param extractor: реализация :class:`ScanExtractorProtocol`.
    :param processor: реализация :class:`ImageProcessorProtocol`.
    :param scans: пары ``(page, path)`` — номер страницы и путь к файлу скана.
    :param out_dir: каталог для сохранения обработанных кропов.
    """

    started = time.perf_counter()
    result = ScanExtractionResult()

    for page, scan_path in scans:
        try:
            crops = extractor.extract(scan_path.read_bytes(), page)
            for index, crop in enumerate(crops):
                out_path = out_dir / f"{page:04d}_{index:02d}"
                result.images.append(processor.process(crop, out_path))
        except (ScanExtractionError, ImageProcessingError, OSError) as exc:
            # Best-effort: сбой одного скана не должен ронять обработку остальных.
            result.failed_scans.append(scan_path)
            _logger.warning(
                "extract_scan_images.scan_failed",
                page=page,
                path=str(scan_path),
                error=str(exc),
            )

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    _logger.info(
        "extract_scan_images.done",
        scans=len(scans),
        images=len(result.images),
        failed=len(result.failed_scans),
        duration_ms=duration_ms,
    )
    return result


__all__ = [
    "PhotoPage",
    "ScanExtractionResult",
    "extract_scan_images",
    "select_photo_pages",
]
