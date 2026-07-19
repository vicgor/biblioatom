"""Тесты core use case ``run_pipeline`` (фокус на ``_extract_images``).

Зависимости (fetcher/extractor/processor) подменяются фейками через
Protocol-интерфейсы (Dependency Inversion) — без сети и OpenCV/Pillow.
"""

from __future__ import annotations

from pathlib import Path

from biblioatom.core.fetch_book import FetchedBook
from biblioatom.core.run_pipeline import _extract_images
from biblioatom.models import EmbeddedContent, ExtractedImage, ImageAsset, PageModel
from biblioatom.services.workspace import BookWorkspace

# --- фейковые реализации Protocol -----------------------------------------


class _CoverFetcher:
    """Fetcher, отдающий одни и те же байты на любой fetch_image."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self.calls: list[int] = []

    def fetch_image(self, book_id: str, page: int) -> bytes:
        self.calls.append(page)
        return self._data


class _NoopExtractor:
    """Extractor без кропов — фокусируемся на обложке, а не на сканах."""

    def extract(self, image: bytes, page: int) -> list[ExtractedImage]:
        return []


class _SpyProcessor:
    """Processor, запоминающий обработанные кропы и проставляющий размеры."""

    def __init__(self) -> None:
        self.processed: list[ExtractedImage] = []

    def process(self, image: ExtractedImage, out_path: Path) -> ImageAsset:
        self.processed.append(image)
        return ImageAsset(
            page=image.page,
            path=out_path.with_suffix(".jpg"),
            caption=image.caption,
            width=1200,
            height=1800,
        )


def _cover_book() -> FetchedBook:
    cover = PageModel(page=0, is_cover=True, content=EmbeddedContent())
    return FetchedBook(book_id="bid", title="Книга", max_page=0, pages=[cover])


class _SpyProgress:
    """Шпион ProgressReporterProtocol: копит события (kind, phase, total)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, int | None]] = []

    def start(self, phase: str, total: int) -> None:
        self.events.append(("start", phase, total))

    def advance(self, phase: str) -> None:
        self.events.append(("advance", phase, None))

    def finish(self, phase: str) -> None:
        self.events.append(("finish", phase, None))


# --- _extract_images: обложка ----------------------------------------------


def test_cover_is_processed_through_image_processor(tmp_path: Path) -> None:
    """Обложка проходит через ImageProcessor (ресайз/нормализация), а не пишется сырой."""

    fetcher = _CoverFetcher(b"rawcover")
    processor = _SpyProcessor()
    ws = BookWorkspace(work_dir=tmp_path, book_id="bid")

    result = _extract_images(fetcher, _NoopExtractor(), processor, _cover_book(), ws)

    # Обложка передана в процессор с исходными байтами.
    assert [img.page for img in processor.processed] == [0]
    assert processor.processed[0].data == b"rawcover"
    # Итоговый ассет обложки — результат процессора (с проставленными размерами).
    assert len(result.images) == 1
    assert result.images[0].page == 0
    assert result.images[0].width == 1200
    assert result.images[0].height == 1800
    # Сырые копии обложки в images/ больше не создаются.
    assert list(ws.images_dir.glob("*_raw.jpg")) == []


def test_scans_are_read_from_workspace_not_duplicated(tmp_path: Path) -> None:
    """Сырые сканы берутся из raw/scans/ — _raw.jpg в images/ больше не пишутся."""

    ws = BookWorkspace(work_dir=tmp_path, book_id="bid")
    ws.ensure_dirs()
    ws.scan_path(5).write_bytes(b"\x89PNGscan")

    class _OneCropExtractor:
        def extract(self, image: bytes, page: int) -> list[ExtractedImage]:
            from biblioatom.models import BoundingBox

            return [
                ExtractedImage(page=page, data=image, box=BoundingBox(x=0, y=0, width=1, height=1))
            ]

    from biblioatom.models import BookElement, ElementKind

    photo = PageModel(
        page=5,
        content=EmbeddedContent(),
        elements=[BookElement(kind=ElementKind.CAPTION, text="Рис. 1", page=5)],
    )
    book = FetchedBook(book_id="bid", title="Книга", max_page=5, pages=[photo])
    processor = _SpyProcessor()

    result = _extract_images(_CoverFetcher(b""), _OneCropExtractor(), processor, book, ws)

    assert [img.page for img in result.images] == [5]
    assert processor.processed[0].data == b"\x89PNGscan"
    assert list(ws.images_dir.glob("*_raw.jpg")) == []


def test_extract_images_forwards_progress(tmp_path: Path) -> None:
    """_extract_images прокидывает progress в extract_scan_images (фаза images)."""

    ws = BookWorkspace(work_dir=tmp_path, book_id="bid")
    spy = _SpyProgress()

    _extract_images(
        _CoverFetcher(b"cover"), _NoopExtractor(), _SpyProcessor(), _cover_book(), ws, spy
    )

    # Фото-страниц нет — фаза images стартует с total=0 и закрывается.
    assert ("start", "images", 0) in spy.events
    assert ("finish", "images", None) in spy.events
