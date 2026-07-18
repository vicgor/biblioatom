"""Тесты BookWorkspace: раскладка путей рабочего каталога книги."""

from __future__ import annotations

from pathlib import Path

from biblioatom.services.workspace import BookWorkspace


def _ws(tmp_path: Path) -> BookWorkspace:
    return BookWorkspace(work_dir=tmp_path / "books", book_id="kapitsa_1994")


def test_layout_paths(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    root = tmp_path / "books" / "kapitsa_1994"
    assert ws.root == root
    assert ws.raw_dir == root / "raw"
    assert ws.meta_path == root / "raw" / "meta.html"
    assert ws.toc_path == root / "raw" / "toc.html"
    assert ws.pages_dir == root / "raw" / "pages"
    assert ws.scans_dir == root / "raw" / "scans"
    assert ws.book_json_path == root / "book.json"
    assert ws.images_dir == root / "images"
    assert ws.epub_path == root / "kapitsa_1994.epub"


def test_page_and_scan_paths_are_zero_padded(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    assert ws.page_path(7) == ws.pages_dir / "p0007.json"
    assert ws.scan_path(0) == ws.scans_dir / "0000.jpg"
    assert ws.scan_path(123) == ws.scans_dir / "0123.jpg"


def test_has_raw_reflects_meta_presence(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    assert not ws.has_raw()
    ws.ensure_dirs()
    assert not ws.has_raw()  # каталоги есть, meta.html ещё нет
    ws.meta_path.write_text("<html/>", encoding="utf-8")
    assert ws.has_raw()


def test_ensure_dirs_creates_nested_layout(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    ws.ensure_dirs()
    assert ws.pages_dir.is_dir()
    assert ws.scans_dir.is_dir()
