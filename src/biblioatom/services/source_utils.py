"""Утилиты для разбора источника книги (URL или идентификатор).

Вынесены из ``parser.py`` в отдельный модуль, чтобы:

* избежать отложенного ``import`` внутри тела функции (workaround против
  несуществующего циклического импорта);
* следовать SRP: ``parser.py`` отвечает за разбор HTML/JSON, а не за
  нормализацию идентификаторов источника;
* при добавлении нового источника (другой сайт, другая схема URL) менять
  только этот модуль — CLI и ``parser`` остаются неизменными.
"""

from __future__ import annotations

from biblioatom.errors import InputValidationError


def book_id_from_source(source: str) -> str:
    """Извлечь идентификатор книги из URL или вернуть строку как есть.

    Поддерживаются две формы::

        kapitsa_1994
        https://elib.biblioatom.ru/text/kapitsa_1994/

    :raises InputValidationError: если из строки не удалось извлечь идентификатор.
    """
    cleaned = source.strip().rstrip("/")
    if "/text/" in cleaned:
        tail = cleaned.split("/text/", 1)[1]
        candidate = tail.split("/", 1)[0]
        if candidate:
            return candidate
    if "/" in cleaned or not cleaned:
        raise InputValidationError(
            "Could not derive a book id from the given source.",
            context={"source": source},
        )
    return cleaned


__all__ = ["book_id_from_source"]
