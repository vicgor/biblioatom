"""Сетевой слой: загрузка данных книги через httpx с ретраями на tenacity.

Замена legacy ``urllib``-реализации (``fetch.py``) на ``httpx.Client`` с
политикой ретраев на ``tenacity``. Реализует
:class:`~biblioatom.services.FetcherProtocol`.

Политика ретраев (исправление багов ревью):

* повторяем ТОЛЬКО транзиентные сбои: таймауты, сетевые ошибки соединения и
  ответы со статусами из ``Http.retry_statuses`` (по умолчанию 429/500/502/503/
  504) плюс 408 (Request Timeout);
* НЕ повторяем прочие 4xx — в частности 404 (legacy ретраил любой код, включая
  404; здесь это исправлено);
* экспоненциальный backoff с джиттером, число попыток и пределы — из config;
* на каждую повторную попытку пишется WARNING через structlog.

Внешние сбои оборачиваются в доменные ошибки (:mod:`biblioatom.errors`) с
цепочкой ``raise ... from exc``: таймаут → :class:`HttpTimeoutError`, 404 →
:class:`ResourceNotFoundError`, прочие сетевые/HTTP-сбои → :class:`FetchError`.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from urllib.parse import quote

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from biblioatom.config import AppSettings, HttpSettings, ParsingSettings
from biblioatom.errors import (
    FetchError,
    HttpTimeoutError,
    ResourceNotFoundError,
)
from biblioatom.logging_config import get_logger
from biblioatom.models import BookMeta, EmbeddedContent, TocEntry
from biblioatom.services.parser import Parser

_logger = get_logger(__name__)

T = TypeVar("T")

#: Дополнительные статусы (помимо config.retry_statuses), считающиеся
#: транзиентными. 408 Request Timeout уместно повторить.
_EXTRA_RETRYABLE_STATUSES = frozenset({408})


class _RetryableStatus(Exception):
    """Внутренний сигнал: HTTP-ответ имеет транзиентный статус и его стоит повторить.

    Не доменная ошибка — используется только для управления ретраями tenacity
    внутри модуля и наружу не выходит.
    """

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"retryable status {response.status_code}")
        self.response = response


class Fetcher:
    """Реализация :class:`FetcherProtocol` на httpx + tenacity.

    :param client: внешний ``httpx.Client`` (для тестов/повторного использования).
        Если не передан — создаётся собственный с таймаутами из config.
    :param app: настройки приложения (base_url, rpc_path, user_agent).
    :param http: настройки HTTP (таймауты, число ретраев, backoff, статусы).
    :param parser: парсер для метаданных/TOC; по умолчанию :class:`Parser`.
    """

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        app: AppSettings | None = None,
        http: HttpSettings | None = None,
        parser: Parser | None = None,
    ) -> None:
        self._app = app or AppSettings()
        self._http = http or HttpSettings()
        self._parser = parser or Parser(ParsingSettings())
        self._retry_statuses = frozenset(self._http.retry_statuses) | _EXTRA_RETRYABLE_STATUSES

        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            timeout = httpx.Timeout(self._http.timeout, connect=self._http.connect_timeout)
            self._client = httpx.Client(
                base_url=self._app.base_url,
                timeout=timeout,
                headers={"User-Agent": self._app.user_agent},
                follow_redirects=True,
            )
            self._owns_client = True

    # -- управление ресурсами ----------------------------------------------

    def close(self) -> None:
        """Закрыть собственный httpx-клиент (внешний не трогаем)."""

        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- политика ретраев ---------------------------------------------------

    @staticmethod
    def _is_retryable(exc: BaseException) -> bool:
        """Повторяем таймауты, транзиентные сетевые ошибки и retryable-статусы."""

        return isinstance(exc, httpx.TimeoutException | httpx.TransportError | _RetryableStatus)

    def _log_retry(self, state: RetryCallState) -> None:
        """WARNING на каждую повторную попытку (до сна перед следующей)."""

        outcome = state.outcome
        exc = outcome.exception() if outcome is not None else None
        _logger.warning(
            "fetch.retry",
            attempt=state.attempt_number,
            max_attempts=self._http.max_retries + 1,
            wait_seconds=round(state.next_action.sleep, 3) if state.next_action else None,
            error=str(exc) if exc else None,
        )

    def _run_with_retry(self, fn: Callable[[], T]) -> T:
        """Выполнить ``fn`` с ретраями согласно политике из config."""

        retrying = Retrying(
            stop=stop_after_attempt(self._http.max_retries + 1),
            wait=wait_exponential_jitter(
                initial=self._http.backoff_factor,
                max=self._http.backoff_max,
            ),
            retry=retry_if_exception(self._is_retryable),
            before_sleep=self._log_retry,
            reraise=True,
        )
        return retrying(fn)

    # -- отладочный дамп HTML -----------------------------------------------

    @staticmethod
    def _dump_ext(content_type: str) -> str:
        """Определить расширение файла дампа по Content-Type заголовку."""
        ct = content_type.lower()
        if "json" in ct:
            return ".json"
        if "html" in ct:
            return ".html"
        if "xml" in ct:
            return ".xml"
        return ".txt"

    def _dump_html_if_debug(self, url: str, response: httpx.Response) -> None:
        """Сохранить тело ответа во временный каталог только при уровне DEBUG.

        Расширение файла определяется по Content-Type: .json/.html/.xml/.txt.
        Файл создаётся один раз за URL (имя = MD5-хэш URL), поэтому повторные
        запросы одной страницы перезаписывают предыдущий дамп, не засоряя каталог.
        Используется ``tempfile.gettempdir()`` вместо захардкоженного ``/tmp``
        для корректной работы на Windows и в окружениях с нестандартным TMPDIR.
        Вызов безопасен при любом уровне логирования — тело метода не выполняется
        если DEBUG не активен.
        """
        if not logging.getLogger(__name__).isEnabledFor(logging.DEBUG):
            return
        content_type = response.headers.get("content-type", "")
        ext = self._dump_ext(content_type)
        slug = hashlib.md5(url.encode()).hexdigest()[:12]
        path = Path(tempfile.gettempdir()) / f"biblioatom_{slug}{ext}"
        try:
            path.write_text(response.text, encoding="utf-8")
        except OSError:
            return
        _logger.debug(
            "fetch.response_dumped",
            url=url,
            path=str(path),
            content_type=content_type,
            size_bytes=len(response.content),
        )

    # -- низкоуровневый GET --------------------------------------------------

    def _get(self, url: str) -> httpx.Response:
        """GET с ретраями; не-retryable HTTP-статусы оборачиваются в доменные ошибки.

        Транзиентные статусы поднимают :class:`_RetryableStatus` (повтор), таймаут
        → :class:`HttpTimeoutError`, сетевой сбой → :class:`FetchError`, 404 →
        :class:`ResourceNotFoundError`, прочие 4xx/5xx → :class:`FetchError`.
        """

        def _attempt() -> httpx.Response:
            response = self._client.get(url)
            if response.status_code in self._retry_statuses:
                raise _RetryableStatus(response)
            return response

        try:
            response = self._run_with_retry(_attempt)
        except _RetryableStatus as exc:
            # Транзиентный статус исчерпал попытки — это окончательный сбой.
            raise FetchError(
                "Request failed after retries.",
                context={"url": url, "status_code": exc.response.status_code},
            ) from exc
        except httpx.TimeoutException as exc:
            raise HttpTimeoutError("HTTP request timed out.", context={"url": url}) from exc
        except httpx.TransportError as exc:
            raise FetchError("HTTP transport error.", context={"url": url}) from exc

        if response.status_code == httpx.codes.NOT_FOUND:
            raise ResourceNotFoundError(
                "Requested resource not found (HTTP 404).",
                context={"url": url, "status_code": 404},
            )
        if response.is_error:
            raise FetchError(
                "HTTP request returned an error status.",
                context={"url": url, "status_code": response.status_code},
            )
        self._dump_html_if_debug(url, response)
        return response

    # -- публичный API (FetcherProtocol) -----------------------------------

    def fetch_book_meta(self, book_id: str) -> BookMeta:
        """Вернуть метаданные книги (:class:`BookMeta`)."""

        url = f"/text/{quote(book_id, safe='')}/"
        response = self._get(url)
        return self._parser.parse_book_meta(response.text, book_id)

    def fetch_toc(self, book_id: str) -> list[TocEntry]:
        """Вернуть оглавление книги (пустой список, если TOC отсутствует)."""

        url = f"/text/{quote(book_id, safe='')}/p0/"
        response = self._get(url)
        return self._parser.parse_toc(response.text)

    def fetch_page(self, book_id: str, page: int) -> EmbeddedContent:
        """Вернуть содержимое одной страницы через RPC-эндпоинт.

        RPC отдаёт JSON. Декодирование и разбор делегируются парсеру
        (``parse_embedded_content``), который принимает сырую JSON-строку и сам
        обрабатывает невалидный JSON (возвращает ``valid=False`` с сырым текстом
        в ``pagetext``, как в legacy) — это штатное содержимое, а не сбой
        запроса. Дублирующее декодирование ``json.loads`` в fetcher убрано.
        """

        rpc = self._app.rpc_path
        url = f"{rpc}?url={quote(book_id, safe='')}&page={quote(str(page), safe='')}"
        response = self._get(url)
        return self._parser.parse_embedded_content(response.text)

    def fetch_image(self, book_id: str, page: int) -> bytes:
        """Вернуть байты JPEG-скана страницы."""

        url = f"/data/{quote(book_id, safe='')}/jpg/{page:04d}.jpg"
        response = self._get(url)
        return response.content


__all__ = ["Fetcher"]
