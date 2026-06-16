# biblioatom

CLI для скачивания книг с [elib.biblioatom.ru](https://elib.biblioatom.ru) и сборки из них EPUB 3 (опционально — AZW3). Книги доступны без авторизации через публичный RPC-эндпоинт.

Стек: Python 3.12+, Typer, Pydantic v2, httpx, selectolax, OpenCV, Pillow, EbookLib, Calibre, structlog, tenacity, mypy strict.

## Возможности

- Загрузка страниц и оглавления книги по идентификатору или URL.
- Структурный анализ: разбивка на главы по TOC сайта (или эвристикой при его отсутствии).
- Сборка валидного **EPUB 3** (nav, spine, CSS, рабочие двусторонние якоря сносок, встраивание иллюстраций как `<figure>`).
- Извлечение иллюстраций со сканов средствами OpenCV (без OCR) и постобработка через Pillow.
- Конвертация EPUB → **AZW3** через Calibre `ebook-convert`.
- Полный сквозной пайплайн одной командой.

## Требования

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — менеджер пакетов и окружений
- **Calibre** (`ebook-convert` в `PATH`) — только для конвертации в AZW3
- Системные библиотеки для OpenCV (Linux): `libgl1`, `libglib2.0-0` — только для извлечения сканов

## Установка

```bash
# Клонировать и установить зависимости в изолированное окружение
git clone https://github.com/vicgor/biblioatom.git
cd biblioatom
uv sync

# Запускать через uv
uv run biblioatom --help
```

Для установки как глобального инструмента:

```bash
uv tool install .
biblioatom --help
```

## Команды

### `fetch` — скачать книгу в JSON

```bash
uv run biblioatom fetch kapitsa_1994 -o book.json
uv run biblioatom fetch https://elib.biblioatom.ru/text/kapitsa_1994/ -o book.json

# Диапазон страниц (0-based индексы)
uv run biblioatom fetch kapitsa_1994 --from-page 0 --to-page 100 -o book.json
```

`SOURCE` — идентификатор книги (сегмент после `/text/` в URL) или полный URL.
Результат — JSON с полями `title`, `book_id`, `toc`, `pages`.

### `analyze` — проанализировать структуру

```bash
# По идентификатору (скачивает на лету)
uv run biblioatom analyze kapitsa_1994

# По локальному JSON (без сети)
uv run biblioatom analyze book.json

# Машиночитаемый JSON-вывод
uv run biblioatom analyze book.json --json

# Режим разбивки на главы при отсутствии TOC
uv run biblioatom analyze book.json --chapter-mode normal
```

`--chapter-mode`: `strict` (по умолчанию) — только явные заголовки на странице ≥ 5; `normal` — любой вероятный заголовок.

### `extract-scans` — извлечь иллюстрации из сканов

```bash
uv run biblioatom extract-scans ./scans -o ./images
```

Принимает каталог с PNG/JPEG-сканами, возвращает каталог с кропами иллюстраций.
Файлы сортируются по натуральному номеру в имени (`page_2.png` → `page_10.png`).

### `build` — собрать EPUB из JSON

```bash
uv run biblioatom build book.json -o book.epub
uv run biblioatom build book.json --chapter-mode normal -o book.epub
```

### `convert` — сконвертировать EPUB в AZW3

```bash
uv run biblioatom convert book.epub -o book.azw3
# Без -o: book.azw3 создаётся рядом с book.epub
uv run biblioatom convert book.epub
```

Требует Calibre (`ebook-convert` в `PATH`).

### `pipeline` — полный пайплайн одной командой

```bash
# Базовый: загрузка → анализ → EPUB
uv run biblioatom pipeline kapitsa_1994 -o book.epub

# С извлечением иллюстраций и конвертацией в AZW3
uv run biblioatom pipeline kapitsa_1994 --images --azw3 -o book.epub

# С ограничением диапазона страниц
uv run biblioatom pipeline kapitsa_1994 --from-page 0 --to-page 200 -o book.epub
```

## Глобальные опции

| Флаг | Описание |
|------|----------|
| `-V, --version` | Показать версию и выйти |
| `-v, --verbose` | `-v` → INFO, `-vv` → DEBUG (показывает traceback при ошибке) |
| `-q, --quiet` | Минимальный вывод (только ошибки) |
| `-c, --config PATH` | Путь к `.env`-файлу с настройками |

## Коды завершения

| Код | Категория |
|-----|-----------|
| `0` | Успех |
| `2` | Ошибка конфигурации |
| `3` | Невалидный ввод (диапазон страниц, путь и т.п.) |
| `4` | Сетевой сбой (загрузка) |
| `5` | Ошибка разбора HTML/JSON |
| `6` | Ошибка структурного анализа |
| `7` | Ошибка извлечения/обработки изображений |
| `8` | Ошибка сборки EPUB |
| `10` | Сбой внешнего инструмента (`ebook-convert`) |
| `130` | Прерывание пользователем (Ctrl+C) |

Traceback печатается только в режиме `-vv`/DEBUG; иначе — понятное сообщение в stderr и код возврата.

## Конфигурация

Настройки читаются из переменных окружения с префиксом `BIBLIOATOM_` (вложенные группы — через `__`) или из файла `.env`.

### HTTP и retry

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `BIBLIOATOM_HTTP__TIMEOUT` | `30.0` | Таймаут запроса, сек |
| `BIBLIOATOM_HTTP__CONNECT_TIMEOUT` | `10.0` | Таймаут соединения, сек |
| `BIBLIOATOM_HTTP__MAX_RETRIES` | `3` | Максимум повторных попыток |
| `BIBLIOATOM_HTTP__BACKOFF_FACTOR` | `0.5` | Начальная пауза backoff, сек |
| `BIBLIOATOM_HTTP__BACKOFF_MAX` | `10.0` | Максимальная пауза backoff, сек |
| `BIBLIOATOM_HTTP__DELAY_MS` | `300` | Пауза между страницами, мс |

Retry выполняется только для transient-сбоев: таймауты, сетевые ошибки, статусы 408/429/500/502/503/504. Ошибки 404 и другие 4xx — доменная ошибка без ретрая.

### Логирование

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `BIBLIOATOM_LOGGING__LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `BIBLIOATOM_LOGGING__JSON_LOGS` | `false` | Принудительно JSON (иначе — авто по `isatty`) |

### Конвертация

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `BIBLIOATOM_CONVERSION__EBOOK_CONVERT_BIN` | `ebook-convert` | Путь к бинарю Calibre |
| `BIBLIOATOM_CONVERSION__TIMEOUT` | `300.0` | Таймаут конвертации, сек |

### Прочие группы

| Группа | Prefix | Ключевые поля |
|--------|--------|---------------|
| `app` | `BIBLIOATOM_APP__` | `base_url`, `user_agent` |
| `parsing` | `BIBLIOATOM_PARSING__` | CSS-селекторы, `fallback_max_page` |
| `structure` | `BIBLIOATOM_STRUCTURE__` | `min_chapter_pages`, `heading_max_words` |
| `scan_extraction` | `BIBLIOATOM_SCAN_EXTRACTION__` | Фильтры OpenCV: `min_area_ratio`, `min_fill_ratio`, `morph_kernel`, … |
| `image` | `BIBLIOATOM_IMAGE__` | `output_format`, `quality`, `max_width`, `max_height` |
| `epub` | `BIBLIOATOM_EPUB__` | `language`, `embed_images`, `css` |

Пример `.env`:

```bash
BIBLIOATOM_HTTP__TIMEOUT=60
BIBLIOATOM_HTTP__MAX_RETRIES=5
BIBLIOATOM_LOGGING__LEVEL=DEBUG
BIBLIOATOM_CONVERSION__EBOOK_CONVERT_BIN=/opt/calibre/ebook-convert
```

## Shell completion

```bash
# Установить автодополнение для текущего shell
uv run biblioatom --install-completion

# Показать скрипт без установки
uv run biblioatom --show-completion
```

Поддерживаются bash, zsh, fish, PowerShell.

## Архитектура

Многослойная архитектура (Thin CLI / Fat Core, SoC, SRP, Dependency Inversion):

```
src/biblioatom/
├── cli.py             — тонкий слой Typer: парсинг аргументов → core → вывод → ExitCode
├── ui.py              — Rich Console (stdout) и err_console (stderr)
├── config.py          — pydantic-settings, 9 групп настроек
├── errors.py          — иерархия BookgrabError + ExitCode enum
├── logging_config.py  — structlog (correlation_id, redact секретов, JSON/pretty)
├── models.py          — доменные модели Pydantic v2
├── core/              — use cases: оркестрация без I/O-деталей
│   ├── fetch_book.py         — загрузка метаданных, TOC и страниц (best-effort)
│   ├── analyze_structure.py  — передача страниц/TOC в анализатор, простановка мета
│   ├── extract_scan_images.py — отбор фото-страниц, оркестрация кропа/постобработки
│   ├── build_epub.py         — передача документа в epub_builder, возврат BuildResult
│   ├── convert_to_azw3.py    — вызов converter, возврат BuildResult
│   └── run_pipeline.py       — сквозной пайплайн: fetch → analyze → [scans] → epub → [azw3]
└── services/          — реализации, внедряемые через typing.Protocol
    ├── __init__.py          — Protocol-интерфейсы (FetcherProtocol, ParserProtocol, …)
    ├── fetcher.py           — httpx + tenacity
    ├── parser.py            — selectolax: метаданные, TOC, embedded content
    ├── structure_analyzer.py — разбивка на главы (TOC или эвристика заголовков)
    ├── html_cleaner.py      — нормализация текста и HTML
    ├── scan_extractor.py    — OpenCV: grayscale→blur→Otsu/Canny→findContours→crop
    ├── image_processor.py   — Pillow: ресайз, нормализация режима, сохранение
    ├── epub_builder.py      — EbookLib: EPUB 3, nav, figcaption, якоря сносок
    └── converter.py         — subprocess ebook-convert (без shell=True)
```

**Принцип:** CLI не содержит бизнес-логики. Core-слой зависит от сервисов через `typing.Protocol` — use cases тестируются без сети, без OpenCV, без Calibre.

### Поток данных

```
fetch_book  →  FetchedBook(pages, toc, title)
                     ↓
analyze_structure  →  StructuredDocument(chapters, toc)
                     ↓
[extract_scan_images  →  list[ImageAsset]]
                     ↓
build_epub  →  BuildResult(outputs=[book.epub])
                     ↓
[convert_to_azw3  →  BuildResult(outputs=[book.azw3])]
```

### Protocol-интерфейсы (`services/__init__.py`)

| Protocol | Реализация | Внедряется в |
|----------|-----------|--------------|
| `FetcherProtocol` | `Fetcher` (httpx) | `fetch_book`, `run_pipeline` |
| `ParserProtocol` | `Parser` (selectolax) | `fetch_book`, `run_pipeline` |
| `StructureAnalyzerProtocol` | `StructureAnalyzer` | `analyze_structure`, `run_pipeline` |
| `EpubBuilderProtocol` | `EpubBuilder` (EbookLib) | `build_epub`, `run_pipeline` |
| `ConverterProtocol` | `EbookConvertConverter` | `convert_to_azw3`, `run_pipeline` |
| `ScanExtractorProtocol` | `ScanExtractor` (OpenCV) | `extract_scan_images`, `run_pipeline` |
| `ImageProcessorProtocol` | `ImageProcessor` (Pillow) | `extract_scan_images`, `run_pipeline` |

## Как расширять

**Новый источник данных** — реализуйте `FetcherProtocol` из `services/__init__.py` и передайте свою реализацию в `fetch_book` или `run_pipeline`.

**Новый выходной формат** — реализуйте `ConverterProtocol` (или `EpubBuilderProtocol`) и передайте в `convert_to_azw3` / `build_epub`.

**Новая команда CLI** — добавьте `@app.command()` в `cli.py`:
1. Соберите нужные сервисы из `ctx.obj["config"]`.
2. Вызовите use case под `_handle_errors(verbose=verbose)`.
3. Выведите результат через `console`.

**Другой парсер HTML** — реализуйте `ParserProtocol`, подмените в CLI при сборке `Fetcher`.

## Разработка

```bash
uv sync

# Линтер и форматирование
uv run ruff check
uv run ruff format --check

# Статический анализ
uv run mypy --strict src/

# Тесты
uv run pytest
uv run pytest tests/test_structure_analyzer.py   # один модуль
uv run pytest -k "test_single_photo"             # по имени
uv run pytest --tb=short                         # краткий traceback
```

CI запускается на Python 3.12 и 3.13 при каждом push/PR в `main`.
