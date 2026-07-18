"""Тесты загрузки конфигурации из окружения и обработки ошибок валидации."""

from __future__ import annotations

from pathlib import Path

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


def test_scan_extraction_filter_defaults() -> None:
    """Группа сканов содержит все фильтры с дефолтами по умолчанию."""

    settings = get_settings()
    scan = settings.scan_extraction

    assert 0 <= scan.min_area_ratio < scan.max_area_ratio <= 1
    assert scan.min_aspect < scan.max_aspect
    assert 0 <= scan.min_rectangularity <= 1
    assert scan.crop_padding >= 0


def test_even_blur_kernel_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Чётный размер ядра отклоняется валидатором конфига."""

    monkeypatch.setenv("BIBLIOATOM_SCAN_EXTRACTION__BLUR_KERNEL", "4")

    with pytest.raises(ConfigurationError):
        get_settings()


def test_app_work_dir_default() -> None:
    """Дефолтный путь работной директории — Path('books')."""

    settings = get_settings()
    assert settings.app.work_dir == Path("books")


def test_app_work_dir_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Путь работной директории может быть переопределён через env."""

    monkeypatch.setenv("BIBLIOATOM_APP__WORK_DIR", "/tmp/mybooks")
    settings = get_settings()
    assert settings.app.work_dir == Path("/tmp/mybooks")
