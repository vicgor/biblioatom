"""Тесты use case загрузки книги (``core/fetch_book.py``).

Без сети: используется мок-fetcher, реализующий ``FetcherProtocol``, и реальный
``Parser``. Проверяется валидация диапазона страниц (InputValidationError) и
happy path.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from biblioatom.config import ParsingSettings
from biblioatom.core.fetch_book import fetch_book
from biblioatom.errors import FetchError, InputValidationError, ParseError
from biblioatom.models import BookMeta, EmbeddedContent, TocEntry
from biblioatom.services.parser import Parser


class _FakeFetcher:
    """Мок-fetcher, реализующий ``FetcherProtocol`` без сети."""

    def __init__(
        self,
        *,
        title: str = "Книга",
        max_page: int = 10,
        page_count_is_fallback: bool = False,
        toc: list[TocEntry] | None = None,
        fail_pages: set[int] | None = None,
        fail_exc: Exception | None = None,
    ) -> None:
        self._title = title
        self._max_page = max_page
        self._page_count_is_fallback = page_count_is_fallback
        self._toc = toc or []
        self._fail_pages = fail_pages or set()
        self._fail_exc = fail_exc or FetchError("boom")
        self.requested_pages: list[int] = []

    def fetch_book_meta(self, book_id: str) -> BookMeta:
        return BookMeta(
            title=self._title,
            max_page=self._max_page,
            page_count_is_fallback=self._page_count_is_fallback,
        )

    def fetch_toc(self, book_id: str) -> list[TocEntry]:
        return self._toc

    def fetch_page(self, book_id: str, page: int) -> EmbeddedContent:
        self.requested_pages.append(page)
        if page in self._fail_pages:
            raise self._fail_exc
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


class TestFallbackPageCount:
    """M3: fallback-предел не должен проходить тихо."""

    def test_fallback_logs_warning(self) -> None:
        fetcher = _FakeFetcher(max_page=545, page_count_is_fallback=True)
        with capture_logs() as events:
            fetch_book(fetcher, _parser(), "book", from_page=0, to_page=1)
        assert any(e["event"] == "fetch_book.page_count_is_fallback" for e in events)

    def test_real_page_count_no_fallback_warning(self) -> None:
        fetcher = _FakeFetcher(max_page=5, page_count_is_fallback=False)
        with capture_logs() as events:
            fetch_book(fetcher, _parser(), "book", from_page=0, to_page=1)
        assert not any(e["event"] == "fetch_book.page_count_is_fallback" for e in events)


class TestPageErrorHandling:
    """M2: best-effort только по доменным ошибкам; баги всплывают."""

    def test_domain_parse_error_is_best_effort(self) -> None:
        fetcher = _FakeFetcher(max_page=3, fail_pages={1}, fail_exc=ParseError("bad"))
        result = fetch_book(fetcher, _parser(), "book", from_page=0, to_page=2)
        assert result.failed_pages == [1]
        assert [p.page for p in result.pages] == [0, 1, 2]

    def test_programming_error_propagates(self) -> None:
        # AttributeError — программный баг, не сетевой сбой: он НЕ должен
        # проглатываться best-effort catch, а всплывать наружу.
        fetcher = _FakeFetcher(max_page=3, fail_pages={1}, fail_exc=AttributeError("bug"))
        with pytest.raises(AttributeError):
            fetch_book(fetcher, _parser(), "book", from_page=0, to_page=2)
