"""Smoke-тесты создания доменных моделей."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from biblioatom.models import (
    BookElement,
    BuildResult,
    ElementKind,
    EmbeddedContent,
    ImageAsset,
    PageModel,
    StructuredChapter,
    StructuredDocument,
    TocEntry,
)


def test_book_element() -> None:
    el = BookElement(kind=ElementKind.FOOTNOTE, text="сноска", page=3, anchor="fn1", ref="ref1")
    assert el.kind is ElementKind.FOOTNOTE
    assert el.page == 3


def test_element_kind_values() -> None:
    assert ElementKind.LIST == "list_"
    assert ElementKind.HEADING == "heading"


def test_page_and_chapter_and_document() -> None:
    content = EmbeddedContent(valid=True, pagetext="текст", pagehtml="<p>текст</p>")
    page = PageModel(page=0, content=content)
    chapter = StructuredChapter(title="Глава 1", pages=[page])
    doc = StructuredDocument(
        title="Книга",
        book_id="kapitsa_1994",
        toc=[TocEntry(title="Глава 1", page=0, level=0)],
        chapters=[chapter],
    )
    assert doc.chapters[0].pages[0].content.pagetext == "текст"
    assert doc.toc[0].title == "Глава 1"


def test_image_asset_and_build_result() -> None:
    asset = ImageAsset(page=5, path=Path("images/0005_foto.jpg"), caption="Рис. 1")
    result = BuildResult(book_id="kapitsa_1994", outputs=[Path("out.epub")], images=[asset])
    assert result.images[0].caption == "Рис. 1"
    assert result.outputs == [Path("out.epub")]


def test_negative_page_rejected() -> None:
    with pytest.raises(ValidationError):
        BookElement(kind=ElementKind.NOTE, text="x", page=-1)


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        TocEntry(title="x", page=0, unknown="boom")  # type: ignore[call-arg]
