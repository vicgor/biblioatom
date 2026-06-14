# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project context

Python CLI для скачивания и конвертации книг с `elib.biblioatom.ru` через публичный RPC-эндпоинт `/rpc/bookviewer/cp/`. Является преемником `../biblioatom-extractor` (который был Tampermonkey-скриптом + отдельным конвертером).

## Commands

```bash
# Run the CLI
biblioatom kapitsa_1994 -f epub,json -o output/
biblioatom kapitsa_1994 --images -o output/        # with embedded illustrations
biblioatom kapitsa_1994 --from-page 0 --to-page 100 --delay 500
biblioatom --from-json export.json -f epub,fb2,html,txt
biblioatom --from-json export.json --images -f epub -o output/

# Install (editable)
pip install -e .

# Run tests
python -m pytest
python -m pytest tests/test_convert.py  # single file
python -m pytest -k TestBuildBook       # single class
```

## Architecture

```
biblioatom/
├── fetch.py     — HTTP layer: fetch_book_meta, fetch_toc, fetch_page, fetch_image, download_book
├── convert.py   — Format converters: build_txt/html/fb2/epub + chapter detection
└── cli.py       — argparse entry point; download → build_book() pipeline
tests/
├── test_fetch.py    — urllib.request mocked via unittest.mock.patch
└── test_convert.py  — pure unit tests, no I/O mocking needed
```

**Data flow:**
1. `fetch.fetch_book_meta(book_id)` → `(title, max_page)` — parses HTML page
2. `fetch.fetch_toc(book_id)` → `list[{title, author, page, print_page, level}]` — parses `<aside data-type="tree-box-contents">` on `/text/{book_id}/p0/`
3. `fetch.download_book(...)` → `items: list[dict]` — each item: `{page, content: {valid, pagetext, pagehtml}}`
4. CLI assembles `src` dict (title, book_id, source, page_range, generated_at, toc, items)
5. If `--images`: `fetch.fetch_image(book_id, page_no)` for each page with an image-caption block → saved as `outdir/images/{page:04d}_{slug}.jpg`
6. `convert.build_book(src, formats, outdir, images_dir=...)` → writes output files

**JSON compatibility:** The `src` dict format is compatible with `../biblioatom-extractor/convert_book.py` — `content` can be a dict or a JSON string; `parse_embedded_content()` handles both.

**Image embedding:** `build_epub(src, chapters, out_path, images_dir=None)` — when `images_dir` is set, image-caption blocks (`<p class="img">`) become `<figure><img src="../images/…"/><figcaption>…</figcaption></figure>` with the JPEG added to the ZIP and OPF manifest. Images are matched by glob `{page:04d}_*.jpg`.

**Chapter splitting:** `split_chapters_by_toc(pages, toc)` — primary. Falls back to heuristic `split_into_chapters` if TOC is absent. When two consecutive TOC entries share the same page (section header + first child), the header becomes a divider chapter with no pages.

## Conventions

- stdlib only — no third-party runtime deps.
- CLI output is Russian (`print` to stdout, progress on same line with `\r`).
- `build-backend = "setuptools.build_meta"` — не `setuptools.backends.legacy:build` (нет в setuptools 82).
- Image captions come from `<p class="img">` blocks; `<div class="comp-draft">` is a container and must not be matched by the block extractor (only `<p>` tags are matched).
