# CLAUDE.md

Этот файл — инструкция для Claude Code при работе с репозиторием.

## Контекст проекта

Python CLI для скачивания и конвертации книг с `elib.biblioatom.ru` через публичный RPC-эндпоинт `/rpc/bookviewer/cp/`. Преемник `../biblioatom-extractor` (Tampermonkey-скрипт + отдельный конвертер).

**Версия:** 0.2.0 · **Python:** 3.12+ · **Менеджер:** uv

## Команды разработки

```bash
# Зависимости
uv sync

# Запуск CLI
uv run biblioatom --help
uv run biblioatom fetch kapitsa_1994 -o book.json
uv run biblioatom pipeline kapitsa_1994 --images --azw3 -o book.epub

# Качество кода
uv run ruff check
uv run ruff format --check
uv run mypy --strict src/

# Тесты
uv run pytest
uv run pytest tests/test_structure_analyzer.py   # один файл
uv run pytest -k "test_single_photo"             # по имени теста
uv run pytest --tb=short                         # краткий traceback
```

## Архитектура

Thin CLI / Fat Core, SoC, SRP, Dependency Inversion через `typing.Protocol`.

```
src/biblioatom/
├── cli.py             — Typer: парсинг аргументов → core → ExitCode (бизнес-логики нет)
├── ui.py              — Rich console (stdout) и err_console (stderr)
├── config.py          — pydantic-settings: 9 групп, префикс BIBLIOATOM_, разделитель __
├── errors.py          — BookgrabError → ConfigurationError/FetchError/… + ExitCode enum
├── logging_config.py  — structlog: correlation_id, redact секретов, JSON/pretty авто
├── models.py          — Pydantic v2: ElementKind, BookElement, PageModel, StructuredDocument, …
├── core/              — use cases (оркестрация, без I/O-деталей)
│   ├── fetch_book.py         — загрузка: meta + TOC + страницы (best-effort по ошибкам страниц)
│   ├── analyze_structure.py  — передача pages/toc в анализатор, простановка title/book_id
│   ├── extract_scan_images.py — select_photo_pages + оркестрация ScanExtractor/ImageProcessor
│   ├── build_epub.py         — передача StructuredDocument в epub_builder
│   ├── convert_to_azw3.py    — вызов converter с логированием
│   └── run_pipeline.py       — сквозной: fetch→analyze→[scans]→epub→[azw3], PipelineResult
└── services/
    ├── __init__.py          — только Protocol-интерфейсы (DI-контракты)
    ├── fetcher.py           — httpx.Client + tenacity (retry только transient: 408/429/5xx)
    ├── parser.py            — selectolax: parse_book_meta, parse_toc, parse_embedded_content
    ├── structure_analyzer.py — split_by_toc / split_into_chapters + StructureAnalyzer
    ├── html_cleaner.py      — normalize_text, clean_pagehtml, strip_tags_preserve_text
    ├── scan_extractor.py    — OpenCV: grayscale→GaussianBlur→Otsu/Canny→morphology→findContours→crop
    ├── image_processor.py   — Pillow: ресайз до max_width/max_height, конвертация режима, сохранение
    ├── epub_builder.py      — EbookLib: EPUB 3, nav, spine, figcaption, двусторонние якоря сносок
    └── converter.py         — subprocess.run(cmd_list) ebook-convert (shell=False)
```

## Поток данных

```
CLI (cli.py)
  └─ fetch_book(fetcher, parser, book_id)  →  FetchedBook
       └─ analyze_structure(analyzer, pages, toc)  →  StructuredDocument
            ├─ [extract_scan_images(scan_extractor, image_processor, scans, dir)]  →  ScanExtractionResult
            ├─ build_epub(epub_builder, document, out_path, images)  →  BuildResult
            └─ [convert_to_azw3(converter, epub, azw3)]  →  BuildResult
```

## Protocol-интерфейсы

Все `*Protocol` объявлены в `services/__init__.py`. Реализации внедряются в use cases через аргументы — use cases не создают зависимости сами.

| Protocol | Реализация | Где внедряется |
|----------|-----------|----------------|
| `FetcherProtocol` | `Fetcher` | `fetch_book`, `run_pipeline` |
| `ParserProtocol` | `Parser` | `fetch_book`, `run_pipeline` |
| `StructureAnalyzerProtocol` | `StructureAnalyzer` | `analyze_structure`, `run_pipeline` |
| `EpubBuilderProtocol` | `EpubBuilder` | `build_epub`, `run_pipeline` |
| `ConverterProtocol` | `EbookConvertConverter` | `convert_to_azw3`, `run_pipeline` |
| `ScanExtractorProtocol` | `ScanExtractor` | `extract_scan_images`, `run_pipeline` |
| `ImageProcessorProtocol` | `ImageProcessor` | `extract_scan_images`, `run_pipeline` |

## Обработка ошибок

- Все доменные ошибки — подклассы `BookgrabError` из `errors.py`.
- Каждый класс несёт `exit_code: ExitCode` (0/2/3/4/5/6/7/8/10).
- CLI ловит `BookgrabError` в `_handle_errors()` → печатает сообщение в stderr → `raise typer.Exit(code)`.
- `KeyboardInterrupt` → код 130.
- Traceback только при `-vv` (verbose ≥ 2).
- Retry (tenacity) — только для transient-ошибок: `TimeoutException`, `TransportError`, статусы 408/429/500/502/503/504. 404 и другие 4xx — немедленная доменная ошибка без ретрая.
- Внешние исключения оборачиваются через `raise DomainError(...) from exc`.
- `assert` в production-коде не используется — только `if ... raise`.

## Модели (`models.py`)

| Модель | Назначение |
|--------|-----------|
| `ElementKind` | StrEnum: CAPTION / FOOTNOTE / NOTE / EPIGRAPH / QUOTE / SIDEBAR / HEADING / LIST_ / TABLE |
| `BookElement` | Типизированный блок (kind, text, page, anchor, ref) |
| `TocEntry` | Запись оглавления (title, author, page, print_page, level) |
| `PageModel` | Страница с EmbeddedContent и list[BookElement] |
| `StructuredChapter` | Глава с pages и elements |
| `StructuredDocument` | Книга: title, book_id, toc, chapters |
| `ExtractedImage` | Кроп со скана (bytes + BoundingBox) |
| `ImageAsset` | Сохранённый файл иллюстрации (path, page, caption) |
| `BuildResult` | Результат сборки (book_id, outputs, images) |

## Конфигурация (`config.py`)

Все настройки — `Settings(BaseSettings)`, prefix `BIBLIOATOM_`, nested delimiter `__`.

Группы: `app` · `http` · `parsing` · `structure` · `scan_extraction` · `image` · `epub` · `conversion` · `logging`

```bash
# Примеры
BIBLIOATOM_HTTP__TIMEOUT=60
BIBLIOATOM_HTTP__MAX_RETRIES=5
BIBLIOATOM_LOGGING__LEVEL=DEBUG
BIBLIOATOM_CONVERSION__EBOOK_CONVERT_BIN=/opt/calibre/ebook-convert
BIBLIOATOM_SCAN_EXTRACTION__MIN_AREA_RATIO=0.03
BIBLIOATOM_IMAGE__MAX_WIDTH=1200
```

## Тесты

```
tests/
├── conftest.py                  — общие фикстуры
├── test_analyze_structure.py    — интеграционный: полный анализ книги
├── test_build_epub.py           — core use case build_epub
├── test_cli.py                  — CLI команды через CliRunner
├── test_config.py               — валидация Settings и env-переменных
├── test_convert_to_azw3.py      — core use case convert_to_azw3
├── test_converter.py            — EbookConvertConverter (мок subprocess)
├── test_epub_builder.py         — EpubBuilder (проверка ZIP/OPF/nav)
├── test_errors.py               — иерархия ошибок, exit_code_for, подтипы
├── test_extract_scan_images.py  — core use case extract_scan_images
├── test_fetch_book.py           — core use case fetch_book (мок fetcher)
├── test_fetcher.py              — Fetcher (respx HTTP-мок)
├── test_html_cleaner.py         — normalize_text, clean_pagehtml
├── test_image_processor.py      — ImageProcessor (Pillow)
├── test_logging_config.py       — setup_logging, correlation_id, redact
├── test_models.py               — Pydantic-модели и валидация
├── test_parser.py               — Parser (selectolax): TOC, meta, content
├── test_pipeline_integration.py — E2E без сети: FakeFetcher + реальные сервисы
├── test_scan_extractor.py       — ScanExtractor: синтетические сканы numpy/OpenCV
└── test_structure_analyzer.py   — split_by_toc, split_into_chapters, эвристики
```

Scan-тесты генерируют синтетические страницы через numpy — бинарные фикстуры не нужны.
Integration-тест использует `_FakeFetcher` (реализует `FetcherProtocol`) + реальные сервисы.

## Конвенции

- Все публичные функции типизированы; `mypy --strict src/` должен проходить без ошибок.
- `from __future__ import annotations` во всех модулях.
- Функции короткие; god objects отсутствуют.
- Структурированное логирование: `_logger.info("module.event", key=value)`.
- CLI-вывод — через `console`/`err_console` из `ui.py`, не через `print`.
- Команды CLI делегируют все вычисления в core; в `cli.py` — только сборка зависимостей и форматирование вывода.
- Секреты в логах маскируются процессором `redact_secrets` в `logging_config.py`.

## Известные технические долги

- `ElementKind.LIST = "list_"` — сериализованное значение отличается от имени члена; при необходимости совместимости с внешними форматами учитывать при десериализации.
- `get_logger()` возвращает `Any` — временный компромисс для совместимости с API structlog.
