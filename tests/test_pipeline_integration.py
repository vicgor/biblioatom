"""E2E-тесты полного пайплайна (``core/run_pipeline.py``) без сети.

Используются локальные фикстуры: мок-fetcher (реализует ``FetcherProtocol``) и
реальные сервисы парсинга/анализа/сборки EPUB. Проверяется, что сквозной прогон
собирает валидный EPUB (распаковывается как ZIP, содержит OPF и nav), что сканы и
AZW3-конвертация подключаются опционально, и что некорректная комбинация
зависимостей поднимает доменную ошибку.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from biblioatom.config import EpubSettings, ParsingSettings
from biblioatom.core.run_pipeline import run_pipeline
from biblioatom.errors import InputValidationError
from biblioatom.models import (
    BookMeta,
    EmbeddedContent,
    ImageAsset,
    TocEntry,
)
from biblioatom.services.epub_builder import EpubBuilder
from biblioatom.services.parser import Parser
from biblioatom.services.structure_analyzer import StructureAnalyzer

_PAGES_HTML = {
    0: "<p>Предисловие к изданию.</p>",
    1: "<p>ГЛАВА ПЕРВАЯ</p><p>Начало первой главы книги.</p>",
    2: "<p>Продолжение первой главы.</p>",
}


class _FakeFetcher:
    """Мок-fetcher без сети, реализующий ``FetcherProtocol``."""

    def __init__(self, *, image: bytes = b"") -> None:
        self._image = image
        self.image_calls: list[int] = []

    def fetch_book_meta(self, book_id: str) -> BookMeta:
        return BookMeta(title="Тестовая книга", max_page=len(_PAGES_HTML))

    def fetch_toc(self, book_id: str) -> list[TocEntry]:
        return [TocEntry(title="Глава первая", page=1, print_page="1")]

    def fetch_page(self, book_id: str, page: int) -> EmbeddedContent:
        return EmbeddedContent(valid=True, pagehtml=_PAGES_HTML.get(page, "<p>пусто</p>"))

    def fetch_image(self, book_id: str, page: int) -> bytes:
        self.image_calls.append(page)
        return self._image


def _services() -> tuple[Parser, StructureAnalyzer, EpubBuilder]:
    return (
        Parser(ParsingSettings()),
        StructureAnalyzer("strict"),
        EpubBuilder(EpubSettings()),
    )


def _assert_valid_epub(path: Path) -> None:
    assert path.exists()
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        assert "mimetype" in names
        assert any(n.endswith(".opf") for n in names)
        assert zf.read("mimetype") == b"application/epub+zip"


def test_pipeline_builds_valid_epub(tmp_path: Path) -> None:
    parser, analyzer, builder = _services()
    out = tmp_path / "book.epub"

    result = run_pipeline(
        fetcher=_FakeFetcher(),
        parser=parser,
        analyzer=analyzer,
        epub_builder=builder,
        book_id="test_book",
        out_path=out,
    )

    assert result.book_id == "test_book"
    assert result.title == "Тестовая книга"
    assert result.chapters >= 1
    assert result.azw3_path is None
    _assert_valid_epub(result.epub_path)


def test_pipeline_respects_page_range(tmp_path: Path) -> None:
    parser, analyzer, builder = _services()
    out = tmp_path / "book.epub"

    result = run_pipeline(
        fetcher=_FakeFetcher(),
        parser=parser,
        analyzer=analyzer,
        epub_builder=builder,
        book_id="test_book",
        out_path=out,
        from_page=0,
        to_page=1,
    )

    _assert_valid_epub(result.epub_path)
    assert result.failed_pages == []


def test_pipeline_invalid_page_range_raises(tmp_path: Path) -> None:
    parser, analyzer, builder = _services()
    with pytest.raises(InputValidationError):
        run_pipeline(
            fetcher=_FakeFetcher(),
            parser=parser,
            analyzer=analyzer,
            epub_builder=builder,
            book_id="test_book",
            out_path=tmp_path / "book.epub",
            from_page=5,
            to_page=2,
        )


def test_pipeline_requires_scan_services_for_images(tmp_path: Path) -> None:
    parser, analyzer, builder = _services()
    with pytest.raises(InputValidationError):
        run_pipeline(
            fetcher=_FakeFetcher(),
            parser=parser,
            analyzer=analyzer,
            epub_builder=builder,
            book_id="test_book",
            out_path=tmp_path / "book.epub",
            extract_images=True,
        )


def test_pipeline_requires_converter_for_azw3(tmp_path: Path) -> None:
    parser, analyzer, builder = _services()
    with pytest.raises(InputValidationError):
        run_pipeline(
            fetcher=_FakeFetcher(),
            parser=parser,
            analyzer=analyzer,
            epub_builder=builder,
            book_id="test_book",
            out_path=tmp_path / "book.epub",
            convert_azw3=True,
        )


class _FakeConverter:
    """Мок-конвертер, реализующий ``ConverterProtocol`` (без Calibre)."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []

    def convert(self, source: Path, target: Path) -> Path:
        self.calls.append((source, target))
        target.write_bytes(b"AZW3")
        return target


def test_pipeline_converts_to_azw3_with_fake_converter(tmp_path: Path) -> None:
    parser, analyzer, builder = _services()
    out = tmp_path / "book.epub"
    converter = _FakeConverter()

    result = run_pipeline(
        fetcher=_FakeFetcher(),
        parser=parser,
        analyzer=analyzer,
        epub_builder=builder,
        book_id="test_book",
        out_path=out,
        convert_azw3=True,
        converter=converter,
    )

    _assert_valid_epub(result.epub_path)
    assert result.azw3_path is not None
    assert result.azw3_path.read_bytes() == b"AZW3"
    assert converter.calls == [(result.epub_path, result.azw3_path)]


class _FakeScanExtractor:
    """Мок-извлекатель сканов, реализующий ``ScanExtractorProtocol``."""

    def extract(self, image: bytes, page: int) -> list[object]:
        return []


class _FakeImageProcessor:
    """Мок-постобработчик, реализующий ``ImageProcessorProtocol``."""

    def process(self, image: object, out_path: Path) -> ImageAsset:
        path = out_path.with_suffix(".jpg")
        path.write_bytes(b"\xff\xd8\xff")
        return ImageAsset(page=0, path=path)


def test_pipeline_extracts_scans_best_effort(tmp_path: Path) -> None:
    parser, analyzer, builder = _services()
    out = tmp_path / "book.epub"
    fetcher = _FakeFetcher(image=b"\x89PNG\r\n\x1a\n")

    result = run_pipeline(
        fetcher=fetcher,
        parser=parser,
        analyzer=analyzer,
        epub_builder=builder,
        book_id="test_book",
        out_path=out,
        extract_images=True,
        scan_extractor=_FakeScanExtractor(),
        image_processor=_FakeImageProcessor(),
        images_dir=tmp_path / "images",
    )

    _assert_valid_epub(result.epub_path)
    # Обложка (page=0) всегда включается; кропов 0 — пайплайн остаётся валидным.
    assert len(result.images) == 1
    assert result.images[0].page == 0
