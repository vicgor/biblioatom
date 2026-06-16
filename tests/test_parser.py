"""Тесты парсера (``services/parser.py``) на локальных HTML/JSON-фикстурах.

Без сети: проверяются разбор метаданных, оглавления (soft hyphen, nbsp, уровень,
автор, печатный номер) и edge cases (нет ``</aside>``, пустой TOC).
"""

from __future__ import annotations

from biblioatom.config import ParsingSettings
from biblioatom.models import EmbeddedContent
from biblioatom.services.parser import Parser

# Фрагмент реального TOC: обложка (без печатного номера), запись с soft hyphen и
# nbsp, вложенная запись с автором (дефисная фамилия) и печатным номером.
_TOC_HTML = """
<html><body>
<aside data-type="tree-box-contents" data-max-level="1" data-parent="root">
<a class="tree-level-0" data-goto-page="0" data-level="0">
<ins><span></span></ins>
<span class="desc"><span>Обложка</span></span>
</a>
<a class="tree-level-0" data-goto-page="6" data-level="0">
<ins><span></span></ins>
<span class="desc"><span>От&nbsp;состави&shy;те&shy;лей</span></span>
<span class="info pageno">5</span>
</a>
<a class="tree-level-1" data-goto-page="8" data-level="1">
<ins><span></span></ins>
<span class="desc">
<span class="info author">Боровик-Романов&nbsp;А.&nbsp;С.</span>
<span>Жизнь и&nbsp;деятель&shy;ность</span>
</span>
<span class="info pageno">7</span>
</a>
</aside>
</body></html>
"""

_META_HTML = (
    "<html><head><title>Капица / Просмотр</title></head>"
    "<body>"
    '<div class="page-gfx" data-rel="42"></div>'
    '<div class="page-gfx" data-rel="99"></div>'
    "</body></html>"
)


def _parser() -> Parser:
    return Parser(ParsingSettings())


class TestParseBookMeta:
    def test_extracts_title_and_max_page(self) -> None:
        title, max_page = _parser().parse_book_meta(_META_HTML, "kapitsa_1994")
        assert title == "Капица"
        assert max_page == 99

    def test_title_fallback_to_book_id(self) -> None:
        html = "<html><head></head><body></body></html>"
        title, max_page = _parser().parse_book_meta(html, "some_book")
        assert title == "some_book"

    def test_max_page_fallback_from_config(self) -> None:
        settings = ParsingSettings(fallback_max_page=777)
        html = "<html><head><title>Без навигации</title></head><body></body></html>"
        title, max_page = Parser(settings).parse_book_meta(html, "norel")
        assert title == "Без навигации"
        assert max_page == 777

    def test_non_numeric_data_rel_ignored(self) -> None:
        html = (
            "<html><head><title>Книга</title></head><body>"
            '<div data-rel="abc"></div><div data-rel="12"></div>'
            "</body></html>"
        )
        _title, max_page = _parser().parse_book_meta(html, "b")
        assert max_page == 12


class TestParseToc:
    def test_parses_all_entries(self) -> None:
        toc = _parser().parse_toc(_TOC_HTML)
        assert len(toc) == 3

    def test_cover_entry_fields(self) -> None:
        cover = _parser().parse_toc(_TOC_HTML)[0]
        assert cover.title == "Обложка"
        assert cover.page == 0
        assert cover.print_page is None
        assert cover.level == 0
        assert cover.author is None

    def test_soft_hyphen_and_nbsp_stripped(self) -> None:
        entry = _parser().parse_toc(_TOC_HTML)[1]
        assert entry.title == "От составителей"
        assert entry.print_page == "5"

    def test_author_and_level_and_print_page(self) -> None:
        entry = _parser().parse_toc(_TOC_HTML)[2]
        assert entry.author is not None
        assert "Боровик-Романов" in entry.author
        assert entry.level == 1
        assert entry.print_page == "7"
        # Автор и печатный номер не должны просочиться в заголовок.
        assert "Боровик" not in entry.title
        assert entry.title == "Жизнь и деятельность"

    def test_missing_aside_returns_empty(self) -> None:
        assert _parser().parse_toc("<html><body><p>нет оглавления</p></body></html>") == []

    def test_unclosed_aside_does_not_raise(self) -> None:
        # Незакрытый </aside>: selectolax-парсер толерантен, ValueError не летит.
        html = (
            '<html><body><aside data-type="tree-box-contents">'
            '<a data-goto-page="3" data-level="0"><span>Глава</span></a>'
        )
        toc = _parser().parse_toc(html)
        assert len(toc) == 1
        assert toc[0].page == 3

    def test_empty_toc_aside(self) -> None:
        html = '<html><body><aside data-type="tree-box-contents"></aside></body></html>'
        assert _parser().parse_toc(html) == []

    def test_link_without_required_attrs_skipped(self) -> None:
        html = (
            '<html><body><aside data-type="tree-box-contents">'
            '<a data-goto-page="3" data-level="0"><span>Норм</span></a>'
            "<a><span>Без атрибутов</span></a>"
            "</aside></body></html>"
        )
        toc = _parser().parse_toc(html)
        assert len(toc) == 1


class TestEmbeddedContentDelegation:
    def test_parse_json_string(self) -> None:
        raw = '{"valid": true, "pagetext": "Текст", "pagehtml": "<p>x</p>"}'
        content = _parser().parse_embedded_content(raw)
        assert content.valid is True
        assert content.pagetext == "Текст"

    def test_parse_invalid_json(self) -> None:
        content = _parser().parse_embedded_content("not json")
        assert content.valid is False
        assert content.pagetext == "not json"

    def test_page_to_model(self) -> None:
        content = EmbeddedContent(valid=True, pagetext="абзац", pagehtml="")
        model = _parser().page_to_model(4, content)
        assert model.page == 4
        assert model.elements
