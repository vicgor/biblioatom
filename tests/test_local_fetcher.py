"""Тесты LocalFetcher: чтение сырья книги из рабочего каталога."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from biblioatom.errors import ResourceNotFoundError
from biblioatom.services import FetcherProtocol
from biblioatom.services.local_fetcher import LocalFetcher
from biblioatom.services.workspace import BookWorkspace

_META_HTML = (
    '<html><head><title>Книга / Просмотр</title></head><body><div data-rel="2"></div></body></html>'
)
_TOC_HTML = (
    '<aside data-type="tree-box-contents">'
    '<a data-goto-page="1" data-level="1">Глава первая'
    '<span class="info pageno">1</span></a></aside>'
)


@pytest.fixture
def ws(tmp_path: Path) -> BookWorkspace:
    workspace = BookWorkspace(work_dir=tmp_path, book_id="bid")
    workspace.ensure_dirs()
    workspace.meta_path.write_text(_META_HTML, encoding="utf-8")
    workspace.toc_path.write_text(_TOC_HTML, encoding="utf-8")
    workspace.page_path(1).write_text(
        json.dumps({"valid": True, "pagehtml": "<p>Текст страницы 1</p>"}),
        encoding="utf-8",
    )
    workspace.scan_path(0).write_bytes(b"\xff\xd8jpeg")
    return workspace


def test_satisfies_fetcher_protocol(ws: BookWorkspace) -> None:
    assert isinstance(LocalFetcher(ws), FetcherProtocol)


def test_fetch_book_meta_parses_cached_html(ws: BookWorkspace) -> None:
    meta = LocalFetcher(ws).fetch_book_meta("bid")
    assert meta.title == "Книга"
    assert meta.max_page == 2
    assert meta.page_count_is_fallback is False


def test_fetch_toc_parses_cached_html(ws: BookWorkspace) -> None:
    toc = LocalFetcher(ws).fetch_toc("bid")
    assert len(toc) == 1
    assert toc[0].title == "Глава первая"
    assert toc[0].print_page == "1"


def test_fetch_page_parses_cached_json(ws: BookWorkspace) -> None:
    content = LocalFetcher(ws).fetch_page("bid", 1)
    assert content.valid is True
    assert "Текст страницы 1" in content.pagehtml


def test_fetch_image_reads_cached_scan(ws: BookWorkspace) -> None:
    assert LocalFetcher(ws).fetch_image("bid", 0) == b"\xff\xd8jpeg"


def test_missing_page_raises_resource_not_found(ws: BookWorkspace) -> None:
    with pytest.raises(ResourceNotFoundError):
        LocalFetcher(ws).fetch_page("bid", 99)


def test_missing_scan_raises_resource_not_found(ws: BookWorkspace) -> None:
    with pytest.raises(ResourceNotFoundError):
        LocalFetcher(ws).fetch_image("bid", 99)


def test_missing_meta_raises_resource_not_found(tmp_path: Path) -> None:
    empty = BookWorkspace(work_dir=tmp_path, book_id="none")
    with pytest.raises(ResourceNotFoundError):
        LocalFetcher(empty).fetch_book_meta("none")
