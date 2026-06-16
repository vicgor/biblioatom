"""Тесты use case структурного анализа (``core/analyze_structure.py``).

Без сети. Покрывают:

* happy-path с реальными сервисами: страницы строятся через
  ``page_to_model`` (parser.extract на embedded-фикстуре), затем
  ``StructureAnalyzer`` разбивает их по TOC — проверяется итоговый
  :class:`StructuredDocument` (число/состав глав, наличие pages/elements);
* проставление метаданных книги (title/book_id/source) на уровне use case;
* контракт неизменности входных списков ``pages``/``toc`` (use case их не
  мутирует);
* fallback на эвристику при отсутствии TOC;
* инъекцию анализатора через Protocol (fake-анализатор).
"""

from __future__ import annotations

from biblioatom.core.analyze_structure import analyze_structure
from biblioatom.models import (
    EmbeddedContent,
    PageModel,
    StructuredChapter,
    StructuredDocument,
    TocEntry,
)
from biblioatom.services.structure_analyzer import StructureAnalyzer, page_to_model


def _embedded(text: str) -> EmbeddedContent:
    """Embedded-содержимое с одним абзацем основного текста."""

    return EmbeddedContent(valid=True, pagetext=text, pagehtml="")


def _build_pages() -> list[PageModel]:
    """Фикстура страниц, построенных реальным parser.extract (page_to_model).

    Страница 0 — front-matter (до первой записи TOC), страницы 1 и 2 — тело двух
    глав. Заголовки даём прописными, чтобы эвристика заголовков тоже работала в
    fallback-кейсе без TOC.
    """

    return [
        page_to_model(0, _embedded("Титульный лист")),
        page_to_model(1, _embedded("ГЛАВА ПЕРВАЯ\n\nтекст первой главы")),
        page_to_model(2, _embedded("ГЛАВА ВТОРАЯ\n\nтекст второй главы")),
    ]


def _toc() -> list[TocEntry]:
    return [
        TocEntry(title="Глава первая", page=1, level=0),
        TocEntry(title="Глава вторая", page=2, level=0),
    ]


class _FakeAnalyzer:
    """Фейковый анализатор для проверки инъекции через Protocol."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def analyze(self, pages: list[PageModel], toc: list[TocEntry]) -> StructuredDocument:
        self.calls.append((len(pages), len(toc)))
        chapter = StructuredChapter(title="Stub", pages=list(pages))
        return StructuredDocument(title="", book_id="", chapters=[chapter])


class TestHappyPathWithRealServices:
    def test_builds_document_with_chapters_from_toc(self) -> None:
        pages = _build_pages()
        toc = _toc()
        document = analyze_structure(
            StructureAnalyzer(),
            pages,
            toc,
            title="Капица",
            book_id="kapitsa_1994",
            source="https://example/text/kapitsa_1994/",
        )

        assert isinstance(document, StructuredDocument)
        # Front-matter (стр. 0) + две главы из TOC.
        assert [ch.title for ch in document.chapters] == [
            "Front Matter",
            "Глава первая",
            "Глава вторая",
        ]
        # Каждая глава несёт свои страницы и непустые блоки.
        for chapter in document.chapters:
            assert chapter.pages
            assert chapter.elements

    def test_metadata_applied_by_use_case(self) -> None:
        document = analyze_structure(
            StructureAnalyzer(),
            _build_pages(),
            _toc(),
            title="Капица",
            book_id="kapitsa_1994",
            source="src",
        )
        assert document.title == "Капица"
        assert document.book_id == "kapitsa_1994"
        assert document.source == "src"
        # TOC проброшен в документ анализатором.
        assert len(document.toc) == 2

    def test_source_optional_defaults_to_none(self) -> None:
        document = analyze_structure(
            StructureAnalyzer(),
            _build_pages(),
            _toc(),
            title="T",
            book_id="b",
        )
        assert document.source is None

    def test_fallback_to_heuristic_without_toc(self) -> None:
        # Без TOC анализатор уходит в эвристическую разбивку по заголовкам.
        document = analyze_structure(
            StructureAnalyzer(chapter_mode="normal"),
            _build_pages(),
            [],
            title="T",
            book_id="b",
        )
        titles = [ch.title for ch in document.chapters]
        assert "ГЛАВА ПЕРВАЯ" in titles
        assert "ГЛАВА ВТОРАЯ" in titles


class TestInputImmutability:
    def test_input_lists_not_mutated(self) -> None:
        pages = _build_pages()
        toc = _toc()
        pages_before = list(pages)
        toc_before = list(toc)

        analyze_structure(
            StructureAnalyzer(),
            pages,
            toc,
            title="T",
            book_id="b",
        )

        # Use case не добавляет/не удаляет элементы входных списков.
        assert pages == pages_before
        assert toc == toc_before


class TestProtocolInjection:
    def test_uses_injected_analyzer(self) -> None:
        analyzer = _FakeAnalyzer()
        pages = _build_pages()
        toc = _toc()

        document = analyze_structure(
            analyzer,
            pages,
            toc,
            title="Капица",
            book_id="kapitsa_1994",
            source="src",
        )

        assert analyzer.calls == [(len(pages), len(toc))]
        assert [ch.title for ch in document.chapters] == ["Stub"]
        # Метаданные проставляет именно use case, а не анализатор.
        assert document.title == "Капица"
        assert document.book_id == "kapitsa_1994"
        assert document.source == "src"
