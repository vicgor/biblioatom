import tempfile
import unittest
from pathlib import Path

from biblioatom.convert import (
    normalize_text,
    extract_blocks_from_html,
    page_to_model,
    slugify,
    split_into_chapters,
    split_chapters_by_toc,
    find_photo_pages,
    build_book,
    build_book_models,
    build_txt,
    output_stem,
    parse_embedded_content,
)


class TestNormalizeText(unittest.TestCase):
    def test_strips_leading_page_number(self):
        self.assertEqual(normalize_text("42\n\nТекст"), "Текст")

    def test_collapses_multiple_newlines(self):
        result = normalize_text("Строка\n\n\n\nДругая")
        self.assertNotIn("\n\n\n", result)

    def test_replaces_nbsp(self):
        self.assertEqual(normalize_text("слово\u00a0слово"), "слово слово")

    def test_empty_input(self):
        self.assertEqual(normalize_text(""), "")
        self.assertEqual(normalize_text(None), "")


class TestExtractBlocks(unittest.TestCase):
    def test_basic_paragraph(self):
        html = '<p class="text">Пример</p>'
        blocks = extract_blocks_from_html(html)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "p")
        self.assertEqual(blocks[0]["text"], "Пример")

    def test_footnote_class(self):
        html = '<p class="ftn">Сноска</p>'
        blocks = extract_blocks_from_html(html)
        self.assertEqual(blocks[0]["type"], "footnote")

    def test_image_caption_class(self):
        html = '<p class="img">Подпись</p>'
        blocks = extract_blocks_from_html(html)
        self.assertEqual(blocks[0]["type"], "image-caption")

    def test_page_no_skipped(self):
        html = '<p class="page-no">42</p><p class="text">Текст</p>'
        blocks = extract_blocks_from_html(html)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["text"], "Текст")

    def test_fallback_text_used_when_no_html(self):
        blocks = extract_blocks_from_html("", fallback_text="Параграф\n\nЕщё")
        self.assertEqual(len(blocks), 2)

    def test_div_not_matched(self):
        html = '<div class="comp-draft"><p class="text">Внутри</p></div>'
        blocks = extract_blocks_from_html(html)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "p")


class TestPageToModel(unittest.TestCase):
    def _make_item(self, pagehtml="", pagetext="", page=1):
        return {"page": page, "content": {"valid": True, "pagetext": pagetext, "pagehtml": pagehtml}}

    def test_page_number_preserved(self):
        model = page_to_model(self._make_item(page=7))
        self.assertEqual(model["page"], 7)

    def test_blocks_extracted(self):
        model = page_to_model(self._make_item(pagehtml='<p class="text">Текст</p>'))
        self.assertEqual(model["blocks"][0]["text"], "Текст")

    def test_html_page_no_extracted(self):
        model = page_to_model(self._make_item(
            pagehtml='<p class="page-no">42</p><p class="text">Hello</p>'
        ))
        self.assertEqual(model["html_page_no"], 42)

    def test_html_page_no_none_when_absent(self):
        model = page_to_model(self._make_item(
            pagehtml='<p class="text">Hello</p>'
        ))
        self.assertIsNone(model["html_page_no"])

    def test_embedded_content_as_json_string(self):
        import json
        item = {"page": 3, "content": json.dumps({"valid": True, "pagetext": "Строка", "pagehtml": ""})}
        model = page_to_model(item)
        self.assertEqual(model["pagetext"], "Строка")


class TestSlugify(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Пример Текст"), "пример_текст")

    def test_empty(self):
        self.assertEqual(slugify(""), "book")

    def test_strips_punctuation(self):
        result = slugify("Текст, пример!")
        self.assertNotIn(",", result)
        self.assertNotIn("!", result)


class TestSplitChapters(unittest.TestCase):
    def _make_pages(self, specs):
        pages = []
        for page_no, blocks in specs:
            pages.append({
                "page": page_no,
                "html_page_no": None,
                "valid": True,
                "pagetext": "",
                "pagehtml": "",
                "blocks": [{"type": "p", "text": b} for b in blocks],
            })
        return pages

    def test_single_chapter(self):
        pages = self._make_pages([(1, ["Текст"]), (2, ["Ещё"])])
        chapters = split_into_chapters(pages, mode="normal")
        self.assertGreaterEqual(len(chapters), 1)

    def test_toc_split(self):
        pages = self._make_pages([
            (5, ["Глава 1", "Текст главы"]),
            (10, ["Глава 2", "Текст второй"]),
        ])
        toc = [
            {"title": "Глава 1", "author": "", "page": 5, "print_page": 5, "level": 0},
            {"title": "Глава 2", "author": "", "page": 10, "print_page": 10, "level": 0},
        ]
        chapters = split_chapters_by_toc(pages, toc)
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0]["title"], "Глава 1")
        self.assertEqual(chapters[1]["title"], "Глава 2")

    def test_empty_toc_returns_empty(self):
        pages = self._make_pages([(1, ["Текст"])])
        self.assertEqual(split_chapters_by_toc(pages, []), [])


class TestFindPhotoPages(unittest.TestCase):
    def _src_with_image_page(self, rpc_page, html_page_no, caption):
        return {
            "items": [{
                "page": rpc_page,
                "content": {
                    "valid": True,
                    "pagetext": "",
                    "pagehtml": (
                        f'<p class="page-no">{html_page_no}</p>'
                        f'<p class="img">{caption}</p>'
                    ) if html_page_no is not None else f'<p class="img">{caption}</p>',
                },
            }]
        }

    def test_returns_photo_pages(self):
        src = self._src_with_image_page(rpc_page=10, html_page_no=9, caption="Портрет")
        result = find_photo_pages(src)
        self.assertEqual(len(result), 1)
        rpc, cdn, cap = result[0]
        self.assertEqual(rpc, 10)
        self.assertEqual(cdn, 9)
        self.assertEqual(cap, "Портрет")

    def test_fallback_cdn_when_no_html_page_no(self):
        src = self._src_with_image_page(rpc_page=10, html_page_no=None, caption="Фото")
        result = find_photo_pages(src)
        rpc, cdn, _ = result[0]
        self.assertEqual(cdn, rpc - 1)

    def test_no_image_pages(self):
        src = {"items": [{"page": 1, "content": {"valid": True, "pagetext": "Текст", "pagehtml": ""}}]}
        self.assertEqual(find_photo_pages(src), [])


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

    def test_build_book_returns_result(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            result = build_book(self.src, out_dir=Path(tmpdir))
            self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
