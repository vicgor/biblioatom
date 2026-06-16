"""Разбор HTML/JSON источника в доменные модели на selectolax.

Перенос доменной логики парсинга из legacy ``fetch.py`` (``_BookPageParser``,
``_clean_toc_text``, ``_parse_toc_html``) с заменой ``HTMLParser``/regex на
selectolax. Сохранены доменные знания о структуре книги и оглавления:

* метаданные страницы книги: заголовок (из ``<title>``) и максимальный номер
  страницы (из атрибутов ``data-rel``);
* структура TOC (``<aside data-type="tree-box-contents">``): мягкие переносы
  (soft hyphen), неразрывные пробелы, автор, печатный номер страницы, уровень
  вложенности.

Разделение ответственности: ``parser`` извлекает структуру из HTML/JSON, а
построение глав остаётся в ``structure_analyzer``. ``parse_embedded_content`` и
``page_to_model`` уже реализованы в ``structure_analyzer`` — здесь они
переиспользуются (делегируются), а не дублируются.
"""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser, Node

from biblioatom.config import ParsingSettings
from biblioatom.errors import ParseError
from biblioatom.logging_config import get_logger
from biblioatom.models import BookMeta, EmbeddedContent, PageModel, TocEntry
from biblioatom.services import structure_analyzer


def book_id_from_source(source: str) -> str:
    """Извлечь идентификатор книги из URL или вернуть строку как есть.

    Поддерживаются две формы::

        kapitsa_1994
        https://elib.biblioatom.ru/text/kapitsa_1994/

    Принадлежит сервисному слою, а не CLI: при добавлении нового источника
    данных (другой сайт, другая схема URL) логика меняется здесь, а CLI
    остаётся неизменным.

    :raises InputValidationError: если из строки не удалось извлечь идентификатор.
    """
    from biblioatom.errors import InputValidationError

    cleaned = source.strip().rstrip("/")
    if "/text/" in cleaned:
        tail = cleaned.split("/text/", 1)[1]
        candidate = tail.split("/", 1)[0]
        if candidate:
            return candidate
    if "/" in cleaned or not cleaned:
        raise InputValidationError(
            "Could not derive a book id from the given source.",
            context={"source": source},
        )
    return cleaned


_logger = get_logger(__name__)

# Заголовок страницы книги имеет вид "<Название> / Просмотр…"; хвост отрезаем.
_TITLE_SUFFIX_RE = re.compile(r"\s*/\s*Просмотр.*$", re.I)
_WS_RE = re.compile(r"\s+")

#: Символы, удаляемые/нормализуемые в тексте TOC.
_SOFT_HYPHEN = "­"
_NBSP = " "


def _clean_toc_text(value: str) -> str:
    """Нормализовать текст записи TOC.

    Убирает мягкие переносы (soft hyphen), заменяет неразрывные пробелы на
    обычные и схлопывает пробельные последовательности. selectolax уже отдаёт
    раскодированные HTML-сущности через ``.text()``, поэтому отдельный
    ``html.unescape`` не нужен.
    """

    s = value.replace(_SOFT_HYPHEN, "").replace(_NBSP, " ")
    return _WS_RE.sub(" ", s).strip()


class Parser:
    """Реализация :class:`~biblioatom.services.ParserProtocol` на selectolax.

    :param settings: настройки парсинга (CSS-селекторы, ``fallback_max_page``).
        ``FALLBACK_MAX_PAGE`` берётся из config, а не хардкодится.
    """

    def __init__(self, settings: ParsingSettings | None = None) -> None:
        self._settings = settings or ParsingSettings()

    # -- метаданные книги ---------------------------------------------------

    def parse_book_meta(self, html: str, book_id: str) -> BookMeta:
        """Извлечь метаданные книги (:class:`BookMeta`) со страницы.

        ``title`` берётся из ``<title>`` (с отрезанным служебным хвостом),
        ``max_page`` — максимум среди атрибутов ``data-rel``. При отсутствии
        данных используются безопасные значения: ``book_id`` и
        ``fallback_max_page`` из config; в этом случае
        ``page_count_is_fallback=True`` и пишется WARNING — чтобы «выдуманный»
        предел не был тихим (вышестоящий код может предупредить о неполноте).
        """

        try:
            tree = HTMLParser(html)
        except (ValueError, TypeError) as exc:  # pragma: no cover - selectolax надёжен
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

    # -- оглавление ---------------------------------------------------------

    def parse_toc(self, html: str) -> list[TocEntry]:
        """Разобрать оглавление книги в список :class:`TocEntry`.

        Парсит ``<aside data-type="tree-box-contents">``. Каждая ссылка с
        атрибутами ``data-goto-page`` и ``data-level`` даёт запись:

        * ``page`` — физический 0-based индекс страницы (``data-goto-page``);
        * ``level`` — глубина вложенности (``data-level``);
        * ``author`` — из ``span.info.author`` (опционально);
        * ``print_page`` — печатный номер из ``span.info.pageno`` (как строка);
        * ``title`` — текст ссылки за вычетом служебных span и блока ``<ins>``.

        Пустой/отсутствующий TOC даёт пустой список (не ошибку).
        """

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
        """Построить :class:`TocEntry` из одной ссылки TOC или вернуть ``None``."""

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

        # Заголовок: текст ссылки за вычетом служебных span (author/pageno) и
        # блока <ins>. Удаляем эти узлы из копии поддерева перед извлечением
        # текста — это устойчивее строковых замен legacy-версии.
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

    # -- содержимое страницы (делегирование в structure_analyzer) ----------

    def parse_embedded_content(self, raw: str | dict[str, object] | None) -> EmbeddedContent:
        """Разобрать поле ``content`` страницы (делегирует structure_analyzer)."""

        return structure_analyzer.parse_embedded_content(raw)

    def page_to_model(
        self, page: int, content: EmbeddedContent, print_page: str | None = None
    ) -> PageModel:
        """Построить :class:`PageModel` из содержимого (делегирует structure_analyzer)."""

        return structure_analyzer.page_to_model(page, content, print_page)


__all__ = ["Parser", "book_id_from_source"]
