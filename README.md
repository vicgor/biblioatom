# biblioatom

CLI для скачивания книг с [elib.biblioatom.ru](https://elib.biblioatom.ru) и сборки из них EPUB 3 (опционально — AZW3). Книги доступны без авторизации через публичный RPC-эндпоинт.

Стек: Python 3.12+, Typer, Pydantic v2, httpx, selectolax, OpenCV, Pillow, EbookLib, Calibre, structlog, tenacity, mypy strict.

## Возможности

- Загрузка страниц и оглавления книги по идентификатору или URL.
- Структурный анализ: разбивка на главы по TOC сайта (или эвристикой при его отсутствии).
- Сборка валидного **EPUB 3** (nav, spine, CSS, рабочие двусторонние якоря сносок, обложка, встраивание иллюстраций как `<figure>`).
- Извлечение иллюстраций со сканов средствами OpenCV (без OCR) и постобработка через Pillow.
- Конвертация EPUB → **AZW3** через Calibre `ebook-convert`.
- Импорт/экспорт библиографических данных в формате **RIS** (Zotero, Mendeley, EndNote).
- Полный сквозной пайплайн одной командой — с кэшем сырья в рабочем каталоге книги
  (`books/<book_id>/`) и оффлайн-пересборкой без повторного похода в сеть.

## Требования

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — менеджер пакетов и окружений
- **Calibre** (`ebook-convert` в `PATH`) — только для конвертации в AZW3

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

### `download` — скачать сырьё книги в рабочий каталог

```bash
uv run biblioatom download kapitsa_1994
uv run biblioatom download kapitsa_1994 --refresh          # перекачать заново, игнорируя кэш
uv run biblioatom download kapitsa_1994 --work-dir /data/books
uv run biblioatom download kapitsa_1994 --from-page 0 --to-page 100
```

Кэширует сырые ответы сервера (HTML метаданных/TOC, JSON RPC страниц, JPEG-сканы обложки и
фото-страниц) в `books/<book_id>/raw/` и собирает распарсенный `books/<book_id>/book.json`.
Идемпотентна: уже скачанные файлы пропускаются (можно безопасно повторять/докачивать после
сбоя); `--refresh` перекачивает всё заново. Сбой отдельной страницы/скана не обрывает загрузку
(best-effort).

### `clean` — очистить кэш книги

```bash
uv run biblioatom clean kapitsa_1994              # только raw/scans/ (сырые сканы)
uv run biblioatom clean kapitsa_1994 --raw        # весь raw/ (сырьё целиком)
uv run biblioatom clean kapitsa_1994 --all        # всё в каталоге книги, кроме .epub
uv run biblioatom clean kapitsa_1994 --work-dir /data/books
```

`--raw` и `--all` взаимоисключающие. Итоговый `.epub` не удаляется никогда.

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

### `import-ris` — импортировать библиографические записи

```bash
uv run biblioatom import-ris refs.ris -o refs.json
```

Разбирает RIS-файл и сохраняет список записей в JSON (поля: `source`, `count`, `entries`).

### `export-ris` — экспортировать оглавление в RIS

```bash
uv run biblioatom export-ris book.json -o book.ris
```

Создаёт RIS-файл из оглавления книги: одна запись типа `CHAP` на каждую главу, заголовок книги в теге `BT`.

### `convert` — сконвертировать EPUB в AZW3

```bash
uv run biblioatom convert book.epub -o book.azw3
# Без -o: book.azw3 создаётся рядом с book.epub
uv run biblioatom convert book.epub
```

Требует Calibre (`ebook-convert` в `PATH`).

### `pipeline` — полный пайплайн одной командой

```bash
# Базовый: [авто-download при отсутствии кэша] → анализ → EPUB → books/kapitsa_1994/kapitsa_1994.epub
uv run biblioatom pipeline kapitsa_1994

# С извлечением иллюстраций и конвертацией в AZW3
uv run biblioatom pipeline kapitsa_1994 --images --azw3

# Явный путь итогового EPUB
uv run biblioatom pipeline kapitsa_1994 --images -o book.epub

# Оффлайн-пересборка из уже скачанного кэша (без сети) — на порядки быстрее
uv run biblioatom pipeline kapitsa_1994 --images

# Перекачать сырьё заново, игнорируя кэш
uv run biblioatom pipeline kapitsa_1994 --refresh

# Свой корень рабочих каталогов и ограничение диапазона страниц
uv run biblioatom pipeline kapitsa_1994 --work-dir /data/books --from-page 0 --to-page 200
```

Сборка всегда идёт из рабочего каталога книги `books/<book_id>/` (см. `download`): если сырья ещё
нет (или указан `--refresh`), пайплайн сначала сам его скачивает, затем собирает EPUB оффлайн из
кэша. Без `-o` итоговый файл — `books/<book_id>/<book_id>.epub`. Повторный прогон уже скачанной
книги не обращается к сети вовсе: на тестовой книге из ~340 повторных секунд (полная сетевая
загрузка) оффлайн-пересборка занимает ~8 секунд.

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
| `3` | Невалидный ввод (диапазон страниц, путь, рабочий каталог и т.п.) |
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
| `app` | `BIBLIOATOM_APP__` | `base_url`, `user_agent`, `work_dir` (default `books` — корень рабочих каталогов книг) |
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
BIBLIOATOM_APP__WORK_DIR=/data/books
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
│   ├── extract_scan_images.py — отбор фото-страниц + кроп/постобработка сканов
│   ├── build_epub.py         — передача документа в epub_builder, возврат BuildResult
│   ├── convert_to_azw3.py    — вызов converter, возврат BuildResult
│   ├── download_book.py      — кэширует сырьё книги (meta/toc/pages/scans) в рабочий каталог
│   │                           + собирает book.json; идемпотентно, best-effort
│   ├── clean_workspace.py    — очистка кэша книги (scans/raw/all), .epub не трогает
│   └── run_pipeline.py       — сквозной пайплайн: [auto-download] → fetch → analyze →
│                               [scans] → epub → [azw3]; сборка всегда из рабочего каталога
│                               (оффлайн через LocalFetcher); обложка качается и проходит
│                               ImageProcessor (best-effort)
├── services/          — реализации, внедряемые через typing.Protocol
│   ├── __init__.py          — Protocol-интерфейсы (FetcherProtocol, RawFetcherProtocol,
│   │                          ParserProtocol, …)
│   ├── fetcher.py           — httpx + tenacity; отдаёт как разобранные данные, так и сырые
│   │                          ответы (fetch_*_raw) для кэширования
│   ├── local_fetcher.py     — LocalFetcher: FetcherProtocol поверх кэша рабочего каталога
│   ├── workspace.py         — BookWorkspace: раскладка путей books/<book_id>/ (raw/, book.json,
│   │                          images/, <book_id>.epub)
│   ├── parser.py            — selectolax: метаданные, TOC, embedded content
│   ├── source_utils.py      — нормализация SOURCE-аргумента (URL или book_id)
│   ├── structure_analyzer.py — разбивка на главы (TOC или эвристика заголовков)
│   ├── html_cleaner.py      — нормализация текста и HTML
│   ├── ris_parser.py        — разбор и генерация RIS-файлов
│   ├── scan_extractor.py    — OpenCV: Otsu/Canny + адаптивный fallback → crop
│   ├── image_processor.py   — Pillow: ресайз, нормализация режима, сохранение
│   ├── epub_builder.py      — EbookLib: EPUB 3, обложка, nav, figcaption, якоря сносок
│   └── converter.py         — subprocess ebook-convert (без shell=True)
└── tools/             — утилиты разработчика
    └── tune_scan.py   — подбор параметров OpenCV (grid-search / Optuna)
```

**Принцип:** CLI не содержит бизнес-логики. Core-слой зависит от сервисов через `typing.Protocol` — use cases тестируются без сети, без OpenCV, без Calibre.

### Поток данных

```
[download_book  →  DownloadResult]   # авто-скачивание сырья в рабочий каталог, если нет кэша
                     ↓
fetch_book  →  FetchedBook(pages, toc, title)   # fetcher = LocalFetcher(workspace)
                     ↓
analyze_structure  →  StructuredDocument(chapters, toc)
                     ↓
[extract_scan_images  →  list[ImageAsset]]      # сканы читаются из workspace.scans_dir
                     ↓
build_epub  →  BuildResult(outputs=[book.epub])
                     ↓
[convert_to_azw3  →  BuildResult(outputs=[book.azw3])]
```

`download`/`clean` (CLI) работают с рабочим каталогом (`BookWorkspace`) напрямую, в обход
остальной цепочки.

### Protocol-интерфейсы (`services/__init__.py`)

| Protocol | Реализация | Внедряется в |
|----------|-----------|--------------|
| `FetcherProtocol` | `Fetcher` (httpx), `LocalFetcher` (кэш workspace) | `fetch_book`, `download_book`, `run_pipeline` |
| `RawFetcherProtocol` | `Fetcher` (сырые ответы) | `download_book`, `run_pipeline` (авто-download) |
| `ParserProtocol` | `Parser` (selectolax) | `fetch_book`, `download_book`, `run_pipeline` |
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
