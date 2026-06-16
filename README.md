# biblioatom

CLI для скачивания книг с [elib.biblioatom.ru](https://elib.biblioatom.ru) и сборки из них EPUB3 (опционально — AZW3). Книги доступны без авторизации через публичный RPC-эндпоинт.

Проект построен на современном стеке: Typer, Pydantic v2, httpx, selectolax, OpenCV/Pillow, EbookLib, Calibre, structlog, tenacity, mypy (strict).

## Возможности

- Загрузка страниц и оглавления книги по идентификатору или URL.
- Структурный анализ: разбивка на главы по TOC сайта (или эвристикой при его отсутствии).
- Сборка валидного **EPUB 3** (nav, spine, CSS, рабочие якоря сносок, встраивание иллюстраций как `<figure>`).
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
uv sync

# Запускать команды через uv
uv run biblioatom --help
```

Для установки как глобального инструмента:

```bash
uv tool install .
biblioatom --help
```

## Команды

```bash
# Скачать книгу в JSON (страницы + оглавление)
uv run biblioatom fetch kapitsa_1994 -o book.json
uv run biblioatom fetch https://elib.biblioatom.ru/text/kapitsa_1994/ -o book.json

# Проанализировать структуру (главы/TOC); --json — машиночитаемый вывод
uv run biblioatom analyze kapitsa_1994
uv run biblioatom analyze kapitsa_1994 --json

# Извлечь иллюстрации из локальных сканов (PNG/JPEG)
uv run biblioatom extract-scans ./scans -o ./images

# Собрать EPUB3 из ранее скачанного JSON
uv run biblioatom build book.json -o book.epub

# Сконвертировать EPUB в AZW3 (нужен Calibre)
uv run biblioatom convert book.epub -o book.azw3

# Полный пайплайн: загрузка → анализ → (сканы) → EPUB → (AZW3)
uv run biblioatom pipeline kapitsa_1994 -o book.epub
uv run biblioatom pipeline kapitsa_1994 --images --azw3 -o book.epub
```

`SOURCE` — идентификатор книги (например `kapitsa_1994`) или её URL. Идентификатор соответствует сегменту после `/text/` в адресе книги.

## Глобальные опции

| Флаг | Описание |
|------|----------|
| `-V, --version` | Показать версию и выйти |
| `-v, --verbose` | Подробность: `-v` → INFO, `-vv` → DEBUG (показывает traceback при ошибке) |
| `-q, --quiet` | Минимальный вывод (только ошибки) |
| `-c, --config PATH` | Путь к `.env`-файлу с настройками |

## Коды завершения

Доменные ошибки централизованно мапятся в стабильные коды возврата:

| Код | Категория |
|-----|-----------|
| `0` | Успех |
| `2` | Ошибка конфигурации |
| `3` | Невалидный ввод (диапазон страниц, путь и т.п.) |
| `4` | Сетевой сбой (загрузка) |
| `5` | Ошибка разбора (HTML/JSON) |
| `6` | Ошибка структурного анализа |
| `7` | Ошибка извлечения/обработки изображений |
| `8` | Ошибка сборки EPUB |
| `10` | Сбой внешнего инструмента (`ebook-convert`) |
| `130` | Прерывание пользователем (Ctrl+C) |

Traceback показывается только в verbose/DEBUG-режиме; иначе пользователь получает понятное сообщение в stderr и корректный код возврата.

## Конфигурация

Настройки читаются из переменных окружения с префиксом `BIBLIOATOM_` (вложенные группы — через `__`) либо из `.env`. Примеры:

```bash
BIBLIOATOM_HTTP__TIMEOUT=60
BIBLIOATOM_HTTP__MAX_RETRIES=5
BIBLIOATOM_LOGGING__LEVEL=DEBUG
BIBLIOATOM_CONVERSION__EBOOK_CONVERT_BIN=/opt/calibre/ebook-convert
```

Группы настроек: `app`, `http`, `parsing`, `structure`, `scan_extraction`, `image`, `epub`, `conversion`, `logging`.

## Shell completion

Typer поддерживает автодополнение для bash/zsh/fish/PowerShell:

```bash
# Установить автодополнение для текущего shell
uv run biblioatom --install-completion

# Показать скрипт без установки
uv run biblioatom --show-completion
```

## Архитектура

Многослойная архитектура (Thin CLI / Fat Core, SoC, SRP, Dependency Inversion):

```
src/biblioatom/
├── cli.py          — тонкий слой Typer: парсинг → core → вывод → ExitCode
├── ui.py           — Rich Console (stdout) и err_console (stderr)
├── config.py       — pydantic-settings (группы настроек)
├── errors.py       — иерархия BookgrabError + ExitCode
├── logging_config.py — structlog (correlation_id, редакция секретов)
├── models.py       — доменные модели Pydantic
├── core/           — use cases (оркестрация, без I/O-деталей)
│   ├── fetch_book.py
│   ├── analyze_structure.py
│   ├── extract_scan_images.py
│   ├── build_epub.py
│   ├── convert_to_azw3.py
│   └── run_pipeline.py
└── services/       — реализации, внедряемые через typing.Protocol
    ├── fetcher.py        — httpx + tenacity
    ├── parser.py         — selectolax
    ├── structure_analyzer.py
    ├── html_cleaner.py
    ├── scan_extractor.py — OpenCV
    ├── image_processor.py — Pillow
    ├── epub_builder.py   — EbookLib
    └── converter.py      — subprocess ebook-convert
```

**Принцип:** CLI не содержит бизнес-логики — только парсит аргументы, собирает зависимости-сервисы, вызывает use case и форматирует результат. Core-слой зависит от сервисов через `typing.Protocol` (Dependency Inversion), поэтому логику можно тестировать без сети и переиспользовать вне CLI.

## Как расширять

- **Новый источник данных** — реализуйте `FetcherProtocol` (см. `services/__init__.py`) и передайте его в use case.
- **Новый выходной формат** — реализуйте `ConverterProtocol` или `EpubBuilderProtocol`.
- **Новая команда CLI** — добавьте `@app.command()` в `cli.py`: соберите сервисы из `ctx.obj["config"]`, вызовите соответствующий use case под `_handle_errors`.

## Разработка

```bash
uv sync
uv run ruff check
uv run ruff format --check
uv run mypy --strict src/
uv run pytest
```
