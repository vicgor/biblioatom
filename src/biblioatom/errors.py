"""Иерархия доменных ошибок и коды завершения процесса.

Все исключения проекта наследуются от :class:`BookgrabError`. Каждое исключение
несёт человекочитаемое сообщение и опциональный контекст (``dict``) для
структурированного логирования. Сцепление исключений выполняется штатным
механизмом ``raise ... from err`` — оригинальная причина доступна через
``__cause__``.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class ExitCode(IntEnum):
    """Коды завершения CLI-процесса.

    Значения стабильны и являются частью контракта с вызывающей стороной
    (скрипты, CI). ``OK`` — успех; остальные соответствуют категориям ошибок.
    """

    OK = 0
    CONFIGURATION = 2
    INPUT_VALIDATION = 3
    FETCH = 4
    PARSE = 5
    STRUCTURE_ANALYSIS = 6
    IMAGE = 7
    EPUB_BUILD = 8
    EXTERNAL_TOOL = 10


class BookgrabError(Exception):
    """Базовое исключение проекта.

    :param message: человекочитаемое описание ошибки.
    :param context: (keyword-only) произвольные структурированные данные для логов.
    """

    #: Код завершения, ассоциированный с этим классом ошибки.
    exit_code: ExitCode = ExitCode.CONFIGURATION

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = context or {}

    def __str__(self) -> str:
        if self.context:
            return f"{self.message} (context={self.context!r})"
        return self.message


class ConfigurationError(BookgrabError):
    """Некорректная или отсутствующая конфигурация."""

    exit_code = ExitCode.CONFIGURATION


class InputValidationError(BookgrabError):
    """Невалидные аргументы пользователя (диапазон страниц, идентификатор и т.п.)."""

    exit_code = ExitCode.INPUT_VALIDATION


class WorkspaceError(InputValidationError):
    """Ошибка работы с рабочим каталогом книги (запись/удаление кэша)."""

    exit_code = ExitCode.INPUT_VALIDATION


class FetchError(BookgrabError):
    """Сбой сетевого взаимодействия с источником."""

    exit_code = ExitCode.FETCH


class HttpTimeoutError(FetchError):
    """Истёк таймаут HTTP-запроса."""

    exit_code = ExitCode.FETCH


class ResourceNotFoundError(FetchError):
    """Запрошенный ресурс отсутствует (HTTP 404)."""

    exit_code = ExitCode.FETCH


class ParseError(BookgrabError):
    """Не удалось разобрать полученные данные (HTML/JSON)."""

    exit_code = ExitCode.PARSE


class StructureAnalysisError(BookgrabError):
    """Сбой структурного анализа документа (главы/блоки/TOC)."""

    exit_code = ExitCode.STRUCTURE_ANALYSIS


class ScanExtractionError(BookgrabError):
    """Сбой извлечения иллюстраций со сканов (OpenCV)."""

    exit_code = ExitCode.IMAGE


class ImageProcessingError(BookgrabError):
    """Сбой постобработки изображений (Pillow)."""

    exit_code = ExitCode.IMAGE


class EpubBuildError(BookgrabError):
    """Сбой сборки EPUB."""

    exit_code = ExitCode.EPUB_BUILD


class ConversionError(BookgrabError):
    """Сбой конвертации формата (например, EPUB → AZW3)."""

    exit_code = ExitCode.EXTERNAL_TOOL


class ExternalToolNotFoundError(ConversionError):
    """Внешний инструмент (``ebook-convert``) не найден в системе."""

    exit_code = ExitCode.EXTERNAL_TOOL


class ExternalToolExecutionError(ConversionError):
    """Внешний инструмент завершился с ошибкой."""

    exit_code = ExitCode.EXTERNAL_TOOL


def exit_code_for(exc: BaseException) -> ExitCode:
    """Сопоставить исключение с кодом завершения процесса.

    Для :class:`BookgrabError` берётся атрибут ``exit_code`` соответствующего
    класса. Любое другое исключение трактуется как неожиданная ошибка
    конфигурации/окружения.

    Ограничение: ``KeyboardInterrupt`` и ``SystemExit`` (оба — ``BaseException``,
    но не ``BookgrabError``) тоже отображаются на ``CONFIGURATION``. Отдельный код
    для них не выделяется; обработку прерывания/выхода следует выполнять выше по
    стеку (в точке входа CLI), не доводя их до этого маппинга.
    """

    if isinstance(exc, BookgrabError):
        return exc.exit_code
    return ExitCode.CONFIGURATION


__all__ = [
    "BookgrabError",
    "ConfigurationError",
    "ConversionError",
    "EpubBuildError",
    "ExitCode",
    "ExternalToolExecutionError",
    "ExternalToolNotFoundError",
    "FetchError",
    "HttpTimeoutError",
    "ImageProcessingError",
    "InputValidationError",
    "ParseError",
    "ResourceNotFoundError",
    "ScanExtractionError",
    "StructureAnalysisError",
    "WorkspaceError",
    "exit_code_for",
]
