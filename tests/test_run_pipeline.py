"""Тесты core use case ``run_pipeline`` (фокус на ``_extract_images``).

Зависимости (fetcher/extractor/processor) подменяются фейками через
Protocol-интерфейсы (Dependency Inversion) — без сети и OpenCV/Pillow.
"""

from __future__ import annotations

from pathlib import Path

from biblioatom.core.fetch_book import FetchedBook
from biblioatom.core.run_pipeline import _extract_images
from biblioatom.models import EmbeddedContent, ExtractedImage, ImageAsset, PageModel

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


# --- _extract_images: обложка ----------------------------------------------


def test_cover_is_processed_through_image_processor(tmp_path: Path) -> None:
    """Обложка проходит через ImageProcessor (ресайз/нормализация), а не пишется сырой."""

    fetcher = _CoverFetcher(b"rawcover")
    processor = _SpyProcessor()

    result = _extract_images(
        fetcher, _NoopExtractor(), processor, _cover_book(), tmp_path / "images"
    )

    # Обложка передана в процессор с исходными байтами.
    assert [img.page for img in processor.processed] == [0]
    assert processor.processed[0].data == b"rawcover"
    # Итоговый ассет обложки — результат процессора (с проставленными размерами).
    assert len(result.images) == 1
    assert result.images[0].page == 0
    assert result.images[0].width == 1200
    assert result.images[0].height == 1800
