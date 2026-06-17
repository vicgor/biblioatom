"""Разбор HTML/JSON источника в доменные модели на selectolax.

Перенос доменной логики парсинга из legacy ``fetch.py`` с заменой ``HTMLParser``/regex на
selectolax. Сохранены доменные знания о структуре книги и оглавления.

Для нормализации идентификатора источника используйте
:func:`~biblioatom.services.source_utils.book_id_from_source` из
:mod:`biblioatom.services.source_utils`.
"""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser, Node

from biblioatom.config import ParsingSettings
from biblioatom.errors import ParseError
from biblioatom.logging_config import get_logger
from biblioatom.models import BookMeta, EmbeddedContent, PageModel, TocEntry
from biblioatom.services import structure_analyzer

_logger = get_logger(__name__)

_TITLE_SUFFIX_RE = re.compile(r"\s*/\s*Просмотр.*$", re.I)
_WS_RE = re.compile(r"\s+")

_SOFT_HYPHEN = "\u00ad"
_NBSP = "\u00a0"


def _clean_toc_text(value: str) -> str:
    s = value.replace(_SOFT_HYPHEN, "").replace(_NBSP, " ")
    return _WS_RE.sub(" ", s).strip()


class Parser:
    """Реализация :class:`~biblioatom.services.ParserProtocol` на selectolax."""

    def __init__(self, settings: ParsingSettings | None = None) -> None:
        self._settings = settings or ParsingSettings()

    def parse_book_meta(self, html: str, book_id: str) -> BookMeta:
        """Извлечь метаданные книги (:class:`BookMeta`) со страницы."""
        try:
            tree = HTMLParser(html)
        except (ValueError, TypeError) as exc:  # pragma: no cover
            raise ParseError(
                "Failed to parse book meta HTML.", context={"book_id": book_id}
            ) from exc

        title = ""
        title_node = tree.css_first("title")
        if title_node is not None:
            title = _TITLE_SUFFIX_RE.sub("", title_node.text(strip=True)).strip()
        if not title:
            title = book_id

        max_data_rel = 0
        for node in tree.css("[data-rel]"):
            rel = node.attributes.get("data-rel")
            if rel is None:
                continue
            try:
                val = int(rel)
            except ValueError:
                continue
            if val > max_data_rel:
                max_data_rel = val

        if max_data_rel > 0:
            return BookMeta(title=title, max_page=max_data_rel, page_count_is_fallback=False)

        fallback = self._settings.fallback_max_page
        _logger.warning(
            "page_count_fallback_used",
            book_id=book_id,
            fallback_max_page=fallback,
        )
        return BookMeta(title=title, max_page=fallback, page_count_is_fallback=True)

    def parse_toc(self, html: str) -> list[TocEntry]:
        """Разобрать оглавление книги в список :class:`TocEntry`."""
        tree = HTMLParser(html)
        aside = tree.css_first(self._settings.toc_selector)
        if aside is None:
            return []
        entries: list[TocEntry] = []
        for a in aside.css("a[data-goto-page][data-level]"):
            entry = self._parse_toc_link(a)
            if entry is not None:
                entries.append(entry)
        return entries

    def _parse_toc_link(self, a: Node) -> TocEntry | None:
        page_raw = a.attributes.get("data-goto-page")
        level_raw = a.attributes.get("data-level")
        if page_raw is None or level_raw is None:
            return None
        try:
            page = int(page_raw)
            level = int(level_raw)
        except ValueError:
            return None

        author = ""
        print_page: str | None = None
        for span in a.css("span.info"):
            classes = (span.attributes.get("class") or "").split()
            text = _clean_toc_text(span.text())
            if "author" in classes:
                author = text
            elif "pageno" in classes and text:
                print_page = text

        for junk in a.css("span.info, ins"):
            junk.decompose()
        title = _clean_toc_text(a.text())
        if not title:
            return None

        return TocEntry(
            title=title,
            author=author or None,
            page=page,
            print_page=print_page,
            level=level,
        )

    def parse_embedded_content(self, raw: str | dict[str, object] | None) -> EmbeddedContent:
        """Разобрать поле ``content`` страницы (делегирует structure_analyzer)."""
        return structure_analyzer.parse_embedded_content(raw)

    def page_to_model(
        self, page: int, content: EmbeddedContent, print_page: str | None = None
    ) -> PageModel:
        """Построить :class:`PageModel` из содержимого (делегирует structure_analyzer)."""
        return structure_analyzer.page_to_model(page, content, print_page)


def fetch_all_pages(
    parser: Parser,
    pages_html: list[str],
    toc_html: str | None = None,
) -> tuple[list[PageModel], list[TocEntry]]:
    """Разобрать список страниц и опциональный TOC."""
    from biblioatom.services import structure_analyzer as sa

    pages: list[PageModel] = []
    for idx, html in enumerate(pages_html):
        content = parser.parse_embedded_content(html)
        pages.append(sa.page_to_model(idx, content))

    toc: list[TocEntry] = []
    if toc_html is not None:
        toc = parser.parse_toc(toc_html)

    return pages, toc


def page_to_model(
    page: int,
    content: EmbeddedContent,
    print_page: str | None = None,
) -> PageModel:
    """Модульный псевдоним для :meth:`Parser.page_to_model` (без экземпляра)."""
    return structure_analyzer.page_to_model(page, content, print_page)


__all__ = ["Parser", "fetch_all_pages", "page_to_model"]
