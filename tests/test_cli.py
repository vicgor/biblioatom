"""Тесты тонкого CLI-слоя через ``typer.testing.CliRunner``.

Сетевые/тяжёлые core-функции мокируются — проверяется парсинг аргументов, вывод,
маппинг доменных ошибок в коды завершения и поведение глобальных опций.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import structlog
from typer.testing import CliRunner

from biblioatom import __version__
from biblioatom.cli import _book_id_from_source, app
from biblioatom.core.fetch_book import FetchedBook
from biblioatom.errors import (
    ExitCode,
    ExternalToolNotFoundError,
    FetchError,
    InputValidationError,
)
from biblioatom.models import StructuredDocument

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_structlog() -> Iterator[None]:
    """Сбросить кэш structlog между прогонами CLI.

    CliRunner подменяет ``sys.stderr`` на каждый вызов и закрывает его после;
    из-за ``cache_logger_on_first_use`` логгер мог бы остаться привязан к уже
    закрытому потоку. Сброс к дефолтной конфигурации устраняет это в тестах.
    """

    yield
    structlog.reset_defaults()


class TestGlobalOptions:
    def test_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "biblioatom" in result.output.lower()

    def test_version(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_no_args_shows_help(self) -> None:
        # no_args_is_help: Typer печатает справку и выходит кодом 2.
        result = runner.invoke(app, [])
        assert "Usage" in result.output

    @pytest.mark.parametrize(
        "command",
        ["fetch", "analyze", "extract-scans", "build", "convert", "pipeline"],
    )
    def test_subcommand_help(self, command: str) -> None:
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0
        assert "Usage" in result.output


class TestBookIdFromSource:
    def test_plain_id(self) -> None:
        assert _book_id_from_source("kapitsa_1994") == "kapitsa_1994"

    def test_url(self) -> None:
        url = "https://elib.biblioatom.ru/text/kapitsa_1994/p0/"
        assert _book_id_from_source(url) == "kapitsa_1994"

    def test_bad_source_raises(self) -> None:
        with pytest.raises(InputValidationError):
            _book_id_from_source("https://example.com/no-book-here")


def _fake_fetched_book() -> FetchedBook:
    return FetchedBook(book_id="b", title="Книга", max_page=3)


class TestFetchCommand:
    def test_fetch_writes_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_fetch_book(*_args: Any, **_kwargs: Any) -> FetchedBook:
            return _fake_fetched_book()

        monkeypatch.setattr("biblioatom.core.fetch_book.fetch_book", fake_fetch_book)
        out = tmp_path / "out.json"
        result = runner.invoke(app, ["fetch", "b", "-o", str(out)])

        assert result.exit_code == 0, result.output
        assert out.exists()
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["book_id"] == "b"

    def test_fetch_maps_fetch_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_args: Any, **_kwargs: Any) -> FetchedBook:
            raise FetchError("network down")

        monkeypatch.setattr("biblioatom.core.fetch_book.fetch_book", boom)
        result = runner.invoke(app, ["fetch", "b"])

        assert result.exit_code == int(ExitCode.FETCH)


class TestAnalyzeCommand:
    def test_analyze_json_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "biblioatom.core.fetch_book.fetch_book",
            lambda *a, **k: _fake_fetched_book(),
        )
        monkeypatch.setattr(
            "biblioatom.core.analyze_structure.analyze_structure",
            lambda *a, **k: StructuredDocument(title="Книга", book_id="b"),
        )
        result = runner.invoke(app, ["analyze", "b", "--json"])

        assert result.exit_code == 0, result.output
        assert '"book_id"' in result.output


class TestBuildCommand:
    def test_build_missing_input_is_input_validation(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["build", str(tmp_path / "nope.json")])
        assert result.exit_code == int(ExitCode.INPUT_VALIDATION)


class TestConvertCommand:
    def test_convert_missing_source(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["convert", str(tmp_path / "nope.epub")])
        assert result.exit_code == int(ExitCode.INPUT_VALIDATION)

    def test_convert_tool_not_found_maps_external_tool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        epub = tmp_path / "book.epub"
        epub.write_bytes(b"epub")

        def boom(*_args: Any, **_kwargs: Any) -> None:
            raise ExternalToolNotFoundError("no calibre")

        monkeypatch.setattr("biblioatom.core.convert_to_azw3.convert_to_azw3", boom)
        result = runner.invoke(app, ["convert", str(epub)])

        assert result.exit_code == int(ExitCode.EXTERNAL_TOOL)


class TestExtractScansCommand:
    def test_missing_dir_is_input_validation(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["extract-scans", str(tmp_path / "missing")])
        assert result.exit_code == int(ExitCode.INPUT_VALIDATION)


class TestVerboseTraceback:
    def test_verbose_propagates_traceback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_args: Any, **_kwargs: Any) -> FetchedBook:
            raise FetchError("boom")

        monkeypatch.setattr("biblioatom.core.fetch_book.fetch_book", boom)
        # В verbose-режиме исключение пробрасывается (CliRunner ловит его в .exception).
        result = runner.invoke(app, ["-v", "fetch", "b"])

        assert result.exit_code != 0
        assert isinstance(result.exception, FetchError)
