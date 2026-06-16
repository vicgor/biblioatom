import html
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS_FB2 = "http://www.gribuser.ru/xml/fictionbook/2.0"
NS_XLINK = "http://www.w3.org/1999/xlink"

ET.register_namespace("", NS_FB2)
ET.register_namespace("l", NS_XLINK)

FRONT_MATTER_TITLES = {
    "ОБЛОЖКА",
    "ФРОНТИСПИС",
    "РОССИЙСКАЯ АКАДЕМИЯ НАУК",
}

STRICT_MIN_PAGE_FOR_CHAPTER = 5
FORMATS = {"html", "fb2", "epub", "txt", "json"}


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

def normalize_text(text):
    s = text or ""
    s = s.replace("\r", "")
    s = s.replace(" ", " ")
    s = re.sub(r"^\d+\s*\n+", "", s.lstrip(), count=1).strip()
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def clean_pagehtml(pagehtml):
    s = pagehtml or ""
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = s.replace('class="page"', 'class="page-no"')
    s = s.replace("class='page'", "class='page-no'")
    return s.strip()


def strip_tags_preserve_text(s):
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p\s*>", "\n", s, flags=re.I)
    s = re.sub(r"</div\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


# ---------------------------------------------------------------------------
# Page model
# ---------------------------------------------------------------------------

def parse_embedded_content(raw):
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {"valid": False, "pagetext": str(raw), "pagehtml": ""}


def extract_blocks_from_html(pagehtml, fallback_text=""):
    blocks = []

    if pagehtml:
        # Match only <p> elements. <div class="comp-draft"> is a container that
        # wraps <p class="text|img|ftn"> — matching the div collapses all inner
        # paragraphs into one block, losing their individual class attributes.
        pattern = re.compile(r"<p(?P<attrs>[^>]*)>(?P<body>.*?)</p>", re.I | re.S)
        for m in pattern.finditer(pagehtml):
            attrs = m.group("attrs") or ""
            body = m.group("body") or ""

            class_match = re.search(r'class=["\']([^"\']+)["\']', attrs, re.I)
            classes = class_match.group(1).split() if class_match else []

            text = strip_tags_preserve_text(body)
            if not text:
                continue
            if "page-no" in classes:
                continue

            if "ftn" in classes:
                blocks.append({"type": "footnote", "text": text})
            elif "img" in classes:
                blocks.append({"type": "image-caption", "text": text})
            else:
                blocks.append({"type": "p", "text": text})

    if not blocks and fallback_text:
        cleaned = normalize_text(fallback_text)
        for part in re.split(r"\n\s*\n", cleaned):
            part = part.strip()
            if part:
                blocks.append({"type": "p", "text": part})

    return blocks


def page_to_model(item):
    embedded = parse_embedded_content(item.get("content") or "")
    page_num = item.get("page")
    pagetext = normalize_text(embedded.get("pagetext", ""))
    pagehtml = clean_pagehtml(embedded.get("pagehtml", ""))
    valid = embedded.get("valid", True)
    # Print page number shown on the page (<p class="page-no">N</p> after clean_pagehtml).
    # CDN JPG files are keyed by this number, not by the 0-based RPC page index.
    pno_m = re.search(r'<p[^>]*class="[^"]*page-no[^"]*"[^>]*>(\d+)</p>', pagehtml)
    html_page_no = int(pno_m.group(1)) if pno_m else None
    return {
        "page": page_num,
        "html_page_no": html_page_no,
        "valid": valid,
        "pagetext": pagetext,
        "pagehtml": pagehtml,
        "blocks": extract_blocks_from_html(pagehtml, pagetext),
    }


def build_book_models(src):
    return [page_to_model(item) for item in src.get("items", [])]


def find_photo_pages(src: dict) -> list[tuple[int, int, str]]:
    """Return (rpc_page, cdn_page, caption) for pages with image-caption blocks.

    cdn_page is the print page number shown in HTML (html_page_no), which is
    the key used by the CDN for JPG files. Falls back to rpc_page - 1 when
    html_page_no is absent.
    """
    result = []
    for pg in build_book_models(src):
        captions = [b["text"] for b in pg["blocks"] if b["type"] == "image-caption"]
        if captions:
            cdn = pg["html_page_no"] if pg.get("html_page_no") is not None else pg["page"] - 1
            result.append((pg["page"], cdn, captions[0]))
    return result


# ---------------------------------------------------------------------------
# Chapter detection
# ---------------------------------------------------------------------------

def slugify(text):
    s = text.strip().lower()
    s = re.sub(r"[^\w\s]", "", s, flags=re.U)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")[:120] or "book"


def output_stem(src, input_path=None, prefix=""):
    title = src.get("title", "") or (input_path.stem if input_path else "book")
    book_id = src.get("book_id", "")
    page_range = src.get("page_range", [])
    page_part = ""
    if isinstance(page_range, list) and len(page_range) == 2:
        page_part = f"{page_range[0]}-{page_range[1]}"
    parts = [prefix.strip(), slugify(title), slugify(book_id), page_part]
    return "_".join(p for p in parts if p)


def strip_heading_marks(text):
    return re.sub(r"[*]+$", "", text.strip()).strip()


def normalized_heading_key(text):
    t = strip_heading_marks(text).upper()
    t = t.replace("Ё", "Е")
    return re.sub(r"\s+", " ", t).strip()


def is_probable_heading(text):
    t = (text or "").strip()
    if not t:
        return False
    t = re.sub(r"\s+", " ", t)
    plain = t.strip("•*-—– ")
    if len(plain) < 4 or len(plain) > 120:
        return False
    if plain.endswith(".") and len(plain) > 40:
        return False
    letters = [ch for ch in plain if ch.isalpha()]
    if not letters:
        return False
    upper = sum(1 for ch in letters if ch.isupper())
    if upper / len(letters) < 0.6:
        return False
    if len(plain.split()) > 12:
        return False
    return True


def is_probable_author_line(text):
    t = (text or "").strip()
    if not t or len(t) > 80:
        return False
    if any(ch.isdigit() for ch in t):
        return False
    words = t.split()
    if len(words) < 2 or len(words) > 6:
        return False
    initials = sum(1 for w in words if re.match(r"^[А-ЯA-Z]\.?[А-ЯA-Z]?\.$", w))
    surname_like = any(re.match(r"^[А-ЯA-ZЁ][а-яa-zё-]+$", w) for w in words)
    return initials >= 1 and surname_like


def is_front_matter_heading(text):
    return normalized_heading_key(text) in FRONT_MATTER_TITLES


def should_start_chapter(text, page_no, mode):
    if not is_probable_heading(text):
        return False
    if mode == "normal":
        return True
    key = normalized_heading_key(text)
    if page_no < STRICT_MIN_PAGE_FOR_CHAPTER:
        return False
    if is_front_matter_heading(key):
        return False
    if len(key.split()) <= 2 and not key.endswith(":"):
        return False
    return True


def split_into_chapters(pages, mode="strict"):
    chapters = []
    current = {"title": "Front Matter", "subtitle": "", "pages": [], "content": []}
    pending_author = ""

    for pg in pages:
        i = 0
        while i < len(pg["blocks"]):
            block = pg["blocks"][i]
            btext = block["text"].strip()
            if not btext:
                i += 1
                continue

            if block["type"] == "p" and is_probable_author_line(btext):
                if i + 1 < len(pg["blocks"]):
                    nxt = pg["blocks"][i + 1]
                    if nxt["type"] == "p" and should_start_chapter(nxt["text"], pg["page"], mode):
                        pending_author = btext
                        i += 1
                        continue

            if block["type"] == "p" and should_start_chapter(btext, pg["page"], mode):
                if current["content"] or current["pages"]:
                    chapters.append(current)
                current = {
                    "title": strip_heading_marks(btext),
                    "subtitle": pending_author,
                    "pages": [pg["page"]],
                    "content": [],
                }
                pending_author = ""
                i += 1
                continue

            if pg["page"] not in current["pages"]:
                current["pages"].append(pg["page"])
            current["content"].append({"page": pg["page"], "type": block["type"], "text": btext})
            i += 1

    if current["content"] or current["pages"]:
        chapters.append(current)

    return _merge_empty_front_matter(chapters)


def _merge_empty_front_matter(chapters):
    cleaned = [ch for ch in chapters if ch["content"] or ch["pages"]]
    if len(cleaned) >= 2 and cleaned[0]["title"] == "Front Matter" and not cleaned[0]["content"]:
        cleaned[1]["pages"] = sorted(set(cleaned[0]["pages"] + cleaned[1]["pages"]))
        return cleaned[1:]
    return cleaned


# ---------------------------------------------------------------------------
# Format builders
# ---------------------------------------------------------------------------

def build_txt(src, pages, out_path):
    title = src.get("title", "Untitled")
    book_id = src.get("book_id", "")
    page_range = src.get("page_range", [])

    lines = [
        f"# {title}",
        f"book_id: {book_id}",
        f"source: {src.get('source', '')}",
        f"page_range: {page_range}",
        f"generated_at: {src.get('generated_at', '')}",
        "",
    ]
    for pg in pages:
        if pg.get("pagetext"):
            lines.append(f"===== PAGE {pg['page']} =====")
            lines.append(pg["pagetext"])
            lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def build_html(src, chapters, out_path):
    title = src.get("title", "Untitled")
    book_id = src.get("book_id", "")
    page_range = src.get("page_range", [])

    sections = []
    toc = []
    for idx, ch in enumerate(chapters, start=1):
        body = []
        if ch["subtitle"]:
            body.append(f'<p class="chapter-subtitle">{html.escape(ch["subtitle"])}</p>')
        for block in ch["content"]:
            text = html.escape(block["text"])
            if block["type"] == "footnote":
                body.append(f'<p class="footnote">{text}</p>')
            elif block["type"] == "image-caption":
                body.append(f'<p class="image-caption">{text}</p>')
            else:
                body.append(f"<p>{text}</p>")
        page_span = f"{min(ch['pages'])}–{max(ch['pages'])}" if ch["pages"] else ""
        sections.append(
            f'<section class="chapter" id="chapter-{idx}">'
            f"<h2>{html.escape(ch['title'])}</h2>"
            f'<div class="chapter-meta">Pages: {html.escape(page_span)}</div>'
            f'<div class="chapter-body">{"".join(body)}</div>'
            f"</section>"
        )
        toc.append(f'<li><a href="#chapter-{idx}">{html.escape(ch["title"])}</a></li>')

    doc = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin: 0; padding: 24px; background: #f5f5f5; color: #111;
           font: 18px/1.65 Georgia, "Times New Roman", serif; }}
    main {{ max-width: 900px; margin: 0 auto; }}
    header, nav.toc, section.chapter {{ background: #fff; border: 1px solid #ddd;
      border-radius: 12px; padding: 20px; margin-bottom: 20px; }}
    h1, h2 {{ margin-top: 0; }}
    .meta, .chapter-meta {{ color: #666; font-size: 14px; }}
    .toc ol {{ margin: 0; padding-left: 20px; }}
    .chapter-subtitle {{ font-style: italic; color: #444; }}
    .footnote {{ font-size: 0.92em; color: #444; border-top: 1px solid #e3e3e3; padding-top: 10px; }}
    .image-caption {{ font-style: italic; color: #444; }}
    .chapter-body p {{ white-space: pre-wrap; margin: 0 0 1em; }}
  </style>
</head>
<body><main>
  <header>
    <h1>{html.escape(title)}</h1>
    <div class="meta">
      <div>book_id: {html.escape(book_id)}</div>
      <div>source: {html.escape(src.get("source", ""))}</div>
      <div>page_range: {html.escape(str(page_range))}</div>
      <div>generated_at: {html.escape(src.get("generated_at", ""))}</div>
    </div>
  </header>
  <nav class="toc"><h2>Contents</h2><ol>{"".join(toc)}</ol></nav>
  {"".join(sections)}
</main></body></html>
"""
    out_path.write_text(doc, encoding="utf-8")


def build_fb2(src, chapters, out_path):
    fb = ET.Element(f"{{{NS_FB2}}}FictionBook")
    desc = ET.SubElement(fb, f"{{{NS_FB2}}}description")
    title_info = ET.SubElement(desc, f"{{{NS_FB2}}}title-info")
    book_title = ET.SubElement(title_info, f"{{{NS_FB2}}}book-title")
    book_title.text = src.get("title", "Untitled")
    lang = ET.SubElement(title_info, f"{{{NS_FB2}}}lang")
    lang.text = "ru"
    doc_info = ET.SubElement(desc, f"{{{NS_FB2}}}document-info")
    program = ET.SubElement(doc_info, f"{{{NS_FB2}}}program-used")
    program.text = "biblioatom-cli"
    date = ET.SubElement(doc_info, f"{{{NS_FB2}}}date")
    date.text = src.get("generated_at", "")

    body = ET.SubElement(fb, f"{{{NS_FB2}}}body")
    title_sec = ET.SubElement(body, f"{{{NS_FB2}}}title")
    p = ET.SubElement(title_sec, f"{{{NS_FB2}}}p")
    p.text = src.get("title", "Untitled")

    for idx, ch in enumerate(chapters, start=1):
        sec = ET.SubElement(body, f"{{{NS_FB2}}}section")
        sec.set("id", f"chapter_{idx}")
        sec_title = ET.SubElement(sec, f"{{{NS_FB2}}}title")
        ET.SubElement(sec_title, f"{{{NS_FB2}}}p").text = ch["title"]
        if ch["subtitle"]:
            ET.SubElement(sec, f"{{{NS_FB2}}}subtitle").text = ch["subtitle"]
        for block in ch["content"]:
            if not block["text"]:
                continue
            if block["type"] == "image-caption":
                ET.SubElement(sec, f"{{{NS_FB2}}}subtitle").text = block["text"]
            else:
                ET.SubElement(sec, f"{{{NS_FB2}}}p").text = block["text"]

    ET.ElementTree(fb).write(out_path, encoding="utf-8", xml_declaration=True)


def _make_epub_xhtml(title, body_html):
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="ru" xml:lang="ru">\n'
        f"<head><title>{html.escape(title)}</title>"
        '<link rel="stylesheet" type="text/css" href="../styles/style.css"/>'
        '<meta charset="utf-8"/></head>\n'
        f"<body>\n{body_html}\n</body></html>"
    )


def _img_for_page(page_no: int, images_dir: Path | None) -> tuple[str, Path] | None:
    """Return (epub_href, local_path) for page_no, or None if no match found."""
    if images_dir is None:
        return None
    matches = sorted(Path(images_dir).glob(f"{page_no:04d}_*.jpg"))
    return (f"images/{matches[0].name}", matches[0]) if matches else None


def build_epub(src, chapters, out_path, images_dir=None):
    """Build an EPUB file.

    images_dir: optional Path to a directory with {page:04d}_*.jpg files.
    When provided, image-caption blocks get a <figure><img/><figcaption/>
    element instead of a plain italic paragraph.
    """
    title = src.get("title", "Untitled")
    book_id = src.get("book_id", "book")

    style_css = (
        "body{font-family:serif;line-height:1.5;}"
        "h1,h2{margin:1em 0 .5em;}"
        "p{margin:0 0 .8em;white-space:pre-wrap;}"
        ".footnote{font-size:.92em;}.image-caption{font-style:italic;}"
        ".chapter-subtitle{font-style:italic;color:#444;}"
        "figure{margin:1.2em 0;text-align:center;}"
        "figure img{max-width:100%;height:auto;}"
        "figcaption{font-style:italic;font-size:.9em;color:#555;margin-top:.4em;}"
    )

    # page_no → (epub_href, local_path)  — avoids duplicate manifest entries
    embedded_images: dict[int, tuple[str, Path]] = {}

    manifest_items = [
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="nav" href="text/nav.xhtml" media-type="application/xhtml+xml"/>',
        '<item id="css" href="styles/style.css" media-type="text/css"/>',
        '<item id="intro" href="text/intro.xhtml" media-type="application/xhtml+xml"/>',
    ]
    spine_items = ['<itemref idref="intro"/>']
    nav_items = ['<li><a href="intro.xhtml">Начало</a></li>']
    chapter_files = []

    for idx, ch in enumerate(chapters, start=1):
        body = [f"<h2>{html.escape(ch['title'])}</h2>"]
        if ch["subtitle"]:
            body.append(f'<p class="chapter-subtitle">{html.escape(ch["subtitle"])}</p>')
        for block in ch["content"]:
            text = html.escape(block["text"])
            if block["type"] == "footnote":
                body.append(f'<p class="footnote">{text}</p>')
            elif block["type"] == "image-caption":
                img = _img_for_page(block["page"], images_dir)
                if img:
                    href, local = img
                    img_id = f"img_{block['page']:04d}"
                    if block["page"] not in embedded_images:
                        embedded_images[block["page"]] = (href, local)
                        manifest_items.append(
                            f'<item id="{img_id}" href="{href}" media-type="image/jpeg"/>'
                        )
                    body.append(
                        f'<figure>'
                        f'<img src="../{href}" alt="{html.escape(block["text"][:120])}"/>'
                        f"<figcaption>{text}</figcaption>"
                        f"</figure>"
                    )
                else:
                    body.append(f'<p class="image-caption">{text}</p>')
            else:
                body.append(f"<p>{text}</p>")
        fname = f"text/chapter_{idx}.xhtml"
        chapter_files.append((fname, _make_epub_xhtml(ch["title"], "\n".join(body))))
        manifest_items.append(
            f'<item id="chapter_{idx}" href="{fname}" media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'<itemref idref="chapter_{idx}"/>')
        nav_items.append(f'<li><a href="chapter_{idx}.xhtml">{html.escape(ch["title"])}</a></li>')

    intro = _make_epub_xhtml(
        title,
        f"<h1>{html.escape(title)}</h1>"
        f"<p><strong>Source:</strong> {html.escape(src.get('source', ''))}</p>"
        f"<p><strong>Generated at:</strong> {html.escape(src.get('generated_at', ''))}</p>",
    )
    nav_xhtml = _make_epub_xhtml(
        "Contents",
        f'<h1>Contents</h1><ol>{"".join(nav_items)}</ol>',
    )

    ncx_points = ['<navPoint id="navPoint-0" playOrder="0">'
                  '<navLabel><text>Начало</text></navLabel>'
                  '<content src="text/intro.xhtml"/></navPoint>']
    for idx, ch in enumerate(chapters, start=1):
        ncx_points.append(
            f'<navPoint id="navPoint-{idx}" playOrder="{idx}">'
            f"<navLabel><text>{html.escape(ch['title'])}</text></navLabel>"
            f'<content src="text/chapter_{idx}.xhtml"/></navPoint>'
        )

    content_opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{html.escape(title)}</dc:title>"
        "<dc:language>ru</dc:language>"
        f'<dc:identifier id="BookId">{html.escape(book_id)}</dc:identifier>'
        "</metadata>"
        f'<manifest>{" ".join(manifest_items)}</manifest>'
        f'<spine toc="ncx">{" ".join(spine_items)}</spine>'
        "</package>"
    )
    toc_ncx = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        f'<head><meta name="dtb:uid" content="{html.escape(book_id)}"/></head>'
        f"<docTitle><text>{html.escape(title)}</text></docTitle>"
        f'<navMap>{"".join(ncx_points)}</navMap>'
        "</ncx>"
    )
    container_xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        "<rootfiles>"
        '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>"
    )

    with zipfile.ZipFile(out_path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/toc.ncx", toc_ncx)
        zf.writestr("OEBPS/text/intro.xhtml", intro)
        zf.writestr("OEBPS/text/nav.xhtml", nav_xhtml)
        zf.writestr("OEBPS/styles/style.css", style_css)
        for fname, content in chapter_files:
            zf.writestr(f"OEBPS/{fname}", content)
        for _page_no, (href, local) in embedded_images.items():
            zf.write(local, f"OEBPS/{href}")


# ---------------------------------------------------------------------------
# TOC-based chapter splitting
# ---------------------------------------------------------------------------

def split_chapters_by_toc(pages, toc):
    """Build chapters from a parsed TOC instead of heuristic heading detection.

    toc items: {title, author, page, print_page, level}
      page — physical 0-based page index

    When two consecutive TOC entries share the same page (e.g. a section
    header and its first child), the earlier entry becomes a content-free
    divider; only the last entry at that page owns the page's content.
    """
    if not toc:
        return []

    page_content = {pg["page"]: pg for pg in pages}

    chapters = []
    for i, entry in enumerate(toc):
        # Does the next TOC entry start at the same page? → this is a divider
        next_entry = toc[i + 1] if i + 1 < len(toc) else None
        is_divider = next_entry is not None and next_entry["page"] == entry["page"]

        if is_divider:
            chapter_pages = []
            content = []
        else:
            # Own all pages from entry["page"] up to (but not including) the
            # next entry that starts at a strictly greater page
            next_diff_page = None
            for j in range(i + 1, len(toc)):
                if toc[j]["page"] > entry["page"]:
                    next_diff_page = toc[j]["page"]
                    break

            if next_diff_page is None:
                chapter_pages = sorted(p for p in page_content if p >= entry["page"])
            else:
                chapter_pages = sorted(
                    p for p in range(entry["page"], next_diff_page) if p in page_content
                )

            content = []
            for pno in chapter_pages:
                for block in page_content[pno]["blocks"]:
                    if block["text"].strip():
                        content.append(
                            {"page": pno, "type": block["type"], "text": block["text"].strip()}
                        )

        chapters.append(
            {
                "title": entry["title"],
                "subtitle": entry["author"],
                "print_page": entry["print_page"],
                "level": entry["level"],
                "pages": chapter_pages,
                "content": content,
            }
        )

    return chapters


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_book(src, formats, outdir, prefix="", chapter_mode="strict", images_dir=None):
    """Convert src dict to requested formats in outdir. Returns list of written paths."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    formats = {f.strip().lower() for f in formats if f.strip()}
    unknown = formats - FORMATS
    if unknown:
        raise ValueError(f"Unknown formats: {', '.join(sorted(unknown))}")

    input_path = Path(src.get("book_id", "book"))
    stem = output_stem(src, input_path, prefix)

    pages = build_book_models(src)

    toc = src.get("toc") or []
    need_chapters = formats - {"json", "txt"}
    if need_chapters:
        if toc:
            chapters = split_chapters_by_toc(pages, toc)
        else:
            chapters = split_into_chapters(pages, mode=chapter_mode)
    else:
        chapters = []

    written = []

    if "json" in formats:
        out = outdir / f"{stem}.json"
        out.write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(out)

    if "txt" in formats:
        out = outdir / f"{stem}.txt"
        build_txt(src, pages, out)
        written.append(out)

    if "html" in formats:
        if not chapters:
            chapters = split_into_chapters(pages, mode=chapter_mode)
        out = outdir / f"{stem}.html"
        build_html(src, chapters, out)
        written.append(out)

    if "fb2" in formats:
        if not chapters:
            chapters = split_into_chapters(pages, mode=chapter_mode)
        out = outdir / f"{stem}.fb2"
        build_fb2(src, chapters, out)
        written.append(out)

    if "epub" in formats:
        if not chapters:
            chapters = split_into_chapters(pages, mode=chapter_mode)
        out = outdir / f"{stem}.epub"
        build_epub(src, chapters, out, images_dir=images_dir)
        written.append(out)

    return written
