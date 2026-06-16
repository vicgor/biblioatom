"""Тесты use case загрузки книги (``core/fetch_book.py``).

Без сети: используется мок-fetcher, реализующий ``FetcherProtocol``, и реальный
``Parser``. Проверяется валидация диапазона страниц (InputValidationError) и
happy path.
"""

from __future__ import annotations

import pytest

from biblioatom.config import ParsingSettings
from biblioatom.core.fetch_book import fetch_book
from biblioatom.errors import FetchError, InputValidationError
from biblioatom.models import EmbeddedContent, TocEntry
from biblioatom.services.parser import Parser


class _FakeFetcher:
    """Мок-fetcher, реализующий ``FetcherProtocol`` без сети."""

    def __init__(
        self,
        *,
        title: str = "Книга",
        max_page: int = 10,
        toc: list[TocEntry] | None = None,
        fail_pages: set[int] | None = None,
    ) -> None:
        self._title = title
        self._max_page = max_page
        self._toc = toc or []
        self._fail_pages = fail_pages or set()
        self.requested_pages: list[int] = []

    def fetch_book_meta(self, book_id: str) -> tuple[str, int]:
        return self._title, self._max_page

    def fetch_toc(self, book_id: str) -> list[TocEntry]:
        return self._toc

    def fetch_page(self, book_id: str, page: int) -> EmbeddedContent:
        self.requested_pages.append(page)
        if page in self._fail_pages:
            raise FetchError("boom", context={"page": page})
        return EmbeddedContent(valid=True, pagetext=f"стр {page}", pagehtml="")

    def fetch_image(self, book_id: str, page: int) -> bytes:
        return b""


def _parser() -> Parser:
    return Parser(ParsingSettings())


class TestPageRangeValidation:
    def test_negative_from_page(self) -> None:
        with pytest.raises(InputValidationError):
            fetch_book(_FakeFetcher(), _parser(), "book", from_page=-1, to_page=3)

    def test_to_page_less_than_from_page(self) -> None:
        with pytest.raises(InputValidationError):
            fetch_book(_FakeFetcher(), _parser(), "book", from_page=5, to_page=2)

    def test_to_page_beyond_max(self) -> None:
        with pytest.raises(InputValidationError):
            fetch_book(_FakeFetcher(max_page=10), _parser(), "book", from_page=0, to_page=20)

    def test_valid_range_passes(self) -> None:
        result = fetch_book(_FakeFetcher(max_page=10), _parser(), "book", from_page=0, to_page=2)
        assert len(result.pages) == 3


class TestHappyPath:
    def test_fetches_metadata_and_pages(self) -> None:
        fetcher = _FakeFetcher(title="Капица", max_page=4)
        result = fetch_book(fetcher, _parser(), "kapitsa", from_page=0, to_page=2)
        assert result.title == "Капица"
        assert result.max_page == 4
        assert [p.page for p in result.pages] == [0, 1, 2]
        assert result.failed_pages == []

    def test_to_page_defaults_to_max_page(self) -> None:
        fetcher = _FakeFetcher(max_page=3)
        result = fetch_book(fetcher, _parser(), "book", from_page=0)
        assert [p.page for p in result.pages] == [0, 1, 2, 3]

    def test_print_page_from_toc_applied(self) -> None:
        toc = [TocEntry(title="Глава", page=1, print_page="42", level=0)]
        fetcher = _FakeFetcher(max_page=3, toc=toc)
        result = fetch_book(fetcher, _parser(), "book", from_page=0, to_page=2)
        page1 = next(p for p in result.pages if p.page == 1)
        assert page1.print_page == "42"

    def test_failed_page_recorded_best_effort(self) -> None:
        fetcher = _FakeFetcher(max_page=3, fail_pages={1})
        result = fetch_book(fetcher, _parser(), "book", from_page=0, to_page=2)
        # Сбой одной страницы не обрывает загрузку остальных.
        assert result.failed_pages == [1]
        assert [p.page for p in result.pages] == [0, 1, 2]
