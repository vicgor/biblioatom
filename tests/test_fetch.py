import io
import json
import unittest
from unittest.mock import MagicMock, patch

from biblioatom.fetch import fetch_page, fetch_book_meta, fetch_toc, download_book, FALLBACK_MAX_PAGE


def _mock_response(body: str, status: int = 200):
    resp = MagicMock()
    resp.read.return_value = body.encode("utf-8")
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.status = status
    return resp


class TestFetchPage(unittest.TestCase):
    def test_success(self):
        payload = json.dumps({"valid": True, "pagetext": "Текст", "pagehtml": ""})
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = fetch_page("test_book", 0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["page"], 0)
        self.assertEqual(result["content"]["pagetext"], "Текст")

    def test_invalid_json_response(self):
        with patch("urllib.request.urlopen", return_value=_mock_response("not json")):
            result = fetch_page("test_book", 5)
        self.assertTrue(result["ok"])
        self.assertFalse(result["content"]["valid"])

    def test_network_error_retries_and_fails(self):
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")), \
             patch("time.sleep"):
            result = fetch_page("test_book", 1, retries=2)
        self.assertFalse(result["ok"])
        self.assertIn("connection refused", result["error"])

    def test_retries_on_transient_error(self):
        payload = json.dumps({"valid": True, "pagetext": "OK", "pagehtml": ""})
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OSError("transient")
            return _mock_response(payload)

        with patch("urllib.request.urlopen", side_effect=side_effect), \
             patch("time.sleep"):
            result = fetch_page("test_book", 3, retries=3)
        self.assertTrue(result["ok"])
        self.assertEqual(call_count, 2)


class TestFetchBookMeta(unittest.TestCase):
    def test_extracts_title_and_max_page(self):
        html = (
            "<html><head><title>Капица / Просмотр</title></head>"
            '<body>'
            '<div class="page-gfx" data-rel="42"></div>'
            '<div class="page-gfx" data-rel="99"></div>'
            "</body></html>"
        )
        with patch("urllib.request.urlopen", return_value=_mock_response(html)):
            title, max_page = fetch_book_meta("kapitsa_1994")
        self.assertEqual(title, "Капица")
        self.assertEqual(max_page, 99)

    def test_fallback_on_network_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            title, max_page = fetch_book_meta("some_book")
        self.assertEqual(title, "some_book")
        self.assertEqual(max_page, FALLBACK_MAX_PAGE)

    def test_fallback_when_no_data_rel(self):
        html = "<html><head><title>Книга без навигации</title></head><body></body></html>"
        with patch("urllib.request.urlopen", return_value=_mock_response(html)):
            title, max_page = fetch_book_meta("norel_book")
        self.assertEqual(title, "Книга без навигации")
        self.assertEqual(max_page, FALLBACK_MAX_PAGE)


class TestFetchToc(unittest.TestCase):
    _TOC_HTML = """
<aside data-type="tree-box-contents" data-max-level="1" data-parent="root">
<a class="tree-level-0" data-goto-page="0" data-from="0" data-ord="0" data-anchor="$p0" data-level="0">
<ins><span></span></ins>
<span class="desc"><span>Обложка</span></span>
</a>
<a class="tree-level-0" data-goto-page="6" data-from="6" data-ord="1" data-anchor="$p5" data-level="0">
<ins><span></span></ins>
<span class="desc"><span>От&nbsp;состави&shy;те&shy;лей</span></span>
<span class="info pageno">5</span>
</a>
<a class="tree-level-1" data-goto-page="8" data-from="8" data-ord="2" data-anchor="$p7" data-level="1">
<ins><span></span></ins>
<span class="desc">
<span class="info author">Боровик-Романов&nbsp;А.&nbsp;С.</span>
<span>Жизнь и&nbsp;деятель&shy;ность</span>
</span>
<span class="info pageno">7</span>
</a>
</aside>"""

    def test_parses_entries(self):
        html = '<html><body>' + self._TOC_HTML + '</body></html>'
        with patch("urllib.request.urlopen", return_value=_mock_response(html)):
            toc = fetch_toc("test_book")
        self.assertEqual(len(toc), 3)

    def test_entry_fields(self):
        html = '<html><body>' + self._TOC_HTML + '</body></html>'
        with patch("urllib.request.urlopen", return_value=_mock_response(html)):
            toc = fetch_toc("test_book")
        cover = toc[0]
        self.assertEqual(cover["title"], "Обложка")
        self.assertEqual(cover["page"], 0)
        self.assertIsNone(cover["print_page"])
        self.assertEqual(cover["level"], 0)

    def test_print_page_and_author(self):
        html = '<html><body>' + self._TOC_HTML + '</body></html>'
        with patch("urllib.request.urlopen", return_value=_mock_response(html)):
            toc = fetch_toc("test_book")
        entry = toc[2]
        self.assertEqual(entry["print_page"], 7)
        self.assertIn("Боровик-Романов", entry["author"])
        self.assertEqual(entry["level"], 1)

    def test_soft_hyphen_stripped(self):
        html = '<html><body>' + self._TOC_HTML + '</body></html>'
        with patch("urllib.request.urlopen", return_value=_mock_response(html)):
            toc = fetch_toc("test_book")
        self.assertEqual(toc[1]["title"], "От составителей")

    def test_network_error_returns_empty(self):
        with patch("urllib.request.urlopen", side_effect=OSError("fail")):
            toc = fetch_toc("test_book")
        self.assertEqual(toc, [])

    def test_missing_aside_returns_empty(self):
        with patch("urllib.request.urlopen", return_value=_mock_response("<html></html>")):
            toc = fetch_toc("test_book")
        self.assertEqual(toc, [])


class TestDownloadBook(unittest.TestCase):
    def test_collects_all_pages(self):
        def mock_urlopen(*args, **kwargs):
            url = str(args[0].full_url if hasattr(args[0], "full_url") else args[0])
            page = int(url.split("page=")[-1])
            payload = json.dumps({"valid": True, "pagetext": f"стр {page}", "pagehtml": ""})
            return _mock_response(payload)

        with patch("urllib.request.urlopen", side_effect=mock_urlopen), \
             patch("time.sleep"):
            items = download_book("book", 0, 2, delay_ms=10)

        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["page"], 0)
        self.assertEqual(items[2]["page"], 2)

    def test_error_page_recorded(self):
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")), \
             patch("time.sleep"):
            items = download_book("book", 0, 0, delay_ms=0, retries=1)
        self.assertEqual(len(items), 1)
        self.assertIn("error", items[0])

    def test_progress_callback_called(self):
        payload = json.dumps({"valid": True, "pagetext": "x", "pagehtml": ""})
        calls = []

        with patch("urllib.request.urlopen", return_value=_mock_response(payload)), \
             patch("time.sleep"):
            download_book("book", 0, 1, delay_ms=0, progress_cb=lambda d, t, p: calls.append(p))

        self.assertEqual(calls, [0, 1])


if __name__ == "__main__":
    unittest.main()
