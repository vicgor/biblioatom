"""Smoke-тесты иерархии ошибок, маппинга в коды завершения и политики ретраев."""

from __future__ import annotations

import httpx
import pytest

from biblioatom.errors import (
    BookgrabError,
    ConfigurationError,
    ConversionError,
    EpubBuildError,
    ExitCode,
    ExternalToolExecutionError,
    ExternalToolNotFoundError,
    FetchError,
    HttpTimeoutError,
    ImageProcessingError,
    InputValidationError,
    ParseError,
    ResourceNotFoundError,
    ScanExtractionError,
    StructureAnalysisError,
    exit_code_for,
)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ConfigurationError("x"), ExitCode.CONFIGURATION),
        (InputValidationError("x"), ExitCode.INPUT_VALIDATION),
        (FetchError("x"), ExitCode.FETCH),
        (HttpTimeoutError("x"), ExitCode.FETCH),
        (ResourceNotFoundError("x"), ExitCode.FETCH),
        (ParseError("x"), ExitCode.PARSE),
        (StructureAnalysisError("x"), ExitCode.STRUCTURE_ANALYSIS),
        (ImageProcessingError("x"), ExitCode.IMAGE),
        (ScanExtractionError("x"), ExitCode.IMAGE),
        (EpubBuildError("x"), ExitCode.EPUB_BUILD),
        (ConversionError("x"), ExitCode.EXTERNAL_TOOL),
        (ExternalToolNotFoundError("x"), ExitCode.EXTERNAL_TOOL),
        (ExternalToolExecutionError("x"), ExitCode.EXTERNAL_TOOL),
    ],
)
def test_exit_code_for_domain_errors(exc: BookgrabError, expected: ExitCode) -> None:
    assert exit_code_for(exc) == expected
    assert exc.exit_code == expected


def test_exit_code_for_unknown_exception() -> None:
    assert exit_code_for(ValueError("boom")) == ExitCode.CONFIGURATION


def test_subclassing_relationships() -> None:
    assert issubclass(HttpTimeoutError, FetchError)
    assert issubclass(ResourceNotFoundError, FetchError)
    assert issubclass(ExternalToolNotFoundError, ConversionError)
    assert issubclass(FetchError, BookgrabError)


def test_context_and_chaining() -> None:
    cause = ValueError("root")
    with pytest.raises(FetchError) as exc_info:
        raise FetchError("failed", context={"book_id": "kapitsa_1994"}) from cause
    err = exc_info.value
    assert err.context == {"book_id": "kapitsa_1994"}
    assert err.__cause__ is cause
    assert "kapitsa_1994" in str(err)


def test_str_without_context() -> None:
    assert str(ConfigurationError("msg")) == "msg"


def test_exit_codes_are_stable() -> None:
    assert ExitCode.OK.value == 0
    assert ExitCode.CONFIGURATION.value == 2
    assert ExitCode.INPUT_VALIDATION.value == 3
    assert ExitCode.FETCH.value == 4
    assert ExitCode.PARSE.value == 5
    assert ExitCode.STRUCTURE_ANALYSIS.value == 6
    assert ExitCode.IMAGE.value == 7
    assert ExitCode.EPUB_BUILD.value == 8
    assert ExitCode.EXTERNAL_TOOL.value == 10


class TestRetryPolicy:
    """Политика ретраев: повтор только для транзиентных сбоев.

    Дополняет интеграционные сетевые тесты (``test_fetcher.py``) проверкой самого
    предиката :meth:`Fetcher._is_retryable` на уровне ошибок: таймаут/транспорт/
    retryable-статус → повтор; валидация/конфиг/парсинг/отсутствие бинаря → нет.
    """

    def _request(self) -> httpx.Request:
        return httpx.Request("GET", "https://example.com/")

    def test_timeout_is_retryable(self) -> None:
        from biblioatom.services.fetcher import Fetcher

        assert Fetcher._is_retryable(httpx.TimeoutException("slow", request=self._request()))

    def test_transport_error_is_retryable(self) -> None:
        from biblioatom.services.fetcher import Fetcher

        assert Fetcher._is_retryable(httpx.ConnectError("down", request=self._request()))

    def test_retryable_status_wrapper_is_retryable(self) -> None:
        from biblioatom.services.fetcher import Fetcher, _RetryableStatus

        response = httpx.Response(503, request=self._request())
        assert Fetcher._is_retryable(_RetryableStatus(response))

    @pytest.mark.parametrize(
        "exc",
        [
            ResourceNotFoundError("404"),
            InputValidationError("bad range"),
            ConfigurationError("bad env"),
            ParseError("bad html"),
            ExternalToolNotFoundError("no calibre"),
            httpx.HTTPStatusError(
                "404",
                request=httpx.Request("GET", "https://example.com/"),
                response=httpx.Response(404, request=httpx.Request("GET", "https://example.com/")),
            ),
        ],
    )
    def test_non_transient_errors_are_not_retryable(self, exc: BaseException) -> None:
        from biblioatom.services.fetcher import Fetcher

        assert not Fetcher._is_retryable(exc)
