"""Тесты сетевого слоя (``services/fetcher.py``) через httpx.MockTransport.

Без реальной сети. Проверяется: успешные ответы; ретрай на 503/timeout с
подсчётом числа попыток (sleep замокан); ОТСУТСТВИЕ ретрая на 404 с
оборачиванием в :class:`ResourceNotFoundError`; таймаут → :class:`HttpTimeoutError`;
прочие сбои → :class:`FetchError`.
"""

from __future__ import annotations

import io
import logging
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
import structlog

from biblioatom.config import AppSettings, HttpSettings
from biblioatom.errors import FetchError, HttpTimeoutError, ResourceNotFoundError
from biblioatom.services.fetcher import Fetcher


def _configure_structlog_level(level: int) -> None:
    """Настроить structlog на конкретный filtering-уровень (вывод в StringIO)."""

    structlog.configure(
        processors=[structlog.dev.ConsoleRenderer()],
        logger_factory=structlog.PrintLoggerFactory(file=io.StringIO()),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=False,
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Убрать реальные паузы backoff: tenacity спит через time.sleep."""

    monkeypatch.setattr("time.sleep", lambda _seconds: None)


def _make_fetcher(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_retries: int = 2,
) -> Fetcher:
    """Собрать Fetcher с мок-транспортом и быстрым backoff."""

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://example.test")
    http = HttpSettings(max_retries=max_retries, backoff_factor=0.0, backoff_max=0.0)
    return Fetcher(client=client, app=AppSettings(base_url="https://example.test"), http=http)


def _counting(
    responses: list[httpx.Response],
) -> tuple[Callable[[httpx.Request], httpx.Response], list[int]]:
    """Handler, отдающий ответы по очереди (последний повторяется); счётчик вызовов."""

    calls = [0]
    it: Iterator[httpx.Response] = iter(responses)
    last = responses[-1]

    def handler(_request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return next(it, last)

    return handler, calls


class TestFetchPageSuccess:
    def test_valid_json(self) -> None:
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"valid": True, "pagetext": "Текст", "pagehtml": ""})

        with _make_fetcher(handler) as fetcher:
            content = fetcher.fetch_page("book", 0)
        assert content.valid is True
        assert content.pagetext == "Текст"

    def test_invalid_json_returns_invalid_content(self) -> None:
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not json")

        with _make_fetcher(handler) as fetcher:
            content = fetcher.fetch_page("book", 1)
        assert content.valid is False
        assert content.pagetext == "not json"


class TestRetryPolicy:
    def test_retries_on_503_then_succeeds(self) -> None:
        handler, calls = _counting(
            [
                httpx.Response(503, text="busy"),
                httpx.Response(503, text="busy"),
                httpx.Response(200, json={"valid": True, "pagetext": "OK", "pagehtml": ""}),
            ]
        )
        with _make_fetcher(handler, max_retries=2) as fetcher:
            content = fetcher.fetch_page("book", 2)
        assert content.pagetext == "OK"
        assert calls[0] == 3  # 2 неудачи + 1 успех

    def test_503_exhausts_retries_raises_fetch_error(self) -> None:
        handler, calls = _counting([httpx.Response(503, text="busy")])
        with (
            _make_fetcher(handler, max_retries=2) as fetcher,
            pytest.raises(FetchError) as exc_info,
        ):
            fetcher.fetch_page("book", 3)
        # max_retries=2 → всего 3 попытки.
        assert calls[0] == 3
        assert exc_info.value.context.get("status_code") == 503

    def test_timeout_retries_then_raises_http_timeout(self) -> None:
        calls = [0]

        def handler(_req: httpx.Request) -> httpx.Response:
            calls[0] += 1
            raise httpx.ConnectTimeout("timed out")

        with (
            _make_fetcher(handler, max_retries=1) as fetcher,
            pytest.raises(HttpTimeoutError),
        ):
            fetcher.fetch_page("book", 4)
        assert calls[0] == 2  # max_retries=1 → 2 попытки

    def test_transport_error_raises_fetch_error(self) -> None:
        def handler(_req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with (
            _make_fetcher(handler, max_retries=0) as fetcher,
            pytest.raises(FetchError),
        ):
            fetcher.fetch_page("book", 5)


class TestNoRetryOn404:
    def test_404_not_retried_and_mapped(self) -> None:
        handler, calls = _counting([httpx.Response(404, text="missing")])
        with (
            _make_fetcher(handler, max_retries=3) as fetcher,
            pytest.raises(ResourceNotFoundError),
        ):
            fetcher.fetch_page("book", 6)
        # 404 не транзиентен — ровно одна попытка, без ретраев.
        assert calls[0] == 1

    def test_400_not_retried_raises_fetch_error(self) -> None:
        handler, calls = _counting([httpx.Response(400, text="bad")])
        with (
            _make_fetcher(handler, max_retries=3) as fetcher,
            pytest.raises(FetchError),
        ):
            fetcher.fetch_page("book", 7)
        assert calls[0] == 1


class TestFetchMetaTocImage:
    def test_fetch_book_meta(self) -> None:
        html = (
            "<html><head><title>Книга / Просмотр</title></head>"
            '<body><div data-rel="50"></div></body></html>'
        )

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html)

        with _make_fetcher(handler) as fetcher:
            meta = fetcher.fetch_book_meta("book")
        assert meta.title == "Книга"
        assert meta.max_page == 50
        assert meta.page_count_is_fallback is False

    def test_fetch_toc(self) -> None:
        html = (
            '<html><body><aside data-type="tree-box-contents">'
            '<a data-goto-page="0" data-level="0"><span>Обложка</span></a>'
            "</aside></body></html>"
        )

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html)

        with _make_fetcher(handler) as fetcher:
            toc = fetcher.fetch_toc("book")
        assert len(toc) == 1
        assert toc[0].title == "Обложка"

    def test_fetch_image_returns_bytes(self) -> None:
        jpeg = b"\xff\xd8\xff\xe0fake"

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=jpeg)

        with _make_fetcher(handler) as fetcher:
            data = fetcher.fetch_image("book", 9)
        assert data == jpeg

    def test_fetch_image_404_raises(self) -> None:
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        with (
            _make_fetcher(handler) as fetcher,
            pytest.raises(ResourceNotFoundError),
        ):
            fetcher.fetch_image("book", 9)


class TestDumpHtmlIfDebug:
    """Отладочный дамп управляется уровнем structlog, а не stdlib logging."""

    @staticmethod
    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    def test_dumps_when_structlog_at_debug(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """structlog=DEBUG → дамп создаётся, даже если stdlib-логгер выше DEBUG."""

        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        _configure_structlog_level(logging.DEBUG)
        # Расхождение: stdlib-логгер модуля выше DEBUG — старый гейт дал бы False.
        stdlib_logger = logging.getLogger("biblioatom.services.fetcher")
        original = stdlib_logger.level
        stdlib_logger.setLevel(logging.WARNING)
        try:
            response = httpx.Response(
                200, text="<html>тело</html>", headers={"content-type": "text/html"}
            )
            with _make_fetcher(self._handler) as fetcher:
                fetcher._dump_html_if_debug("https://example.test/book/1", response)
        finally:
            stdlib_logger.setLevel(original)

        dumps = list(tmp_path.glob("biblioatom_*.html"))
        assert len(dumps) == 1
        assert "тело" in dumps[0].read_text(encoding="utf-8")

    def test_skipped_when_structlog_below_debug(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """structlog=INFO → дамп не создаётся."""

        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        _configure_structlog_level(logging.INFO)
        response = httpx.Response(200, text="x", headers={"content-type": "text/html"})
        with _make_fetcher(self._handler) as fetcher:
            fetcher._dump_html_if_debug("https://example.test/book/2", response)

        assert list(tmp_path.glob("biblioatom_*")) == []
