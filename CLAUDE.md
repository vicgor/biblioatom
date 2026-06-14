# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project context

Python CLI для скачивания и конвертации книг с `elib.biblioatom.ru` через публичный RPC-эндпоинт `/rpc/bookviewer/cp/`. Является преемником `../biblioatom-extractor` (который был Tampermonkey-скриптом + отдельным конвертером).

## Commands

```bash
# Run the CLI
biblioatom kapitsa_1994 -f epub,json -o output/
biblioatom kapitsa_1994 --from-page 0 --to-page 100 --delay 500
biblioatom --from-json export.json -f epub,fb2,html,txt

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
├── fetch.py     — HTTP layer: fetch_book_meta, fetch_page, download_book
├── convert.py   — Format converters: build_txt/html/fb2/epub + chapter detection
└── cli.py       — argparse entry point; download → build_book() pipeline
tests/
├── test_fetch.py    — urllib.request mocked via unittest.mock.patch
└── test_convert.py  — pure unit tests, no I/O mocking needed
```

**Data flow:**
1. `fetch.fetch_book_meta(book_id)` → `(title, max_page)` — parses HTML page
2. `fetch.download_book(...)` → `items: list[dict]` — each item: `{page, content: {valid, pagetext, pagehtml}}`
3. CLI assembles `src` dict (title, book_id, source, page_range, generated_at, items)
4. `convert.build_book(src, formats, outdir)` → writes output files

**JSON compatibility:** The `src` dict format is compatible with `../biblioatom-extractor/convert_book.py` — `content` can be a dict or a JSON string; `parse_embedded_content()` handles both.

## Conventions

- stdlib only — no third-party runtime deps.
- CLI output is Russian (`print` to stdout, progress on same line with `\r`).
- `build-backend = "setuptools.build_meta"` — не `setuptools.backends.legacy:build` (нет в setuptools 82).
