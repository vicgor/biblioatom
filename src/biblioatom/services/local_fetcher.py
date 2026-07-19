"""Оффлайн-реализация :class:`FetcherProtocol` поверх рабочего каталога книги.

Читает сырые ответы сервера из ``workspace.raw_dir`` (заполняется командой
``download``) и парсит их тем же :class:`Parser`, что и сетевой ``Fetcher``.
Отсутствие файла поднимает :class:`ResourceNotFoundError` — ту же доменную
ошибку, что и сетевой 404, поэтому best-effort-логика ``fetch_book``
(``failed_pages``) работает без изменений.
"""

from __future__ import annotations

from pathlib import Path

from biblioatom.config import ParsingSettings
from biblioatom.errors import FetchError, ResourceNotFoundError
from biblioatom.models import BookMeta, EmbeddedContent, TocEntry
from biblioatom.services.parser import Parser
from biblioatom.services.workspace import BookWorkspace


class LocalFetcher:
    """Источник данных книги: локальный кэш вместо сети.

    :param workspace: рабочий каталог книги (:class:`BookWorkspace`).
    :param parser: парсер метаданных/TOC/страниц; по умолчанию :class:`Parser`.
    """

    def __init__(self, workspace: BookWorkspace, *, parser: Parser | None = None) -> None:
        self._ws = workspace
        self._parser = parser or Parser(ParsingSettings())

    def _read_text(self, path: Path, what: str) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ResourceNotFoundError(
                "Cached file not found; run `biblioatom download` first.",
                context={"what": what, "path": str(path)},
            ) from exc
        except OSError as exc:
            raise FetchError(
                "Failed to read cached file.",
                context={"what": what, "path": str(path)},
            ) from exc

    def fetch_book_meta(self, book_id: str) -> BookMeta:
        """Вернуть метаданные книги из кэшированного ``meta.html``."""

        return self._parser.parse_book_meta(self._read_text(self._ws.meta_path, "meta"), book_id)

    def fetch_toc(self, book_id: str) -> list[TocEntry]:
        """Вернуть оглавление из кэшированного ``toc.html``."""

        return self._parser.parse_toc(self._read_text(self._ws.toc_path, "toc"))

    def fetch_page(self, book_id: str, page: int) -> EmbeddedContent:
        """Вернуть содержимое страницы из кэшированного RPC-ответа."""

        raw = self._read_text(self._ws.page_path(page), "page")
        return self._parser.parse_embedded_content(raw)

    def fetch_image(self, book_id: str, page: int) -> bytes:
        """Вернуть байты кэшированного скана страницы."""

        path = self._ws.scan_path(page)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ResourceNotFoundError(
                "Cached scan not found; run `biblioatom download` first.",
                context={"path": str(path), "page": page},
            ) from exc
        except OSError as exc:
            raise FetchError(
                "Failed to read cached scan.",
                context={"path": str(path), "page": page},
            ) from exc


__all__ = ["LocalFetcher"]
