"""Use case: загрузка книги (метаданные + страницы) через сервисный слой.

Оркестрирует загрузку как чистый use case. Зависимости (fetcher/parser)
внедряются через Protocol-интерфейсы (Dependency Inversion) — use case их не
создаёт сам и не зависит от конкретных реализаций httpx/selectolax.

Особенности:

* диапазон страниц ``from_page``/``to_page`` валидируется — при нарушении
  поднимается :class:`InputValidationError` вместо молчаливого пустого
  результата;
* прогресс логируется через structlog, без CLI-зависимостей в use case.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from biblioatom.errors import FetchError, InputValidationError, ParseError
from biblioatom.logging_config import get_logger
from biblioatom.models import EmbeddedContent, PageModel, TocEntry
from biblioatom.services import FetcherProtocol, ParserProtocol

_logger = get_logger(__name__)


@dataclass(slots=True)
class FetchedBook:
    """Результат загрузки книги: метаданные, оглавление и страницы.

    ``failed_pages`` хранит номера страниц, которые не удалось получить (запрос
    завершился доменной ошибкой) — загрузка продолжается best-effort, чтобы один
    сбойный лист не обрывал всю книгу.
    """

    book_id: str
    title: str
    max_page: int
    toc: list[TocEntry] = field(default_factory=list)
    pages: list[PageModel] = field(default_factory=list)
    failed_pages: list[int] = field(default_factory=list)


def book_payload(book: FetchedBook) -> dict[str, object]:
    """JSON-сериализуемое представление книги (формат ``book.json``).

    Единый формат для команды ``fetch`` и use case ``download_book`` —
    читается обратно ``_load_book_from_json`` (CLI ``analyze``/``build``).
    """

    return {
        "title": book.title,
        "book_id": book.book_id,
        "max_page": book.max_page,
        "toc": [entry.model_dump() for entry in book.toc],
        "pages": [page.model_dump() for page in book.pages],
    }


def validate_page_range(from_page: int, to_page: int, max_page: int) -> None:
    """Проверить корректность диапазона страниц.

    Требования: ``from_page >= 0`` (физический индекс 0-based), ``to_page >=
    from_page`` и ``to_page`` не превышает общее число страниц ``max_page``.
    Нарушение — :class:`InputValidationError` (а не пустой результат).
    """

    if from_page < 0:
        raise InputValidationError(
            "from_page must be >= 0.",
            context={"from_page": from_page},
        )
    if to_page < from_page:
        raise InputValidationError(
            "to_page must be >= from_page.",
            context={"from_page": from_page, "to_page": to_page},
        )
    if to_page > max_page:
        raise InputValidationError(
            "to_page exceeds the book's last page.",
            context={"to_page": to_page, "max_page": max_page},
        )


def fetch_book(
    fetcher: FetcherProtocol,
    parser: ParserProtocol,
    book_id: str,
    *,
    from_page: int = 0,
    to_page: int | None = None,
    delay_ms: int = 0,
) -> FetchedBook:
    """Загрузить книгу: метаданные, TOC и диапазон страниц.

    :param fetcher: источник данных (реализация :class:`FetcherProtocol`).
    :param parser: парсер содержимого страниц (:class:`ParserProtocol`).
    :param book_id: идентификатор книги.
    :param from_page: первая страница диапазона (0-based, включительно).
    :param to_page: последняя страница (включительно); ``None`` → до ``max_page``.
    :param delay_ms: пауза между запросами страниц, мс (вежливость к серверу).
    :raises InputValidationError: при некорректном диапазоне страниц.
    """

    meta = fetcher.fetch_book_meta(book_id)
    title, max_page = meta.title, meta.max_page
    if to_page is None:
        to_page = max_page

    validate_page_range(from_page, to_page, max_page)

    # M3: число страниц получено через fallback (HTML не содержал data-rel).
    # Предел «выдуман» и неотличим от настоящего на уровне валидации, поэтому
    # явно предупреждаем — книга может скачаться частично/пусто. Поведение
    # остаётся best-effort (не hard-error), но больше не тихим.
    if meta.page_count_is_fallback:
        _logger.warning(
            "fetch_book.page_count_is_fallback",
            book_id=book_id,
            max_page=max_page,
            to_page=to_page,
        )

    toc = fetcher.fetch_toc(book_id)
    # Печатные номера страниц из TOC, привязанные к физическому индексу.
    print_pages = {entry.page: entry.print_page for entry in toc}

    total = to_page - from_page + 1
    _logger.info(
        "fetch_book.start",
        book_id=book_id,
        title=title,
        from_page=from_page,
        to_page=to_page,
        total=total,
    )

    pages: list[PageModel] = []
    failed: list[int] = []

    for index, page_no in enumerate(range(from_page, to_page + 1)):
        try:
            content = fetcher.fetch_page(book_id, page_no)
        except (FetchError, ParseError) as exc:
            # M2: best-effort только по доменным ошибкам — сетевой сбой или
            # неразбираемый ответ страницы не рвёт всю книгу. Программные
            # исключения (AttributeError/KeyError/TypeError) сюда НЕ попадают и
            # всплывают наружу, чтобы баги не маскировались под сбой страницы.
            failed.append(page_no)
            _logger.warning(
                "fetch_book.page_failed",
                book_id=book_id,
                page=page_no,
                error=str(exc),
            )
            content = EmbeddedContent(valid=False)

        model = parser.page_to_model(page_no, content)
        # print_page приходит из TOC и привязан к физическому индексу; модель
        # строится парсером без него, поэтому проставляем здесь.
        print_page = print_pages.get(page_no)
        if print_page is not None:
            model.print_page = print_page
        # Страница 0 — всегда обложка (нет печатного номера, CDN = 0000.jpg).
        if page_no == 0:
            model.is_cover = True
        pages.append(model)

        _logger.debug(
            "fetch_book.page_done",
            book_id=book_id,
            page=page_no,
            done=index + 1,
            total=total,
        )

        if delay_ms > 0 and page_no < to_page:
            time.sleep(delay_ms / 1000.0)

    _logger.info(
        "fetch_book.done",
        book_id=book_id,
        pages=len(pages),
        failed=len(failed),
    )

    return FetchedBook(
        book_id=book_id,
        title=title,
        max_page=max_page,
        toc=toc,
        pages=pages,
        failed_pages=failed,
    )


__all__ = ["FetchedBook", "book_payload", "fetch_book", "validate_page_range"]
