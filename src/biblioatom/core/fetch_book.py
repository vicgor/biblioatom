"""Use case: загрузка книги (метаданные + страницы) через сервисный слой.

Перенос оркестрации legacy ``download_book`` (``fetch.py``) в чистый use case.
Зависимости (fetcher/parser) внедряются через Protocol-интерфейсы
(Dependency Inversion) — use case их не создаёт сам и не зависит от конкретных
реализаций httpx/selectolax.

Исправления багов ревью:

* добавлена валидация диапазона страниц ``from_page``/``to_page`` — при
  нарушении поднимается :class:`InputValidationError` вместо молчаливого пустого
  результата;
* прогресс логируется через structlog (Rich-прогресс появится в CLI на Этапе 6),
  без CLI-зависимостей в use case.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from biblioatom.errors import InputValidationError
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


def _validate_page_range(from_page: int, to_page: int, max_page: int) -> None:
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

    title, max_page = fetcher.fetch_book_meta(book_id)
    if to_page is None:
        to_page = max_page

    _validate_page_range(from_page, to_page, max_page)

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
        except Exception as exc:  # noqa: BLE001 — best-effort: один сбой не рвёт книгу
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


__all__ = ["FetchedBook", "fetch_book"]
