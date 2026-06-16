"""Тесты use case извлечения иллюстраций (``core/extract_scan_images.py``).

Зависимости (extractor/processor) мокируются через Protocol-интерфейсы
(Dependency Inversion). Логика отбора фото-страниц проверяется на
Pydantic-моделях без I/O.
"""

from __future__ import annotations

from pathlib import Path

from biblioatom.core.extract_scan_images import (
    PhotoPage,
    extract_scan_images,
    select_photo_pages,
)
from biblioatom.errors import ScanExtractionError
from biblioatom.models import (
    BookElement,
    BoundingBox,
    ElementKind,
    EmbeddedContent,
    ExtractedImage,
    ImageAsset,
    PageModel,
)
from biblioatom.services import ImageProcessorProtocol, ScanExtractorProtocol

# --- фейковые реализации Protocol -----------------------------------------


class _FakeExtractor:
    """Extractor, возвращающий заранее заданные кропы по номеру страницы."""

    def __init__(self, per_page: dict[int, int], fail_pages: set[int] | None = None) -> None:
        self._per_page = per_page
        self._fail = fail_pages or set()
        self.calls: list[int] = []

    def extract(self, image: bytes, page: int) -> list[ExtractedImage]:
        self.calls.append(page)
        if page in self._fail:
            raise ScanExtractionError("boom", context={"page": page})
        count = self._per_page.get(page, 0)
        return [
            ExtractedImage(
                page=page,
                data=b"\x00",
                box=BoundingBox(x=0, y=0, width=10, height=10),
            )
            for _ in range(count)
        ]


class _FakeProcessor:
    """Processor, возвращающий ImageAsset с путём из переданного out_path."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def process(self, image: ExtractedImage, out_path: Path) -> ImageAsset:
        self.calls.append(out_path)
        return ImageAsset(page=image.page, path=out_path.with_suffix(".jpg"))


def _page(num: int, *, caption: str | None = None, print_page: str | None = None) -> PageModel:
    elements: list[BookElement] = []
    if caption is not None:
        elements.append(BookElement(kind=ElementKind.CAPTION, text=caption, page=num))
    return PageModel(
        page=num,
        print_page=print_page,
        content=EmbeddedContent(),
        elements=elements,
    )


# --- select_photo_pages ----------------------------------------------------


def test_select_photo_pages_picks_caption_pages() -> None:
    pages = [
        _page(0),
        _page(5, caption="Рис. 1", print_page="4"),
        _page(6),
    ]
    result = select_photo_pages(pages)

    assert result == [PhotoPage(page=5, cdn_page=4, caption="Рис. 1")]


def test_select_photo_pages_cdn_falls_back_to_page_minus_one() -> None:
    """Без печатного номера CDN-страница = page - 1 (legacy-поведение)."""

    result = select_photo_pages([_page(10, caption="Фото")])
    assert result == [PhotoPage(page=10, cdn_page=9, caption="Фото")]


def test_select_photo_pages_ignores_non_numeric_print_page() -> None:
    result = select_photo_pages([_page(10, caption="Фото", print_page="XII")])
    assert result[0].cdn_page == 9


def test_select_photo_pages_empty_caption_ignored() -> None:
    page = _page(3)
    page.elements.append(BookElement(kind=ElementKind.CAPTION, text="   ", page=3))
    assert select_photo_pages([page]) == []


def test_select_photo_pages_uses_first_caption() -> None:
    page = _page(4, caption="Первая")
    page.elements.append(BookElement(kind=ElementKind.CAPTION, text="Вторая", page=4))
    result = select_photo_pages([page])
    assert result[0].caption == "Первая"


# --- extract_scan_images ---------------------------------------------------


def test_extract_scan_images_orchestrates(tmp_path: Path) -> None:
    extractor = _FakeExtractor(per_page={1: 2, 2: 1})
    processor = _FakeProcessor()
    assert isinstance(extractor, ScanExtractorProtocol)
    assert isinstance(processor, ImageProcessorProtocol)

    scan1 = tmp_path / "p1.png"
    scan2 = tmp_path / "p2.png"
    scan1.write_bytes(b"a")
    scan2.write_bytes(b"b")

    result = extract_scan_images(extractor, processor, [(1, scan1), (2, scan2)], tmp_path / "out")

    # 2 + 1 = 3 кропа обработаны.
    assert len(result.images) == 3
    assert result.failed_scans == []
    assert extractor.calls == [1, 2]
    # Имена выходных файлов уникальны по (page, index).
    stems = sorted(p.stem for p in processor.calls)
    assert stems == ["0001_00", "0001_01", "0002_00"]


def test_extract_scan_images_best_effort_on_failure(tmp_path: Path) -> None:
    extractor = _FakeExtractor(per_page={1: 1, 2: 1}, fail_pages={1})
    processor = _FakeProcessor()

    scan1 = tmp_path / "p1.png"
    scan2 = tmp_path / "p2.png"
    scan1.write_bytes(b"a")
    scan2.write_bytes(b"b")

    result = extract_scan_images(extractor, processor, [(1, scan1), (2, scan2)], tmp_path / "out")

    # Страница 1 упала, страница 2 обработана.
    assert result.failed_scans == [scan1]
    assert len(result.images) == 1
    assert result.images[0].page == 2


def test_extract_scan_images_missing_file_is_best_effort(tmp_path: Path) -> None:
    extractor = _FakeExtractor(per_page={1: 1})
    processor = _FakeProcessor()
    missing = tmp_path / "nope.png"

    result = extract_scan_images(extractor, processor, [(1, missing)], tmp_path / "out")

    assert result.failed_scans == [missing]
    assert result.images == []
