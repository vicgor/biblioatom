"""Тесты загрузки конфигурации из окружения и обработки ошибок валидации."""

from __future__ import annotations

import pytest

from biblioatom.config import get_settings
from biblioatom.errors import ConfigurationError


def test_env_nested_delimiter_overrides_http_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Вложенное поле подхватывается через env_nested_delimiter ``__``."""

    monkeypatch.setenv("BIBLIOATOM_HTTP__TIMEOUT", "60")

    settings = get_settings()

    assert settings.http.timeout == 60.0


def test_invalid_http_timeout_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Невалидное значение (timeout <= 0) приводит к ConfigurationError."""

    monkeypatch.setenv("BIBLIOATOM_HTTP__TIMEOUT", "0")

    with pytest.raises(ConfigurationError) as exc_info:
        get_settings()

    err = exc_info.value
    assert "Invalid configuration" in err.message
    assert "details" in err.context


def test_invalid_logging_level_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Невалидный уровень логирования отклоняется на этапе валидации конфига."""

    monkeypatch.setenv("BIBLIOATOM_LOGGING__LEVEL", "DEBG")

    with pytest.raises(ConfigurationError):
        get_settings()


def test_logging_level_normalized_to_uppercase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Валидный уровень в нижнем регистре нормализуется к верхнему."""

    monkeypatch.setenv("BIBLIOATOM_LOGGING__LEVEL", "debug")

    settings = get_settings()

    assert settings.logging.level == "DEBUG"
