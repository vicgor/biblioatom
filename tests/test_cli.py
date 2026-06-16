"""Тесты CLI-слоя (тонкие — только Typer-обёртка, не бизнес-логика)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from biblioatom import __version__
from biblioatom.cli import app
from biblioatom.core.fetch_book import FetchedBook
from biblioatom.errors import (
    ExitCode,
    FetchError,
    InputValidationError,
)
from biblioatom.models import StructuredDocument
from biblioatom.services.source_utils import book_id_from_source

runner = CliRunner()


class TestVersion:
    def test_version_flag(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output


class TestHelp:
    @pytest.mark.parametrize(
        "command",
        ["fetch", "analyze", "extract-scans", "build", "convert", "pipeline"],
    )
    def test_subcommand_help(self, command: str) -> None:
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0


class TestBookIdFromSource:
    def test_plain_id(self) -> None:
        assert book_id_from_source("kapitsa_1994") == "kapitsa_1994"

    def test_url(self) -> None:
        url = "https://elib.biblioatom.ru/text/kapitsa_1994/p0/"
        assert book_id_from_source(url) == "kapitsa_1994"

    def test_bad_source_raises(self) -> None:
        with pytest.raises(InputValidationError):
            book_id_from_source("https://example.com/no-book-here")


def _fake_fetched_book() -> FetchedBook:
    return FetchedBook(
        title="Test Book",
        book_id="test_book",
        max_page=3,
        toc=[],
        pages=[],
        failed_pages=[],
    )


class TestFetchCommand:
    def test_fetch_writes_json(self, tmp_path: Path) -> None:
        out = tmp_path / "book.json"
        with (
            patch("biblioatom.cli.Fetcher") as mock_fetcher_cls,
            patch("biblioatom.core.fetch_book.fetch_book") as mock_fetch,
        ):
            mock_fetcher_cls.return_value = MagicMock()
            mock_fetch.return_value = _fake_fetched_book()
            result = runner.invoke(app, ["fetch", "test_book", "--output", str(out)])

        assert result.exit_code == 0
        assert out.exists()

    def test_fetch_bad_source(self) -> None:
        result = runner.invoke(app, ["fetch", "https://example.com/no-book"])
        assert result.exit_code == int(ExitCode.INPUT_VALIDATION_ERROR)


class TestErrorMapping:
    def test_fetch_error_exit_code(self) -> None:
        def boom(*_args: Any, **_kwargs: Any) -> FetchedBook:
            raise FetchError("boom")

        with (
            patch("biblioatom.cli.Fetcher"),
            patch("biblioatom.core.fetch_book.fetch_book", side_effect=boom),
        ):
            result = runner.invoke(app, ["-v", "fetch", "b"])

        assert result.exit_code != 0
        assert isinstance(result.exception, FetchError)
