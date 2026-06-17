"""Общие фикстуры pytest."""

from __future__ import annotations

import io

import pytest
import structlog


@pytest.fixture(autouse=True)
def _reset_structlog_output() -> None:
    """'Перенаправляет structlog PrintLogger на StringIO на время каждого теста.

    Без этого structlog пытается писать в stderr, который pytest захватывает или
    закрывает в определённых тестах — причина ``ValueError: IO operation on
    closed file``.
    """
    buf = io.StringIO()
    structlog.configure(
        processors=[
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=buf),
        cache_logger_on_first_use=False,
    )
