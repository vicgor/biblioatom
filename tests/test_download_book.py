"""Тесты core use case ``download_book`` (мок-сеть, реальный Parser/LocalFetcher)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from biblioatom.config import ParsingSettings
from biblioatom.core.download_book import DownloadResult, download_book
from biblioatom.errors import FetchError, InputValidationError
from biblioatom.services.local_fetcher import LocalFetcher
from biblioatom.services.parser import Parser
from biblioatom.services.workspace import BookWorkspace

_META_HTML = (
    '<html><head><title>Книга / Просмотр</title></head><body><div data-rel="2"></div></body></html>'
)


class _FakeNetwork:
    """Мок RawFetcherProtocol: страница 1 содержит подпись-иллюстрацию."""

    def __init__(self, *, fail_pages: frozenset[int] = frozenset()) -> None:
        self.page_calls: list[int] = []
        self.image_calls: list[int] = []
        self._fail_pages = fail_pages

    def fetch_book_meta_raw(self, book_id: str) -> str:
        return _META_HTML

    def fetch_toc_raw(self, book_id: str) -> str:
        return "<html><body></body></html>"

    def fetch_page_raw(self, book_id: str, page: int) -> str:
        self.page_calls.append(page)
        if page in self._fail_pages:
            raise FetchError("boom", context={"page": page})
        html = '<p class="img">Рис. 1. Подпись</p>' if page == 1 else f"<p>Текст {page}</p>"
        return json.dumps({"valid": True, "pagehtml": html})

    def fetch_image(self, book_id: str, page: int) -> bytes:
        self.image_calls.append(page)
        return b"\xff\xd8scan"


class _SpyProgress:
    """Шпион ProgressReporterProtocol: копит события (kind, phase, total)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, int | None]] = []

    def start(self, phase: str, total: int) -> None:
        self.events.append(("start", phase, total))

    def advance(self, phase: str) -> None:
        self.events.append(("advance", phase, None))

    def finish(self, phase: str) -> None:
        self.events.append(("finish", phase, None))


def _run(
    tmp_path: Path,
    network: _FakeNetwork | None = None,
    *,
    refresh: bool = False,
    from_page: int = 0,
    to_page: int | None = None,
    progress: _SpyProgress | None = None,
) -> tuple[_FakeNetwork, BookWorkspace, DownloadResult]:
    net = network or _FakeNetwork()
    ws = BookWorkspace(work_dir=tmp_path / "books", book_id="bid")
    parser = Parser(ParsingSettings())
    local = LocalFetcher(ws, parser=parser)
    result = download_book(
        net,
        local,
        parser,
        ws,
        "bid",
        from_page=from_page,
        to_page=to_page,
        refresh=refresh,
        progress=progress,
    )
    return net, ws, result


def test_download_creates_raw_layout_and_book_json(tmp_path: Path) -> None:
    net, ws, result = _run(tmp_path)

    assert ws.meta_path.is_file()
    assert ws.toc_path.is_file()
    # max_page=2 → страницы 0..2.
    assert [ws.page_path(p).is_file() for p in range(3)] == [True, True, True]
    payload = json.loads(ws.book_json_path.read_text(encoding="utf-8"))
    assert payload["title"] == "Книга"
    assert len(payload["pages"]) == 3
    # Сканы: обложка (0) + фото-страница 1 (CAPTION, cdn=page без print_page).
    assert ws.scan_path(0).read_bytes() == b"\xff\xd8scan"
    assert ws.scan_path(1).is_file()
    assert result.pages_downloaded == 3
    assert result.scans_downloaded == 2
    assert result.failed_pages == []


def test_download_is_idempotent(tmp_path: Path) -> None:
    net, ws, _ = _run(tmp_path)
    # Повторный прогон тем же кэшем: сеть по страницам не дёргается.
    net2, _, result2 = _run(tmp_path)
    assert result2.pages_downloaded == 0
    assert result2.pages_skipped == 3
    assert result2.scans_skipped == 2
    assert net2.page_calls == []
    assert net2.image_calls == []


def test_download_refresh_redownloads(tmp_path: Path) -> None:
    _run(tmp_path)
    net2, _, result2 = _run(tmp_path, refresh=True)
    assert result2.pages_downloaded == 3
    assert net2.page_calls == [0, 1, 2]


def test_download_page_failure_is_best_effort(tmp_path: Path) -> None:
    net = _FakeNetwork(fail_pages=frozenset({2}))
    _, ws, result = _run(tmp_path, net)
    assert result.failed_pages == [2]
    assert not ws.page_path(2).exists()
    # book.json всё равно записан (best-effort).
    assert ws.book_json_path.is_file()


def test_download_invalid_range_raises(tmp_path: Path) -> None:
    with pytest.raises(InputValidationError):
        _run(tmp_path, from_page=5, to_page=1)


def test_progress_reports_pages_then_scans(tmp_path: Path) -> None:
    """Фазы идут последовательно; advance на каждой итерации обоих циклов."""

    spy = _SpyProgress()
    _, _, result = _run(tmp_path, progress=spy)

    starts = [e for e in spy.events if e[0] == "start"]
    assert starts == [("start", "pages", 3), ("start", "scans", 2)]
    assert [e for e in spy.events if e[0] == "advance" and e[1] == "pages"] == [
        ("advance", "pages", None)
    ] * 3
    assert [e for e in spy.events if e[0] == "advance" and e[1] == "scans"] == [
        ("advance", "scans", None)
    ] * 2
    finishes = [e for e in spy.events if e[0] == "finish"]
    assert finishes == [("finish", "pages", None), ("finish", "scans", None)]


def test_progress_advances_on_cached_skips(tmp_path: Path) -> None:
    """Идемпотентный повтор: advance и на пропущенных-из-кэша элементах."""

    _run(tmp_path)  # наполняем кэш
    spy = _SpyProgress()
    _, _, result = _run(tmp_path, progress=spy)

    assert result.pages_skipped == 3
    assert len([e for e in spy.events if e[0] == "advance" and e[1] == "pages"]) == 3
    assert len([e for e in spy.events if e[0] == "advance" and e[1] == "scans"]) == 2
