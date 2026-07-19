"""Тесты core use case ``clean_workspace`` (режимы очистки кэша книги)."""

from __future__ import annotations

from pathlib import Path

import pytest

from biblioatom.core.clean_workspace import CleanScope, clean_workspace
from biblioatom.errors import InputValidationError
from biblioatom.services.workspace import BookWorkspace


@pytest.fixture
def ws(tmp_path: Path) -> BookWorkspace:
    workspace = BookWorkspace(work_dir=tmp_path, book_id="bid")
    workspace.ensure_dirs()
    workspace.meta_path.write_text("<html/>", encoding="utf-8")
    workspace.toc_path.write_text("<html/>", encoding="utf-8")
    workspace.page_path(0).write_text("{}", encoding="utf-8")
    workspace.scan_path(0).write_bytes(b"\xff\xd8" * 100)
    workspace.book_json_path.write_text("{}", encoding="utf-8")
    workspace.images_dir.mkdir()
    (workspace.images_dir / "0001_00.jpg").write_bytes(b"\xff\xd8")
    workspace.epub_path.write_bytes(b"PK epub")
    return workspace


def test_default_scope_removes_only_scans(ws: BookWorkspace) -> None:
    result = clean_workspace(ws)
    assert not ws.scans_dir.exists()
    assert ws.meta_path.is_file()
    assert ws.page_path(0).is_file()
    assert ws.images_dir.is_dir()
    assert ws.epub_path.is_file()
    assert result.freed_bytes >= 200
    assert ws.scans_dir in result.removed


def test_raw_scope_removes_raw_dir(ws: BookWorkspace) -> None:
    clean_workspace(ws, CleanScope.RAW)
    assert not ws.raw_dir.exists()
    assert ws.book_json_path.is_file()
    assert ws.images_dir.is_dir()
    assert ws.epub_path.is_file()


def test_all_scope_keeps_only_epub(ws: BookWorkspace) -> None:
    clean_workspace(ws, CleanScope.ALL)
    assert not ws.raw_dir.exists()
    assert not ws.book_json_path.exists()
    assert not ws.images_dir.exists()
    assert ws.epub_path.is_file()


def test_missing_workspace_raises(tmp_path: Path) -> None:
    empty = BookWorkspace(work_dir=tmp_path, book_id="none")
    with pytest.raises(InputValidationError):
        clean_workspace(empty)


def test_clean_is_idempotent(ws: BookWorkspace) -> None:
    clean_workspace(ws)
    result = clean_workspace(ws)  # scans уже нет — не ошибка
    assert result.removed == []
    assert result.freed_bytes == 0
