import html as html_mod
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

BASE_URL = "https://elib.biblioatom.ru"
RPC_URL = f"{BASE_URL}/rpc/bookviewer/cp/"
DEFAULT_TIMEOUT = 20
DEFAULT_RETRIES = 3
DEFAULT_DELAY_MS = 300
FALLBACK_MAX_PAGE = 545

_HEADERS = {"User-Agent": "biblioatom-cli/0.1"}


class _BookPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.max_data_rel = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        attr_dict = dict(attrs)
        rel = attr_dict.get("data-rel")
        if rel is not None:
            try:
                val = int(rel)
                if val > self.max_data_rel:
                    self.max_data_rel = val
            except (ValueError, TypeError):
                pass

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def fetch_book_meta(book_id, timeout=DEFAULT_TIMEOUT):
    """Return (title, max_page) for a book. Falls back gracefully on any error."""
    url = f"{BASE_URL}/text/{urllib.parse.quote(book_id, safe='')}/"
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            page_html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return book_id, FALLBACK_MAX_PAGE

    parser = _BookPageParser()
    parser.feed(page_html)

    title = re.sub(r"\s*/\s*Просмотр.*$", "", parser.title.strip(), flags=re.I).strip()
    if not title:
        title = book_id

    max_page = parser.max_data_rel if parser.max_data_rel > 0 else FALLBACK_MAX_PAGE
    return title, max_page


def fetch_page(book_id, page, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES):
    """Fetch one page via RPC. Returns {"ok": True, "page": N, "content": dict}
    or {"ok": False, "page": N, "error": str}."""
    url = (
        f"{RPC_URL}"
        f"?url={urllib.parse.quote(book_id, safe='')}"
        f"&page={urllib.parse.quote(str(page), safe='')}"
    )
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            try:
                content = json.loads(raw)
            except json.JSONDecodeError:
                content = {"valid": False, "pagetext": raw, "pagehtml": ""}
            return {"ok": True, "page": page, "content": content}
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.25 * attempt)

    return {"ok": False, "page": page, "error": str(last_error or "unknown error")}


def _clean_toc_text(s):
    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s)
    s = s.replace("­", "")   # soft hyphen
    s = s.replace(" ", " ")  # non-breaking space
    return re.sub(r"\s+", " ", s).strip()


def _parse_toc_html(toc_html):
    """Parse <aside data-type="tree-box-contents"> into a list of TOC entries.

    Each entry: {title, author, page, print_page, level}
      page        — physical page index (data-goto-page, 0-based)
      print_page  — printed page number in the book, or None
      level       — nesting depth (0 = top level)
    """
    entries = []
    for a_m in re.finditer(
        r'<a\s[^>]*data-goto-page="(\d+)"[^>]*data-level="(\d+)"[^>]*>',
        toc_html,
    ):
        page = int(a_m.group(1))
        level = int(a_m.group(2))
        body_start = a_m.end()
        close_m = re.search(r"</a>", toc_html[body_start:])
        if not close_m:
            continue
        body = toc_html[body_start : body_start + close_m.start()]

        pageno_m = re.search(
            r'<span[^>]*class="[^"]*info pageno[^"]*"[^>]*>\s*(\d+)\s*</span>', body, re.S
        )
        print_page = int(pageno_m.group(1)) if pageno_m else None

        author_m = re.search(
            r'<span[^>]*class="[^"]*info author[^"]*"[^>]*>(.*?)</span>', body, re.S
        )
        author = _clean_toc_text(author_m.group(1)) if author_m else ""

        # Title: body minus <ins> block, minus author span, minus pageno span
        title_body = re.sub(r"<ins>.*?</ins>", "", body, flags=re.S)
        if author_m:
            title_body = title_body.replace(author_m.group(0), "")
        if pageno_m:
            title_body = title_body.replace(pageno_m.group(0), "")
        title = _clean_toc_text(title_body)

        if title:
            entries.append(
                {
                    "title": title,
                    "author": author,
                    "page": page,
                    "print_page": print_page,
                    "level": level,
                }
            )
    return entries


def fetch_toc(book_id, timeout=DEFAULT_TIMEOUT):
    """Fetch the table of contents for a book.

    Returns list of {title, author, page, print_page, level}, or [] on failure.
    page is the physical 0-based page index; print_page is the book's printed page number.
    """
    url = f"{BASE_URL}/text/{urllib.parse.quote(book_id, safe='')}/p0/"
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            page_html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []

    marker = '<aside data-type="tree-box-contents"'
    if marker not in page_html:
        return []

    start = page_html.index(marker)
    end = page_html.index("</aside>", start) + len("</aside>")
    return _parse_toc_html(page_html[start:end])


def fetch_image(book_id, page_no, timeout=DEFAULT_TIMEOUT):
    """Fetch a page scan as JPEG bytes. Returns bytes or None on error."""
    url = (
        f"{BASE_URL}/data/{urllib.parse.quote(book_id, safe='')}"
        f"/jpg/{page_no:04d}.jpg"
    )
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def download_book(
    book_id,
    from_page,
    to_page,
    delay_ms=DEFAULT_DELAY_MS,
    progress_cb=None,
    timeout=DEFAULT_TIMEOUT,
    retries=DEFAULT_RETRIES,
):
    """Download pages from_page..to_page inclusive.

    progress_cb(done, total, page_no) is called before each fetch.
    Returns list of item dicts compatible with convert.build_book_models.
    """
    items = []
    total = to_page - from_page + 1

    for i, page in enumerate(range(from_page, to_page + 1)):
        if progress_cb:
            progress_cb(i, total, page)

        result = fetch_page(book_id, page, timeout=timeout, retries=retries)
        if result["ok"]:
            items.append({"page": page, "content": result["content"]})
        else:
            items.append({"page": page, "content": None, "error": result["error"]})

        if delay_ms > 0 and page < to_page:
            time.sleep(delay_ms / 1000.0)

    return items
