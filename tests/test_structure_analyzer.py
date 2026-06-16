"""Тесты сервиса структурного анализа (``structure_analyzer``).

Адаптированы из legacy ``tests/test_convert.py`` на новые Pydantic-модели и
дополнены регрессионными кейсами на исправленные при переносе баги:

* дефисные фамилии в ``is_probable_author_line``;
* потеря страниц до первой записи TOC в ``split_by_toc``;
* сужение перехвата исключений в ``parse_embedded_content``.
"""

from __future__ import annotations

from biblioatom.models import (
    BookElement,
    ElementKind,
    EmbeddedContent,
    PageModel,
    TocEntry,
)
from biblioatom.services.structure_analyzer import (
    StructureAnalyzer,
    extract_blocks,
    is_probable_author_line,
    is_probable_heading,
    page_to_model,
    parse_embedded_content,
    should_start_chapter,
    split_by_toc,
    split_into_chapters,
)

_BODY_KIND = ElementKind.NOTE


def _page(page_no: int, text: str) -> PageModel:
    """Страница с одним body-блоком (аналог legacy _make_page)."""

    return PageModel(
        page=page_no,
        content=EmbeddedContent(valid=True, pagetext=text, pagehtml=""),
        elements=[BookElement(kind=_BODY_KIND, text=text, page=page_no)],
    )


class TestIsProbableHeading:
    def test_all_caps_short(self) -> None:
        assert is_probable_heading("ГЛАВА ПЕРВАЯ")

    def test_mixed_case_fails(self) -> None:
        assert not is_probable_heading("Обычный текст предложения")

    def test_too_short(self) -> None:
        assert not is_probable_heading("АБВ")

    def test_too_long(self) -> None:
        assert not is_probable_heading("СЛОВО " * 13)

    def test_sentence_with_period(self) -> None:
        assert not is_probable_heading("ОЧЕНЬ ДЛИННОЕ ПРЕДЛОЖЕНИЕ, КОТОРОЕ ЗАКАНЧИВАЕТСЯ ТОЧКОЙ.")


class TestIsProbableAuthorLine:
    def test_author_with_initials(self) -> None:
        assert is_probable_author_line("А.П. Иванов")

    def test_plain_sentence(self) -> None:
        assert not is_probable_author_line("Это обычный текст")

    def test_with_digits(self) -> None:
        assert not is_probable_author_line("А.П. Иванов 1994")

    def test_hyphenated_surname(self) -> None:
        # Регресс: дефисная фамилия должна распознаваться (legacy → False).
        assert is_probable_author_line("Боровик-Романов А. С.")

    def test_hyphenated_surname_initials_first(self) -> None:
        assert is_probable_author_line("А. С. Боровик-Романов")


class TestShouldStartChapter:
    def test_strict_early_page_no_chapter(self) -> None:
        assert not should_start_chapter("ВВЕДЕНИЕ", 2, "strict")

    def test_strict_late_page_short_heading_no_chapter(self) -> None:
        assert not should_start_chapter("ИТОГ", 10, "strict")

    def test_normal_mode_any_heading(self) -> None:
        assert should_start_chapter("ИТОГ", 2, "normal")

    def test_strict_valid_heading(self) -> None:
        assert should_start_chapter("ГЛАВА ПЕРВАЯ НАЧАЛО", 10, "strict")


class TestExtractBlocks:
    def test_extracts_paragraph(self) -> None:
        blocks = extract_blocks('<p class="text">Текст абзаца</p>', page=0)
        assert len(blocks) == 1
        assert blocks[0].text == "Текст абзаца"
        assert blocks[0].kind is _BODY_KIND
        assert blocks[0].page == 0

    def test_skips_page_no(self) -> None:
        blocks = extract_blocks('<p class="page-no">5</p><p class="text">Текст</p>', page=3)
        texts = [b.text for b in blocks]
        assert "5" not in texts
        assert "Текст" in texts

    def test_footnote_kind(self) -> None:
        blocks = extract_blocks('<p class="ftn">сноска</p>', page=1)
        assert blocks[0].kind is ElementKind.FOOTNOTE

    def test_image_caption_kind(self) -> None:
        blocks = extract_blocks('<p class="img">Рис. 1. Подпись</p>', page=1)
        assert blocks[0].kind is ElementKind.CAPTION

    def test_comp_draft_container_not_collapsed(self) -> None:
        # <div class="comp-draft"> — контейнер; должны извлекаться вложенные <p>
        # по отдельности, а не схлопываться в один блок.
        html = '<div class="comp-draft"><p class="text">Первый</p><p class="ftn">Сноска</p></div>'
        blocks = extract_blocks(html, page=2)
        assert len(blocks) == 2
        assert blocks[0].kind is _BODY_KIND
        assert blocks[1].kind is ElementKind.FOOTNOTE

    def test_fallback_to_text(self) -> None:
        blocks = extract_blocks("", page=0, fallback_text="Запасной текст")
        assert blocks[0].text == "Запасной текст"

    def test_ref_footnote_link_at_model_level(self) -> None:
        # Связь ref↔footnote выражается полями модели BookElement.
        ref = BookElement(kind=_BODY_KIND, text="текст[1]", page=0, ref="fn1")
        footnote = BookElement(
            kind=ElementKind.FOOTNOTE, text="1. примечание", page=0, anchor="fn1"
        )
        assert ref.ref == footnote.anchor


class TestPageToModel:
    def test_normalizes_and_extracts(self) -> None:
        content = EmbeddedContent(
            valid=True,
            pagetext="запас",
            pagehtml='<p class="text">Абзац</p><p class="page">7</p>',
        )
        page = page_to_model(0, content)
        # page-номер исключён, остаётся только содержательный абзац.
        assert [b.text for b in page.elements] == ["Абзац"]


class TestSplitIntoChapters:
    def test_no_headings_single_chapter(self) -> None:
        chapters = split_into_chapters([_page(1, "Текст без заголовков.")], mode="normal")
        assert len(chapters) == 1

    def test_heading_splits_chapters(self) -> None:
        pages = [
            _page(1, "Предисловие"),
            _page(10, "ПЕРВАЯ БОЛЬШАЯ ГЛАВА КНИГИ"),
            _page(11, "Текст главы"),
        ]
        chapters = split_into_chapters(pages, mode="strict")
        titles = [ch.title for ch in chapters]
        assert "ПЕРВАЯ БОЛЬШАЯ ГЛАВА КНИГИ" in titles

    def test_author_line_becomes_subtitle(self) -> None:
        page = PageModel(
            page=10,
            content=EmbeddedContent(pagetext=""),
            elements=[
                BookElement(kind=_BODY_KIND, text="Боровик-Романов А. С.", page=10),
                BookElement(kind=_BODY_KIND, text="ГЛАВА ПЕРВАЯ НАЧАЛО", page=10),
                BookElement(kind=_BODY_KIND, text="Тело главы", page=10),
            ],
        )
        chapters = split_into_chapters([page], mode="strict")
        chapter = next(ch for ch in chapters if ch.title == "ГЛАВА ПЕРВАЯ НАЧАЛО")
        assert chapter.author == "Боровик-Романов А. С."

    def test_pages_populated_no_page_lost(self) -> None:
        # Регресс (blocking #3): split_into_chapters обязан заполнять
        # chapter.pages. Сумма страниц по всем главам = числу входных страниц,
        # ни одна не теряется и не дублируется.
        pages = [
            _page(1, "Предисловие к изданию"),
            _page(10, "ПЕРВАЯ БОЛЬШАЯ ГЛАВА КНИГИ"),
            _page(11, "Текст первой главы продолжается"),
            _page(20, "ВТОРАЯ БОЛЬШАЯ ГЛАВА КНИГИ"),
            _page(21, "Текст второй главы"),
        ]
        chapters = split_into_chapters(pages, mode="strict")

        collected = [p.page for ch in chapters for p in ch.pages]
        assert sorted(collected) == [1, 10, 11, 20, 21]
        assert len(collected) == len(pages)  # без дублей и пропусков

    def test_chapter_with_elements_has_nonempty_pages(self) -> None:
        # Каждая глава, у которой есть elements, должна иметь непустой pages.
        pages = [
            _page(10, "ПЕРВАЯ БОЛЬШАЯ ГЛАВА КНИГИ"),
            _page(11, "Содержательный текст главы"),
        ]
        chapters = split_into_chapters(pages, mode="strict")
        for ch in chapters:
            if ch.elements:
                assert ch.pages, f"глава {ch.title!r} имеет elements, но пустой pages"

    def test_multi_block_page_not_duplicated_in_pages(self) -> None:
        # Страница с несколькими блоками должна попасть в chapter.pages один раз.
        page = PageModel(
            page=15,
            content=EmbeddedContent(pagetext=""),
            elements=[
                BookElement(kind=_BODY_KIND, text="Первый абзац страницы", page=15),
                BookElement(kind=_BODY_KIND, text="Второй абзац страницы", page=15),
                BookElement(kind=_BODY_KIND, text="Третий абзац страницы", page=15),
            ],
        )
        chapters = split_into_chapters([page], mode="strict")
        assert len(chapters) == 1
        assert [p.page for p in chapters[0].pages] == [15]

    def test_front_matter_with_pages_but_no_heading_merged(self) -> None:
        # Регресс (blocking #2): front-matter с реальными pages, но без
        # «настоящего» заголовка (только body-текст) сливается, как только
        # появляется содержательная глава. Сам front-matter без elements не
        # должен оставаться отдельной главой, но его страницы не теряются —
        # они остаются в потоке (front-matter тут несёт текст → elements есть).
        pages = [
            _page(1, "Вступительный текст без заголовка"),
            _page(10, "ПЕРВАЯ БОЛЬШАЯ ГЛАВА КНИГИ"),
            _page(11, "Тело главы"),
        ]
        chapters = split_into_chapters(pages, mode="strict")
        # Front-matter здесь содержит elements (вступительный текст), поэтому
        # сохраняется как отдельная глава с непустыми pages.
        front = chapters[0]
        assert front.title == "Front Matter"
        assert front.elements
        assert [p.page for p in front.pages] == [1]

    def test_empty_front_matter_dropped_when_content_follows(self) -> None:
        # Front-matter без значимых блоков (страница лишь с номером page-no →
        # пустой elements) отбрасывается при наличии содержательной главы.
        pages = [
            PageModel(
                page=1,
                content=EmbeddedContent(pagetext=""),
                elements=[BookElement(kind=_BODY_KIND, text="   ", page=1)],
            ),
            _page(10, "ПЕРВАЯ БОЛЬШАЯ ГЛАВА КНИГИ"),
            _page(11, "Тело главы"),
        ]
        chapters = split_into_chapters(pages, mode="strict")
        assert all(ch.title != "Front Matter" for ch in chapters)
        assert chapters[0].title == "ПЕРВАЯ БОЛЬШАЯ ГЛАВА КНИГИ"


class TestSplitByToc:
    def test_splits_on_toc_page_boundaries(self) -> None:
        pages = [_page(i, f"текст {i}") for i in range(10)]
        toc = [
            TocEntry(title="Начало", author=None, page=0, level=0),
            TocEntry(title="Глава 1", author=None, page=5, print_page="4", level=0),
        ]
        chapters = split_by_toc(pages, toc)
        assert [ch.title for ch in chapters] == ["Начало", "Глава 1"]
        assert 0 in [p.page for p in chapters[0].pages]
        assert 5 in [p.page for p in chapters[1].pages]
        assert 5 not in [p.page for p in chapters[0].pages]

    def test_section_header_same_page_is_divider(self) -> None:
        pages = [_page(5, "Содержимое"), _page(6, "Продолжение")]
        toc = [
            TocEntry(title="Раздел", author=None, page=5, print_page="4", level=0),
            TocEntry(title="Статья", author="Автор", page=5, print_page="4", level=1),
            TocEntry(title="Следующая", author=None, page=6, print_page="5", level=1),
        ]
        chapters = split_by_toc(pages, toc)
        assert len(chapters) == 3
        assert chapters[0].title == "Раздел"
        assert chapters[0].pages == []
        assert chapters[1].title == "Статья"
        assert 5 in [p.page for p in chapters[1].pages]

    def test_author_as_subtitle(self) -> None:
        pages = [_page(0, "текст")]
        toc = [TocEntry(title="Эссе", author="Иванов И. И.", page=0, print_page="1", level=1)]
        chapters = split_by_toc(pages, toc)
        assert chapters[0].author == "Иванов И. И."

    def test_empty_toc_returns_empty(self) -> None:
        assert split_by_toc([_page(0, "текст")], []) == []

    def test_front_matter_pages_before_first_toc_not_lost(self) -> None:
        # Регресс (blocking): страницы с номером меньше toc[0].page терялись.
        # Теперь для них создаётся front-matter глава, ни одна страница не пропадает.
        pages = [_page(i, f"текст {i}") for i in range(6)]
        toc = [TocEntry(title="Глава 1", author=None, page=3, level=0)]
        chapters = split_by_toc(pages, toc)
        all_pages = {p.page for ch in chapters for p in ch.pages}
        assert all_pages == {0, 1, 2, 3, 4, 5}
        assert chapters[0].title == "Front Matter"
        assert {p.page for p in chapters[0].pages} == {0, 1, 2}

    def test_no_front_matter_when_toc_starts_at_zero(self) -> None:
        pages = [_page(i, f"текст {i}") for i in range(4)]
        toc = [TocEntry(title="Глава 1", author=None, page=0, level=0)]
        chapters = split_by_toc(pages, toc)
        assert chapters[0].title == "Глава 1"
        assert all(ch.title != "Front Matter" for ch in chapters)


class TestParseEmbeddedContent:
    def test_dict_passthrough(self) -> None:
        result = parse_embedded_content({"valid": True, "pagetext": "x", "pagehtml": ""})
        assert result.pagetext == "x"
        assert result.valid is True

    def test_json_string(self) -> None:
        result = parse_embedded_content('{"valid": true, "pagetext": "hello", "pagehtml": ""}')
        assert result.pagetext == "hello"

    def test_invalid_string(self) -> None:
        result = parse_embedded_content("not json")
        assert result.valid is False
        assert result.pagetext == "not json"

    def test_none(self) -> None:
        result = parse_embedded_content(None)
        assert result.valid is True
        assert result.pagetext == ""

    def test_non_object_json(self) -> None:
        # Валидный JSON, но не объект → невалидное содержимое.
        result = parse_embedded_content("[1, 2, 3]")
        assert result.valid is False


class TestStructureAnalyzer:
    def test_analyze_with_toc(self) -> None:
        pages = [_page(i, f"текст {i}") for i in range(4)]
        toc = [TocEntry(title="Глава 1", author=None, page=0, level=0)]
        doc = StructureAnalyzer().analyze(pages, toc)
        assert doc.chapters[0].title == "Глава 1"

    def test_analyze_without_toc_falls_back_to_heuristic(self) -> None:
        pages = [_page(1, "Просто текст без заголовков")]
        doc = StructureAnalyzer().analyze(pages, [])
        assert len(doc.chapters) == 1
