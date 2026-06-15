"""Очистка и нормализация текста/HTML страниц.

Перенос чистой логики из legacy ``convert.py`` с типизацией. На этом этапе
парсинг остаётся на регулярных выражениях; реализация структурирована так,
чтобы парсер (selectolax) можно было подменить на следующем этапе, не меняя
публичный контракт функций.
"""

from __future__ import annotations

import html
import re

# Регулярки компилируются один раз на уровне модуля.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_CLOSE_P_RE = re.compile(r"</p\s*>", re.I)
_CLOSE_DIV_RE = re.compile(r"</div\s*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_TRAILING_WS_NEWLINE_RE = re.compile(r"[ \t]+\n")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_NON_WORD_RE = re.compile(r"[^\w\s]", re.U)
_WS_RE = re.compile(r"\s+")
_UNDERSCORES_RE = re.compile(r"_+")


def normalize_text(text: str | None) -> str:
    """Нормализовать текст страницы: пробелы, неразрывные пробелы, переводы строк.

    В отличие от legacy-реализации НЕ срезает ведущее число: старая строка
    ``re.sub(r"^\\d+\\s*\\n+", "", ...)`` ошибочно удаляла значимое ведущее
    число (например ``"1941\\nгод"`` → ``"год"``). Удаление номера страницы
    выполняется на уровне извлечения блоков (фильтр ``page-no``), а не здесь.
    """

    s = text or ""
    s = s.replace("\r", "")
    s = s.replace(" ", " ")  # неразрывный пробел → обычный
    s = _MULTI_NEWLINE_RE.sub("\n\n", s)
    s = _TRAILING_WS_NEWLINE_RE.sub("\n", s)
    s = _MULTI_SPACE_RE.sub(" ", s)
    return s.strip()


def clean_pagehtml(pagehtml: str | None) -> str:
    """Убрать комментарии и переименовать класс ``page`` → ``page-no``."""

    s = pagehtml or ""
    s = _COMMENT_RE.sub("", s)
    s = s.replace('class="page"', 'class="page-no"')
    s = s.replace("class='page'", "class='page-no'")
    return s.strip()


def strip_tags_preserve_text(s: str) -> str:
    """Удалить HTML-теги, сохранив текст; ``<br>``/``</p>``/``</div>`` → перевод строки."""

    s = _BR_RE.sub("\n", s)
    s = _CLOSE_P_RE.sub("\n", s)
    s = _CLOSE_DIV_RE.sub("\n", s)
    s = _TAG_RE.sub("", s)
    return html.unescape(s).strip()


def slugify(text: str) -> str:
    """Преобразовать строку в безопасный slug для имени файла.

    Защита от path traversal: результат не содержит ``/``, ``\\`` или ``..`` —
    все небуквенно-цифровые символы (включая разделители путей и точки)
    удаляются ещё на шаге ``_NON_WORD_RE``.
    """

    s = (text or "").strip().lower()
    s = _NON_WORD_RE.sub("", s)
    s = _WS_RE.sub("_", s)
    s = _UNDERSCORES_RE.sub("_", s)
    slug = s.strip("_")[:120] or "book"
    # Подстраховка на случай изменения регулярок выше: гарантируем отсутствие
    # сепараторов путей и относительных переходов.
    slug = slug.replace("/", "").replace("\\", "").replace("..", "")
    return slug or "book"


def output_stem(
    title: str = "",
    book_id: str = "",
    page_range: tuple[int, int] | list[int] | None = None,
    prefix: str = "",
) -> str:
    """Собрать имя выходного файла (без расширения) из метаданных книги."""

    page_part = ""
    if isinstance(page_range, (list, tuple)) and len(page_range) == 2:
        page_part = f"{page_range[0]}-{page_range[1]}"
    parts = [prefix.strip(), slugify(title), slugify(book_id), page_part]
    return "_".join(p for p in parts if p)


__all__ = [
    "clean_pagehtml",
    "normalize_text",
    "output_stem",
    "slugify",
    "strip_tags_preserve_text",
]
