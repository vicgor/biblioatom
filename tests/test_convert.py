import json
import tempfile
import unittest
from pathlib import Path

from biblioatom.convert import (
    split_chapters_by_toc,
    normalize_text,
    is_probable_heading,
    is_probable_author_line,
    should_start_chapter,
    split_into_chapters,
    build_book_models,
    build_txt,
    output_stem,
    slugify,
    parse_embedded_content,
    extract_blocks_from_html,
    build_book,
)


class TestNormalizeText(unittest.TestCase):
    def test_strips_leading_page_number(self):
        self.assertEqual(normalize_text("42\nТекст"), "Текст")

    def test_collapses_extra_newlines(self):
        result = normalize_text("a\n\n\n\nb")
        self.assertEqual(result, "a\n\nb")

    def test_replaces_nbsp(self):
        result = normalize_text("a b")
        self.assertNotIn(" ", result)

    def test_empty(self):
        self.assertEqual(normalize_text(""), "")
        self.assertEqual(normalize_text(None), "")


class TestIsProbableHeading(unittest.TestCase):
    def test_all_caps_short(self):
        self.assertTrue(is_probable_heading("ГЛАВА ПЕРВАЯ"))

    def test_mixed_case_fails(self):
        self.assertFalse(is_probable_heading("Обычный текст предложения"))

    def test_too_short(self):
        self.assertFalse(is_probable_heading("АБВ"))

    def test_too_long(self):
        self.assertFalse(is_probable_heading("СЛОВО " * 13))

    def test_sentence_with_period(self):
        self.assertFalse(is_probable_heading("ОЧЕНЬ ДЛИННОЕ ПРЕДЛОЖЕНИЕ, КОТОРОЕ ЗАКАНЧИВАЕТСЯ ТОЧКОЙ."))


class TestIsProbableAuthorLine(unittest.TestCase):
    def test_author_with_initials(self):
        self.assertTrue(is_probable_author_line("А.П. Иванов"))

    def test_plain_sentence(self):
        self.assertFalse(is_probable_author_line("Это обычный текст"))

    def test_with_digits(self):
        self.assertFalse(is_probable_author_line("А.П. Иванов 1994"))


class TestShouldStartChapter(unittest.TestCase):
    def test_strict_early_page_no_chapter(self):
        self.assertFalse(should_start_chapter("ВВЕДЕНИЕ", 2, "strict"))

    def test_strict_late_page_short_heading_no_chapter(self):
        self.assertFalse(should_start_chapter("ИТОГ", 10, "strict"))

    def test_normal_mode_any_heading(self):
        self.assertTrue(should_start_chapter("ИТОГ", 2, "normal"))

    def test_strict_valid_heading(self):
        self.assertTrue(should_start_chapter("ГЛАВА ПЕРВАЯ НАЧАЛО", 10, "strict"))


class TestSplitIntoChapters(unittest.TestCase):
    def _make_page(self, page_no, text):
        return {
            "page": page_no,
            "valid": True,
            "pagetext": text,
            "pagehtml": "",
            "blocks": [{"type": "p", "text": text}],
        }

    def test_no_headings_single_chapter(self):
        pages = [self._make_page(1, "Текст без заголовков.")]
        chapters = split_into_chapters(pages, mode="normal")
        self.assertEqual(len(chapters), 1)

    def test_heading_splits_chapters(self):
        pages = [
            self._make_page(1, "Предисловие"),
            self._make_page(10, "ПЕРВАЯ БОЛЬШАЯ ГЛАВА КНИГИ"),
            self._make_page(11, "Текст главы"),
        ]
        chapters = split_into_chapters(pages, mode="strict")
        titles = [ch["title"] for ch in chapters]
        self.assertIn("ПЕРВАЯ БОЛЬШАЯ ГЛАВА КНИГИ", titles)


class TestSplitChaptersByToc(unittest.TestCase):
    def _make_page(self, page_no, text):
        return {
            "page": page_no,
            "valid": True,
            "pagetext": text,
            "pagehtml": "",
            "blocks": [{"type": "p", "text": text}],
        }

    def test_splits_on_toc_page_boundaries(self):
        pages = [self._make_page(i, f"текст {i}") for i in range(10)]
        toc = [
            {"title": "Начало", "author": "", "page": 0, "print_page": None, "level": 0},
            {"title": "Глава 1", "author": "", "page": 5, "print_page": 4, "level": 0},
        ]
        chapters = split_chapters_by_toc(pages, toc)
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0]["title"], "Начало")
        self.assertEqual(chapters[1]["title"], "Глава 1")
        self.assertIn(0, chapters[0]["pages"])
        self.assertIn(5, chapters[1]["pages"])
        self.assertNotIn(5, chapters[0]["pages"])

    def test_section_header_same_page_is_divider(self):
        # Section header and first subsection share the same page — header gets no content
        pages = [self._make_page(5, "Содержимое"), self._make_page(6, "Продолжение")]
        toc = [
            {"title": "Раздел", "author": "", "page": 5, "print_page": 4, "level": 0},
            {"title": "Статья", "author": "Автор", "page": 5, "print_page": 4, "level": 1},
            {"title": "Следующая", "author": "", "page": 6, "print_page": 5, "level": 1},
        ]
        chapters = split_chapters_by_toc(pages, toc)
        self.assertEqual(len(chapters), 3)
        # Divider has no pages/content
        self.assertEqual(chapters[0]["title"], "Раздел")
        self.assertEqual(chapters[0]["pages"], [])
        # First subsection owns page 5
        self.assertEqual(chapters[1]["title"], "Статья")
        self.assertIn(5, chapters[1]["pages"])

    def test_print_page_preserved(self):
        pages = [self._make_page(5, "содержимое")]
        toc = [{"title": "Гл.", "author": "", "page": 5, "print_page": 42, "level": 0}]
        chapters = split_chapters_by_toc(pages, toc)
        self.assertEqual(chapters[0]["print_page"], 42)

    def test_author_as_subtitle(self):
        pages = [self._make_page(0, "текст")]
        toc = [{"title": "Эссе", "author": "Иванов И. И.", "page": 0, "print_page": 1, "level": 1}]
        chapters = split_chapters_by_toc(pages, toc)
        self.assertEqual(chapters[0]["subtitle"], "Иванов И. И.")

    def test_empty_toc_returns_empty(self):
        pages = [self._make_page(0, "текст")]
        self.assertEqual(split_chapters_by_toc(pages, []), [])


class TestParseEmbeddedContent(unittest.TestCase):
    def test_dict_passthrough(self):
        d = {"valid": True, "pagetext": "x"}
        self.assertIs(parse_embedded_content(d), d)

    def test_json_string(self):
        s = '{"valid": true, "pagetext": "hello"}'
        self.assertEqual(parse_embedded_content(s)["pagetext"], "hello")

    def test_invalid_string(self):
        result = parse_embedded_content("not json")
        self.assertFalse(result.get("valid"))

    def test_none(self):
        self.assertEqual(parse_embedded_content(None), {})


class TestExtractBlocksFromHtml(unittest.TestCase):
    def test_extracts_paragraph(self):
        pagehtml = '<p class="text">Текст абзаца</p>'
        blocks = extract_blocks_from_html(pagehtml)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["text"], "Текст абзаца")

    def test_skips_page_no(self):
        pagehtml = '<p class="page-no">5</p><p class="text">Текст</p>'
        blocks = extract_blocks_from_html(pagehtml)
        texts = [b["text"] for b in blocks]
        self.assertNotIn("5", texts)
        self.assertIn("Текст", texts)

    def test_fallback_to_text(self):
        blocks = extract_blocks_from_html("", fallback_text="Запасной текст")
        self.assertEqual(blocks[0]["text"], "Запасной текст")


class TestOutputStem(unittest.TestCase):
    def test_basic(self):
        src = {"title": "Моя Книга", "book_id": "my_book", "page_range": [0, 100]}
        stem = output_stem(src)
        self.assertIn("моя", stem)
        self.assertIn("0-100", stem)

    def test_prefix(self):
        src = {"title": "Книга", "book_id": "book", "page_range": [0, 10]}
        stem = output_stem(src, prefix="test")
        self.assertTrue(stem.startswith("test"))


class TestBuildTxt(unittest.TestCase):
    def test_writes_file(self):
        src = {
            "title": "Тест",
            "book_id": "test",
            "source": "",
            "page_range": [0, 1],
            "generated_at": "",
            "items": [
                {"page": 0, "content": {"valid": True, "pagetext": "Страница ноль", "pagehtml": ""}},
                {"page": 1, "content": {"valid": True, "pagetext": "Страница один", "pagehtml": ""}},
            ],
        }
        pages = build_book_models(src)
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            path = Path(f.name)
        build_txt(src, pages, path)
        text = path.read_text(encoding="utf-8")
        self.assertIn("Страница ноль", text)
        self.assertIn("PAGE 0", text)
        path.unlink()


class TestBuildBook(unittest.TestCase):
    def setUp(self):
        self.src = {
            "title": "Тестовая Книга",
            "book_id": "test_book",
            "source": "https://example.com/",
            "page_range": [0, 2],
            "generated_at": "2026-01-01T00:00:00",
            "items": [
                {"page": i, "content": {"valid": True, "pagetext": f"Текст страницы {i}", "pagehtml": ""}}
                for i in range(3)
            ],
        }

    def test_json_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            written = build_book(self.src, ["json"], tmpdir)
            self.assertEqual(len(written), 1)
            self.assertTrue(written[0].exists())
            data = json.loads(written[0].read_text(encoding="utf-8"))
            self.assertEqual(data["book_id"], "test_book")

    def test_txt_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            written = build_book(self.src, ["txt"], tmpdir)
            text = written[0].read_text(encoding="utf-8")
            self.assertIn("Текст страницы 0", text)

    def test_html_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            written = build_book(self.src, ["html"], tmpdir)
            text = written[0].read_text(encoding="utf-8")
            self.assertIn("<!doctype html", text.lower())

    def test_epub_format(self):
        import zipfile as zf
        with tempfile.TemporaryDirectory() as tmpdir:
            written = build_book(self.src, ["epub"], tmpdir)
            self.assertTrue(zf.is_zipfile(written[0]))

    def test_unknown_format_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                build_book(self.src, ["pdf"], tmpdir)

    def test_multiple_formats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            written = build_book(self.src, ["json", "txt", "html"], tmpdir)
            self.assertEqual(len(written), 3)


if __name__ == "__main__":
    unittest.main()
