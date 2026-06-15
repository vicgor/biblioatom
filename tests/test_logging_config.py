"""Тесты чистых процессоров structlog: redact_secrets и add_correlation_id."""

from __future__ import annotations

import logging

from biblioatom.logging_config import (
    _REDACTED,
    add_correlation_id,
    redact_secrets,
    set_correlation_id,
)

_LOGGER = logging.getLogger("test")


def test_redact_secrets_top_level() -> None:
    event = redact_secrets(_LOGGER, "info", {"event": "login", "password": "hunter2"})

    assert event["password"] == _REDACTED
    assert event["event"] == "login"


def test_redact_secrets_nested_dict() -> None:
    """Секреты во вложенном dict (headers/Authorization) маскируются."""

    event = redact_secrets(
        _LOGGER,
        "info",
        {"event": "request", "headers": {"Authorization": "Bearer tok", "Accept": "json"}},
    )

    headers = event["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == _REDACTED
    assert headers["Accept"] == "json"


def test_redact_secrets_in_list_of_dicts() -> None:
    event = redact_secrets(
        _LOGGER,
        "info",
        {"items": [{"token": "abc"}, {"name": "ok"}]},
    )

    items = event["items"]
    assert isinstance(items, list)
    assert items[0]["token"] == _REDACTED
    assert items[1]["name"] == "ok"


def test_redact_secrets_deeply_nested() -> None:
    event = redact_secrets(
        _LOGGER,
        "info",
        {"outer": {"inner": {"secret": "s"}}},
    )

    assert event["outer"]["inner"]["secret"] == _REDACTED


def test_add_correlation_id_present() -> None:
    set_correlation_id("cid-123")
    try:
        event = add_correlation_id(_LOGGER, "info", {"event": "x"})
        assert event["correlation_id"] == "cid-123"
    finally:
        set_correlation_id(None)


def test_add_correlation_id_absent() -> None:
    set_correlation_id(None)
    event = add_correlation_id(_LOGGER, "info", {"event": "x"})

    assert "correlation_id" not in event
