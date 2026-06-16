"""Настройка структурированного логирования на structlog поверх stdlib logging.

Для интерактивного терминала (tty/dev) используется цветной ``ConsoleRenderer``,
иначе — ``JSONRenderer``. Выбор рендерера основан на ``sys.stderr.isatty()``.
Поддерживается correlation_id через ``contextvars``, временные метки и редакция
секретов.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any, cast

import structlog
from structlog.types import EventDict, FilteringBoundLogger, Processor

#: Публичный псевдоним типа логгера, возвращаемого :func:`get_logger`.
#:
#: ``structlog.get_logger()`` аннотирован как ``Any`` в самой библиотеке
#: (``BoundLoggerLazyProxy`` делегирует методы динамически), поэтому мы
#: используем ``FilteringBoundLogger`` — Protocol из ``structlog.types``,
#: объявляющий весь публичный API (info/debug/warning/error/…).
#: Это позволяет mypy проверять вызовы логгера во всём проекте.
BoundLogger = FilteringBoundLogger

#: Идентификатор корреляции для связывания записей в рамках одной операции.
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

#: Ключи, значения которых должны быть скрыты в логах.
_SECRET_KEYS = frozenset({"password", "token", "secret", "authorization", "api_key", "cookie"})

_REDACTED = "***REDACTED***"


def set_correlation_id(value: str | None) -> None:
    """Установить correlation_id для текущего контекста выполнения."""
    _correlation_id.set(value)


def add_correlation_id(
    _logger: logging.Logger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """Процессор: добавить correlation_id в запись, если он задан."""
    cid = _correlation_id.get()
    if cid is not None:
        event_dict["correlation_id"] = cid
    return event_dict


def _redact_value(value: Any) -> Any:
    """Рекурсивно замаскировать секреты во вложенных dict и списках dict."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if k.lower() in _SECRET_KEYS else _redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def redact_secrets(_logger: logging.Logger, _method_name: str, event_dict: EventDict) -> EventDict:
    """Процессор: заменить значения секретных ключей на маску на любой глубине."""
    for key in list(event_dict.keys()):
        if key.lower() in _SECRET_KEYS:
            event_dict[key] = _REDACTED
        else:
            event_dict[key] = _redact_value(event_dict[key])
    return event_dict


def setup_logging(level: str = "INFO", *, json_logs: bool | None = None) -> None:
    """Сконфигурировать structlog и stdlib logging.

    :param level: минимальный уровень (``DEBUG``..``CRITICAL``); валидируется в
        :class:`~biblioatom.config.LoggingSettings`.
    :param json_logs: принудительно включить/выключить JSON-рендеринг. Если
        ``None`` — выбирается автоматически по ``sys.stderr.isatty()``: JSON для
        не-tty, цветной вывод для интерактивного терминала.
    """
    if json_logs is None:
        json_logs = not sys.stderr.isatty()

    numeric_level: int = logging.getLevelName(level.upper())

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        add_correlation_id,
        redact_secrets,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[*shared_processors, structlog.processors.format_exc_info, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=numeric_level,
    )


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    """Вернуть structlog-логгер (опционально именованный)."""
    return cast(FilteringBoundLogger, structlog.get_logger(name))


__all__ = [
    "add_correlation_id",
    "BoundLogger",
    "get_logger",
    "redact_secrets",
    "set_correlation_id",
    "setup_logging",
]
