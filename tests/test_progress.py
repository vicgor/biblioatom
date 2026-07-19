"""Тесты RichProgressReporter: жизненный цикл фаз и устойчивость к misuse."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from biblioatom.services import ProgressReporterProtocol
from biblioatom.services.progress import RichProgressReporter


def _reporter() -> tuple[RichProgressReporter, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    return RichProgressReporter(console=console), buf


def test_satisfies_protocol() -> None:
    reporter, _ = _reporter()
    assert isinstance(reporter, ProgressReporterProtocol)


def test_full_phase_lifecycle_renders_label(tmp_path: object) -> None:
    reporter, buf = _reporter()
    with reporter:
        reporter.start("pages", 3)
        reporter._progress.refresh()  # детерминированный рендер кадра в тесте
        reporter.advance("pages")
        reporter.advance("pages")
        reporter.finish("pages")
    assert "Страницы" in buf.getvalue()


def test_unknown_phase_key_is_shown_as_is() -> None:
    reporter, buf = _reporter()
    with reporter:
        reporter.start("weird", 1)
        reporter._progress.refresh()
    assert "weird" in buf.getvalue()


def test_advance_and_finish_on_unstarted_phase_are_noop() -> None:
    reporter, _ = _reporter()
    with reporter:
        reporter.advance("pages")  # не должно бросить
        reporter.finish("pages")  # не должно бросить


def test_start_twice_replaces_task() -> None:
    reporter, _ = _reporter()
    with reporter:
        reporter.start("pages", 3)
        reporter.start("pages", 5)  # замена, не исключение
        reporter.advance("pages")
        reporter.finish("pages")


def test_exit_stops_rendering_after_exception() -> None:
    reporter, _ = _reporter()
    try:
        with reporter:
            reporter.start("pages", 3)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    # После __exit__ рендер остановлен; повторный with — валиден.
    with reporter:
        reporter.start("pages", 1)
        reporter.finish("pages")
