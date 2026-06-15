"""Smoke-тесты иерархии ошибок и маппинга в коды завершения."""

from __future__ import annotations

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
    assert ExitCode.OK == 0
    assert ExitCode.CONFIGURATION == 2
    assert ExitCode.INPUT_VALIDATION == 3
    assert ExitCode.FETCH == 4
    assert ExitCode.PARSE == 5
    assert ExitCode.STRUCTURE_ANALYSIS == 6
    assert ExitCode.IMAGE == 7
    assert ExitCode.EPUB_BUILD == 8
    assert ExitCode.EXTERNAL_TOOL == 10
