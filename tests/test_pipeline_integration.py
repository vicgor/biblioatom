"""E2E-тесты полного пайплайна (``core/run_pipeline.py``) без сети.

Сетевой слой подменяется ``_FakeNetworkFetcher`` (RawFetcherProtocol): первый
прогон авто-скачивает сырьё в workspace, сборка идёт оффлайн через
``LocalFetcher`` и реальные сервисы парсинга/анализа/EPUB.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from biblioatom.config import EpubSettings, ParsingSettings
from biblioatom.core.run_pipeline import run_pipeline
from biblioatom.errors import InputValidationError
from biblioatom.models import ExtractedImage, ImageAsset
from biblioatom.services.epub_builder import EpubBuilder
from biblioatom.services.local_fetcher import LocalFetcher
from biblioatom.services.parser import Parser
from biblioatom.services.structure_analyzer import StructureAnalyzer
from biblioatom.services.workspace import BookWorkspace

_PAGES_HTML = {
    0: "<p>Предисловие к изданию.</p>",
    1: "<p>ГЛАВА ПЕРВАЯ</p><p>Начало первой главы книги.</p>",
    2: "<p>Продолжение первой главы.</p>",
}


class _FakeNetworkFetcher:
    """Мок сетевого слоя, реализующий ``RawFetcherProtocol``."""

    def __init__(self, *, image: bytes = b"") -> None:
        self._image = image
        self.image_calls: list[int] = []

    def fetch_book_meta_raw(self, book_id: str) -> str:
        return (
            "<html><head><title>Тестовая книга / Просмотр</title></head>"
            f'<body><div data-rel="{len(_PAGES_HTML)}"></div></body></html>'
        )

    def fetch_toc_raw(self, book_id: str) -> str:
        return (
            '<aside data-type="tree-box-contents">'
            '<a data-goto-page="1" data-level="1">Глава первая'
            '<span class="info pageno">1</span></a></aside>'
        )

    def fetch_page_raw(self, book_id: str, page: int) -> str:
        html = _PAGES_HTML.get(page, "<p>пусто</p>")
        return json.dumps({"valid": True, "pagehtml": html})

    def fetch_image(self, book_id: str, page: int) -> bytes:
        self.image_calls.append(page)
        return self._image


class _SpyProgress:
    """Шпион ProgressReporterProtocol: копит события (kind, phase, total)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, int | None]] = []

    def start(self, phase: str, total: int) -> None:
        self.events.append(("start", phase, total))

    def advance(self, phase: str, *, skipped: bool = False) -> None:
        self.events.append(("advance", phase, None))

    def finish(self, phase: str) -> None:
        self.events.append(("finish", phase, None))


def _services() -> tuple[Parser, StructureAnalyzer, EpubBuilder]:
    return (
        Parser(ParsingSettings()),
        StructureAnalyzer("strict"),
        EpubBuilder(EpubSettings()),
    )


def _workspace(tmp_path: Path) -> BookWorkspace:
    return BookWorkspace(work_dir=tmp_path / "books", book_id="test_book")


def _assert_valid_epub(path: Path) -> None:
    assert path.exists()
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        assert "mimetype" in names
        assert any(n.endswith(".opf") for n in names)
        assert zf.read("mimetype") == b"application/epub+zip"


def test_pipeline_downloads_then_builds_valid_epub(tmp_path: Path) -> None:
    parser, analyzer, builder = _services()
    ws = _workspace(tmp_path)

    result = run_pipeline(
        fetcher=LocalFetcher(ws, parser=parser),
        network_fetcher=_FakeNetworkFetcher(),
        parser=parser,
        analyzer=analyzer,
        epub_builder=builder,
        workspace=ws,
        book_id="test_book",
    )

    assert result.book_id == "test_book"
    assert result.title == "Тестовая книга"
    assert result.chapters >= 1
    assert result.azw3_path is None
    # EPUB по умолчанию — в каталоге книги; сырьё закэшировано.
    assert result.epub_path == ws.epub_path
    assert ws.has_raw()
    _assert_valid_epub(result.epub_path)


def test_pipeline_rebuilds_offline_from_cache(tmp_path: Path) -> None:
    """Повторный прогон без network_fetcher собирает EPUB из кэша."""

    parser, analyzer, builder = _services()
    ws = _workspace(tmp_path)
    run_pipeline(
        fetcher=LocalFetcher(ws, parser=parser),
        network_fetcher=_FakeNetworkFetcher(),
        parser=parser,
        analyzer=analyzer,
        epub_builder=builder,
        workspace=ws,
        book_id="test_book",
    )

    result = run_pipeline(
        fetcher=LocalFetcher(ws, parser=parser),
        network_fetcher=None,
        parser=parser,
        analyzer=analyzer,
        epub_builder=builder,
        workspace=ws,
        book_id="test_book",
    )
    _assert_valid_epub(result.epub_path)


def test_pipeline_without_cache_and_network_raises(tmp_path: Path) -> None:
    parser, analyzer, builder = _services()
    with pytest.raises(InputValidationError):
        run_pipeline(
            fetcher=LocalFetcher(_workspace(tmp_path), parser=parser),
            network_fetcher=None,
            parser=parser,
            analyzer=analyzer,
            epub_builder=builder,
            workspace=_workspace(tmp_path),
            book_id="test_book",
        )


def test_pipeline_custom_out_path(tmp_path: Path) -> None:
    parser, analyzer, builder = _services()
    ws = _workspace(tmp_path)
    out = tmp_path / "custom" / "book.epub"

    result = run_pipeline(
        fetcher=LocalFetcher(ws, parser=parser),
        network_fetcher=_FakeNetworkFetcher(),
        parser=parser,
        analyzer=analyzer,
        epub_builder=builder,
        workspace=ws,
        book_id="test_book",
        out_path=out,
    )
    assert result.epub_path == out
    _assert_valid_epub(out)


def test_pipeline_invalid_page_range_raises(tmp_path: Path) -> None:
    parser, analyzer, builder = _services()
    ws = _workspace(tmp_path)
    with pytest.raises(InputValidationError):
        run_pipeline(
            fetcher=LocalFetcher(ws, parser=parser),
            network_fetcher=_FakeNetworkFetcher(),
            parser=parser,
            analyzer=analyzer,
            epub_builder=builder,
            workspace=ws,
            book_id="test_book",
            from_page=5,
            to_page=2,
        )


def test_pipeline_requires_scan_services_for_images(tmp_path: Path) -> None:
    parser, analyzer, builder = _services()
    ws = _workspace(tmp_path)
    with pytest.raises(InputValidationError):
        run_pipeline(
            fetcher=LocalFetcher(ws, parser=parser),
            parser=parser,
            analyzer=analyzer,
            epub_builder=builder,
            workspace=ws,
            book_id="test_book",
            extract_images=True,
        )


def test_pipeline_requires_converter_for_azw3(tmp_path: Path) -> None:
    parser, analyzer, builder = _services()
    ws = _workspace(tmp_path)
    with pytest.raises(InputValidationError):
        run_pipeline(
            fetcher=LocalFetcher(ws, parser=parser),
            parser=parser,
            analyzer=analyzer,
            epub_builder=builder,
            workspace=ws,
            book_id="test_book",
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
    ws = _workspace(tmp_path)
    converter = _FakeConverter()

    result = run_pipeline(
        fetcher=LocalFetcher(ws, parser=parser),
        network_fetcher=_FakeNetworkFetcher(),
        parser=parser,
        analyzer=analyzer,
        epub_builder=builder,
        workspace=ws,
        book_id="test_book",
        convert_azw3=True,
        converter=converter,
    )

    _assert_valid_epub(result.epub_path)
    assert result.azw3_path is not None
    assert result.azw3_path.read_bytes() == b"AZW3"
    assert converter.calls == [(result.epub_path, result.azw3_path)]


class _FakeScanExtractor:
    """Мок-извлекатель сканов, реализующий ``ScanExtractorProtocol``."""

    def extract(self, image: bytes, page: int) -> list[ExtractedImage]:
        return []


class _FakeImageProcessor:
    """Мок-постобработчик, реализующий ``ImageProcessorProtocol``."""

    def process(self, image: ExtractedImage, out_path: Path) -> ImageAsset:
        path = out_path.with_suffix(".jpg")
        path.write_bytes(b"\xff\xd8\xff")
        return ImageAsset(page=0, path=path)


def test_pipeline_extracts_scans_best_effort(tmp_path: Path) -> None:
    parser, analyzer, builder = _services()
    ws = _workspace(tmp_path)
    network = _FakeNetworkFetcher(image=b"\x89PNG\r\n\x1a\n")

    result = run_pipeline(
        fetcher=LocalFetcher(ws, parser=parser),
        network_fetcher=network,
        parser=parser,
        analyzer=analyzer,
        epub_builder=builder,
        workspace=ws,
        book_id="test_book",
        extract_images=True,
        scan_extractor=_FakeScanExtractor(),
        image_processor=_FakeImageProcessor(),
    )

    _assert_valid_epub(result.epub_path)
    # Обложка (page=0) скачана download'ом и включена; кропов 0 — валидно.
    assert len(result.images) == 1
    assert result.images[0].page == 0
    # Кэш сканов — в raw/scans, дублей *_raw.jpg в images/ нет.
    assert ws.scan_path(0).is_file()
    assert list(ws.images_dir.glob("*_raw.jpg")) == []


def test_pipeline_forwards_progress_to_download_only(tmp_path: Path) -> None:
    """progress получает фазы download'а (pages, scans), но не оффлайн-перепарса."""

    parser, analyzer, builder = _services()
    ws = _workspace(tmp_path)
    spy = _SpyProgress()

    run_pipeline(
        fetcher=LocalFetcher(ws, parser=parser),
        network_fetcher=_FakeNetworkFetcher(),
        parser=parser,
        analyzer=analyzer,
        epub_builder=builder,
        workspace=ws,
        book_id="test_book",
        progress=spy,
    )

    pages_starts = [e for e in spy.events if e[0] == "start" and e[1] == "pages"]
    # Ровно один старт фазы pages — от download_book; оффлайн-fetch_book молчит.
    assert len(pages_starts) == 1
