"""Тесты сервиса очистки/нормализации текста (``html_cleaner``).

Адаптированы из legacy ``tests/test_convert.py`` и дополнены регрессионными
кейсами на исправленные при переносе баги (нормализация ведущего числа,
защита slugify от path traversal).
"""

from __future__ import annotations

from biblioatom.services.html_cleaner import (
    clean_pagehtml,
    normalize_text,
    output_stem,
    slugify,
    strip_tags_preserve_text,
)


class TestNormalizeText:
    def test_collapses_extra_newlines(self) -> None:
        assert normalize_text("a\n\n\n\nb") == "a\n\nb"

    def test_replaces_nbsp(self) -> None:
        assert "\xa0" not in normalize_text("a\xa0b")
        assert normalize_text("a\xa0b") == "a b"

    def test_collapses_spaces(self) -> None:
        assert normalize_text("a    b") == "a b"

    def test_empty(self) -> None:
        assert normalize_text("") == ""
        assert normalize_text(None) == ""

    def test_preserves_significant_leading_number(self) -> None:
        # Регресс: legacy-версия превращала "1941\nгод" → "год", срезая значимое
        # ведущее число. Теперь число сохраняется.
        assert normalize_text("1941\nгод") == "1941\nгод"

    def test_does_not_strip_leading_page_like_number(self) -> None:
        # Удаление номера страницы вынесено в извлечение блоков (фильтр page-no),
        # normalize_text больше не трогает ведущие числа.
        assert normalize_text("42\nТекст") == "42\nТекст"


class TestCleanPagehtml:
    def test_removes_comments(self) -> None:
        assert clean_pagehtml("<!-- c -->X") == "X"

    def test_renames_page_class(self) -> None:
        assert 'class="page-no"' in clean_pagehtml('<p class="page">5</p>')
        assert "class='page-no'" in clean_pagehtml("<p class='page'>5</p>")

    def test_renames_page_class_with_spaces_around_eq(self) -> None:
        # Регресс: устойчивость к пробелам вокруг "=" (раньше строковая замена
        # покрывала только class="page" / class='page' впритык).
        assert 'class="page-no"' in clean_pagehtml('<p class = "page">5</p>')
        assert "class='page-no'" in clean_pagehtml("<p class= 'page'>5</p>")

    def test_renames_page_class_case_insensitive(self) -> None:
        assert "page-no" in clean_pagehtml('<p CLASS="page">5</p>')

    def test_empty(self) -> None:
        assert clean_pagehtml(None) == ""


class TestStripTagsPreserveText:
    def test_br_becomes_newline(self) -> None:
        assert strip_tags_preserve_text("a<br/>b") == "a\nb"

    def test_unescapes_entities(self) -> None:
        assert strip_tags_preserve_text("<p>a &amp; b</p>") == "a & b"


class TestSlugify:
    def test_basic(self) -> None:
        assert slugify("Моя Книга") == "моя_книга"

    def test_empty_fallback(self) -> None:
        assert slugify("") == "book"
        assert slugify("***") == "book"

    def test_truncates_to_120(self) -> None:
        assert len(slugify("a" * 300)) <= 120

    def test_no_path_traversal_slashes(self) -> None:
        # Регресс: slug не должен содержать сепараторов путей или "..".
        result = slugify("../../etc/passwd")
        assert "/" not in result
        assert "\\" not in result
        assert ".." not in result

    def test_no_path_traversal_backslashes(self) -> None:
        result = slugify("..\\..\\windows\\system32")
        assert "/" not in result
        assert "\\" not in result
        assert ".." not in result

    def test_dotdot_collapsed(self) -> None:
        assert ".." not in slugify("a..b")


class TestOutputStem:
    def test_basic(self) -> None:
        stem = output_stem(title="Моя Книга", book_id="my_book", page_range=[0, 100])
        assert "моя" in stem
        assert "0-100" in stem

    def test_prefix(self) -> None:
        stem = output_stem(title="Книга", book_id="book", page_range=[0, 10], prefix="test")
        assert stem.startswith("test")

    def test_no_page_range(self) -> None:
        stem = output_stem(title="Книга", book_id="book")
        assert "-" not in stem.replace("книга", "").replace("book", "")
