"""Тесты use case сборки EPUB (``core/build_epub.py``).

Builder мокируется через ``EpubBuilderProtocol`` (Dependency Inversion) —
проверяется, что use case прокидывает аргументы и возвращает результат builder.
"""

from __future__ import annotations

from pathlib import Path

from biblioatom.core.build_epub import build_epub
from biblioatom.models import BuildResult, ImageAsset, StructuredDocument
from biblioatom.services import EpubBuilderProtocol


class _FakeBuilder:
    """Мок-builder, реализующий ``EpubBuilderProtocol``."""

    def __init__(self) -> None:
        self.calls: list[tuple[StructuredDocument, Path, list[ImageAsset] | None]] = []

    def build(
        self,
        document: StructuredDocument,
        out_path: Path,
        images: list[ImageAsset] | None = None,
    ) -> BuildResult:
        self.calls.append((document, out_path, images))
        return BuildResult(book_id=document.book_id, outputs=[out_path], images=images or [])


def test_build_epub_delegates_to_builder(tmp_path: Path) -> None:
    builder = _FakeBuilder()
    # Структурная совместимость с протоколом.
    assert isinstance(builder, EpubBuilderProtocol)

    doc = StructuredDocument(title="T", book_id="b")
    out = tmp_path / "b.epub"
    images = [ImageAsset(page=1, path=tmp_path / "x.jpg")]

    result = build_epub(builder, doc, out, images=images)

    assert builder.calls == [(doc, out, images)]
    assert result.outputs == [out]
    assert result.book_id == "b"


def test_build_epub_without_images(tmp_path: Path) -> None:
    builder = _FakeBuilder()
    doc = StructuredDocument(title="T", book_id="b")
    out = tmp_path / "b.epub"

    result = build_epub(builder, doc, out)

    assert builder.calls[0][2] is None
    assert result.images == []
