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
from biblioatom.services.source_utils import book_id_from_source
from biblioatom.services.workspace import BookWorkspace

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
        assert result.exit_code == int(ExitCode.INPUT_VALIDATION)


class TestDownloadCommand:
    def test_download_invokes_use_case(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from biblioatom.core.download_book import DownloadResult

        captured: dict[str, object] = {}

        def fake_download(network, local, parser, workspace, book_id, **kwargs):  # type: ignore[no-untyped-def]
            captured["book_id"] = book_id
            captured["root"] = workspace.root
            captured["refresh"] = kwargs.get("refresh")
            return DownloadResult(
                book_id=book_id,
                title="Книга",
                max_page=2,
                pages_downloaded=3,
                scans_downloaded=1,
            )

        monkeypatch.setattr("biblioatom.core.download_book.download_book", fake_download)
        result = runner.invoke(app, ["download", "bid", "--work-dir", str(tmp_path), "--refresh"])
        assert result.exit_code == 0
        assert captured["book_id"] == "bid"
        assert captured["root"] == tmp_path / "bid"
        assert captured["refresh"] is True
        assert "3" in result.output  # счётчик страниц в выводе


class TestProgressReporter:
    def test_default_mode_returns_reporter(self) -> None:
        from biblioatom.cli import _progress_reporter
        from biblioatom.services.progress import RichProgressReporter

        assert isinstance(_progress_reporter(quiet=False, verbose=False), RichProgressReporter)

    def test_quiet_returns_none(self) -> None:
        from biblioatom.cli import _progress_reporter

        assert _progress_reporter(quiet=True, verbose=False) is None

    def test_verbose_returns_none(self) -> None:
        from biblioatom.cli import _progress_reporter

        assert _progress_reporter(quiet=False, verbose=True) is None

    def test_download_passes_reporter_by_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from biblioatom.core.download_book import DownloadResult
        from biblioatom.services.progress import RichProgressReporter

        captured: dict[str, object] = {}

        def fake_download(network, local, parser, workspace, book_id, **kwargs):  # type: ignore[no-untyped-def]
            captured["progress"] = kwargs.get("progress")
            return DownloadResult(book_id=book_id, title="Книга", max_page=1)

        monkeypatch.setattr("biblioatom.core.download_book.download_book", fake_download)
        result = runner.invoke(app, ["download", "bid", "--work-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert isinstance(captured["progress"], RichProgressReporter)

    def test_download_verbose_passes_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from biblioatom.core.download_book import DownloadResult

        captured: dict[str, object] = {}

        def fake_download(network, local, parser, workspace, book_id, **kwargs):  # type: ignore[no-untyped-def]
            captured["progress"] = kwargs.get("progress")
            return DownloadResult(book_id=book_id, title="Книга", max_page=1)

        monkeypatch.setattr("biblioatom.core.download_book.download_book", fake_download)
        result = runner.invoke(app, ["-v", "download", "bid", "--work-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert captured["progress"] is None


class TestCleanCommand:
    def _make_workspace(self, tmp_path: Path) -> BookWorkspace:
        ws = BookWorkspace(work_dir=tmp_path, book_id="bid")
        ws.ensure_dirs()
        ws.meta_path.write_text("<html/>", encoding="utf-8")
        ws.scan_path(0).write_bytes(b"\xff\xd8" * 10)
        ws.epub_path.write_bytes(b"PK")
        return ws

    def test_clean_default_removes_scans(self, tmp_path: Path) -> None:
        ws = self._make_workspace(tmp_path)
        result = runner.invoke(app, ["clean", "bid", "--work-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert not ws.scans_dir.exists()
        assert ws.meta_path.is_file()

    def test_clean_all_keeps_epub(self, tmp_path: Path) -> None:
        ws = self._make_workspace(tmp_path)
        result = runner.invoke(app, ["clean", "bid", "--work-dir", str(tmp_path), "--all"])
        assert result.exit_code == 0
        assert not ws.raw_dir.exists()
        assert ws.epub_path.is_file()

    def test_clean_raw_and_all_conflict(self, tmp_path: Path) -> None:
        self._make_workspace(tmp_path)
        result = runner.invoke(app, ["clean", "bid", "--work-dir", str(tmp_path), "--raw", "--all"])
        assert result.exit_code == 3

    def test_clean_missing_workspace_exit_3(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["clean", "nope", "--work-dir", str(tmp_path)])
        assert result.exit_code == 3


class TestPipelineWorkspace:
    def test_pipeline_passes_workspace_and_default_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from biblioatom.core.run_pipeline import PipelineResult

        captured: dict[str, object] = {}

        def fake_run_pipeline(**kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            ws = kwargs["workspace"]
            return PipelineResult(book_id="bid", title="Книга", epub_path=ws.epub_path)

        monkeypatch.setattr("biblioatom.core.run_pipeline.run_pipeline", fake_run_pipeline)
        result = runner.invoke(app, ["pipeline", "bid", "--work-dir", str(tmp_path), "--refresh"])
        assert result.exit_code == 0
        ws = captured["workspace"]
        assert ws.root == tmp_path / "bid"  # type: ignore[union-attr]
        assert captured["out_path"] is None  # default → workspace.epub_path
        assert captured["refresh"] is True
        assert captured["network_fetcher"] is not None
        assert captured["fetcher"] is not captured["network_fetcher"]


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
