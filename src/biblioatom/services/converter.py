"""Конвертация EPUB → AZW3 через внешний инструмент Calibre ``ebook-convert``.

Реализует :class:`~biblioatom.services.ConverterProtocol`. Вызов выполняется
через :func:`subprocess.run` со списком аргументов (НЕ ``shell=True``) — нет
риска shell injection. Параметры (путь к бинарю, таймаут) берутся из
:class:`~biblioatom.config.ConversionSettings`.

Обработка ошибок:

* бинарь не найден (:func:`shutil.which` → ``None``) → :class:`ExternalToolNotFoundError`
  без каких-либо ретраев (повтор бессмысленен);
* ненулевой код возврата → :class:`ExternalToolExecutionError` со ``stderr`` в
  контексте;
* истечение таймаута → :class:`ExternalToolExecutionError`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from biblioatom.config import ConversionSettings
from biblioatom.errors import ExternalToolExecutionError, ExternalToolNotFoundError
from biblioatom.logging_config import get_logger

_logger = get_logger(__name__)


class EbookConvertConverter:
    """Конвертер на базе Calibre ``ebook-convert``, реализует ``ConverterProtocol``."""

    def __init__(self, settings: ConversionSettings | None = None) -> None:
        self._settings = settings or ConversionSettings()

    def convert(self, source: Path, target: Path) -> Path:
        """Сконвертировать ``source`` в ``target`` и вернуть путь результата.

        Формат вывода определяется расширением ``target`` (Calibre сам выбирает
        конвертер по расширению, например ``.azw3``).

        :raises ExternalToolNotFoundError: бинарь ``ebook-convert`` недоступен.
        :raises ExternalToolExecutionError: ненулевой код возврата или таймаут.
        """

        binary = self._resolve_binary()
        target.parent.mkdir(parents=True, exist_ok=True)

        # Список аргументов, НЕ строка: shell не задействован → нет injection.
        cmd = [binary, str(source), str(target)]
        _logger.info(
            "converter.start",
            source=str(source),
            target=str(target),
            binary=binary,
        )

        try:
            # shell не задействован: cmd — список аргументов, без shell=True.
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._settings.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExternalToolExecutionError(
                "ebook-convert timed out.",
                context={
                    "binary": binary,
                    "timeout": self._settings.timeout,
                    "source": str(source),
                },
            ) from exc

        if result.returncode != 0:
            raise ExternalToolExecutionError(
                "ebook-convert exited with a non-zero status.",
                context={
                    "binary": binary,
                    "returncode": result.returncode,
                    "stderr": (result.stderr or "").strip(),
                    "source": str(source),
                    "target": str(target),
                },
            )

        _logger.info("converter.done", target=str(target))
        return target

    def _resolve_binary(self) -> str:
        """Найти исполняемый ``ebook-convert`` или поднять ошибку (без ретраев)."""

        binary = self._settings.ebook_convert_bin
        resolved = shutil.which(binary)
        if resolved is None:
            raise ExternalToolNotFoundError(
                "Calibre 'ebook-convert' binary was not found. Install Calibre "
                "or set BIBLIOATOM_CONVERSION__EBOOK_CONVERT_BIN.",
                context={"binary": binary},
            )
        return resolved


__all__ = ["EbookConvertConverter"]
