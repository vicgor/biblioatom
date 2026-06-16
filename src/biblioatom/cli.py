"""Тонкий CLI-слой на Typer.

CLI только парсит аргументы, собирает зависимости-сервисы, вызывает
соответствующий core use case, форматирует вывод и мапит доменные ошибки в
:class:`~biblioatom.errors.ExitCode`. Бизнес-логики здесь нет — она в ``core/``.

Глобальные опции (``--verbose``/``--quiet``/``--version``/``--config``) задаются в
:func:`main`-callback и кладут собранную конфигурацию в ``ctx.obj`` для подкоманд.
Traceback показывается только в verbose/DEBUG-режиме; иначе пользователь получает
понятное сообщение в stderr и корректный код возврата.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from biblioatom import __version__
from biblioatom.config import Settings, get_settings
from biblioatom.errors import (
    BookgrabError,
    ExitCode,
    InputValidationError,
    exit_code_for,
)
from biblioatom.logging_config import get_logger, setup_logging
from biblioatom.services.converter import EbookConvertConverter
from biblioatom.services.epub_builder import EpubBuilder
from biblioatom.services.fetcher import Fetcher
from biblioatom.services.image_processor import ImageProcessor
from biblioatom.services.parser import Parser
from biblioatom.services.scan_extractor import ScanExtractor
from biblioatom.services.structure_analyzer import StructureAnalyzer
from biblioatom.ui import console, err_console

app = typer.Typer(
    name="biblioatom",
    help="Скачивание и конвертация книг с elib.biblioatom.ru.",
    rich_markup_mode="rich",
    no_args_is_help=True,
    add_completion=True,
)

#: Ключи ``ctx.obj`` (избегаем «магических строк» в подкомандах).
_CONFIG = "config"
_VERBOSE = "verbose"


class ChapterMode(StrEnum):
    """Режим определения границ глав при отсутствии TOC."""

    STRICT = "strict"
    NORMAL = "normal"


def _book_id_from_source(source: str) -> str:
    """Извлечь идентификатор книги из URL или вернуть строку как есть.

    Поддерживаются формы ``kapitsa_1994`` и
    ``https://elib.biblioatom.ru/text/kapitsa_1994/...``.
    """

    cleaned = source.strip().rstrip("/")
    if "/text/" in cleaned:
        tail = cleaned.split("/text/", 1)[1]
        candidate = tail.split("/", 1)[0]
        if candidate:
            return candidate
    if "/" in cleaned or not cleaned:
        raise InputValidationError(
            "Could not derive a book id from the given source.",
            context={"source": source},
        )
    return cleaned


@contextmanager
def _handle_errors(*, verbose: bool) -> Iterator[None]:
    """Централизованно поймать доменные ошибки и завершить с нужным ExitCode.

    В verbose/DEBUG-режиме исключение пробрасывается (Typer покажет traceback);
    иначе печатается понятное сообщение в stderr, а процесс завершается кодом из
    :func:`exit_code_for`. ``KeyboardInterrupt`` → код 130.
    """

    try:
        yield
    except KeyboardInterrupt:
        err_console.print("\n[yellow]Прервано пользователем.[/yellow]")
        raise typer.Exit(code=130) from None
    except BookgrabError as exc:
        code = exit_code_for(exc)
        # Логгер берём лениво (а не на уровне модуля): structlog кэширует logger
        # при первом использовании, поэтому свежий вызов после setup_logging
        # привязывается к актуальному потоку вывода.
        get_logger(__name__).error("cli.command_failed", error=str(exc), exit_code=int(code))
        if verbose:
            raise
        err_console.print(f"[red]Ошибка:[/red] {exc.message}")
        raise typer.Exit(code=int(code)) from exc


def _load_settings(config_file: Path | None) -> Settings:
    """Загрузить настройки из ``.env`` (опционально) или из окружения."""

    if config_file is not None:
        return Settings(_env_file=str(config_file))  # type: ignore[call-arg]
    return get_settings()


def _build_fetcher(settings: Settings) -> Fetcher:
    """Собрать ``Fetcher`` с парсером и HTTP-настройками из конфигурации."""

    parser = Parser(settings.parsing)
    return Fetcher(app=settings.app, http=settings.http, parser=parser)


def _version_callback(value: bool) -> None:
    """Вывести версию и выйти."""

    if value:
        console.print(f"biblioatom [bold]{__version__}[/bold]")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Показать версию и выйти.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            help="Подробность вывода: -v INFO, -vv DEBUG.",
        ),
    ] = 0,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Минимальный вывод (только ошибки)."),
    ] = False,
    config_file: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Путь к .env-файлу с настройками."),
    ] = None,
) -> None:
    """Глобальные опции, применяемые ко всем подкомандам."""

    if quiet:
        level = "WARNING"
    elif verbose >= 2:
        level = "DEBUG"
    elif verbose == 1:
        level = "INFO"
    else:
        level = "WARNING"

    setup_logging(level=level)

    ctx.ensure_object(dict)
    ctx.obj[_CONFIG] = _load_settings(config_file)
    ctx.obj[_VERBOSE] = verbose >= 1


@app.command()
def fetch(
    ctx: typer.Context,
    source: Annotated[str, typer.Argument(help="Идентификатор книги или URL.")],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Путь итогового JSON."),
    ] = Path("book.json"),
    from_page: Annotated[
        int,
        typer.Option("--from-page", help="Первая страница (0-based)."),
    ] = 0,
    to_page: Annotated[
        int | None,
        typer.Option("--to-page", help="Последняя страница (по умолчанию — авто)."),
    ] = None,
) -> None:
    """Скачать книгу и сохранить страницы с оглавлением в JSON."""

    settings: Settings = ctx.obj[_CONFIG]
    verbose: bool = ctx.obj[_VERBOSE]
    with _handle_errors(verbose=verbose):
        from biblioatom.core.fetch_book import fetch_book

        book_id = _book_id_from_source(source)
        fetcher = _build_fetcher(settings)
        try:
            book = fetch_book(
                fetcher,
                Parser(settings.parsing),
                book_id,
                from_page=from_page,
                to_page=to_page,
                delay_ms=settings.http.delay_ms,
            )
        finally:
            fetcher.close()

        payload = {
            "title": book.title,
            "book_id": book.book_id,
            "max_page": book.max_page,
            "toc": [entry.model_dump() for entry in book.toc],
            "pages": [page.model_dump() for page in book.pages],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(
            f"[green]✓[/green] Скачано {len(book.pages)} стр. "
            f"(ошибок: {len(book.failed_pages)}) → {output}"
        )


@app.command()
def analyze(
    ctx: typer.Context,
    source: Annotated[str, typer.Argument(help="Идентификатор книги или URL.")],
    chapter_mode: Annotated[
        ChapterMode,
        typer.Option("--chapter-mode", help="Режим разбивки на главы без TOC."),
    ] = ChapterMode.STRICT,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Вывести структуру документа в JSON."),
    ] = False,
) -> None:
    """Скачать книгу и проанализировать её структуру (главы/TOC)."""

    settings: Settings = ctx.obj[_CONFIG]
    verbose: bool = ctx.obj[_VERBOSE]
    with _handle_errors(verbose=verbose):
        from biblioatom.core.analyze_structure import analyze_structure
        from biblioatom.core.fetch_book import fetch_book

        book_id = _book_id_from_source(source)
        fetcher = _build_fetcher(settings)
        try:
            book = fetch_book(
                fetcher,
                Parser(settings.parsing),
                book_id,
                delay_ms=settings.http.delay_ms,
            )
        finally:
            fetcher.close()

        document = analyze_structure(
            StructureAnalyzer(chapter_mode.value),
            book.pages,
            book.toc,
            title=book.title,
            book_id=book.book_id,
            source=source,
        )

        if as_json:
            console.print_json(document.model_dump_json())
            return
        console.print(f"[bold]{document.title}[/bold] ({document.book_id})")
        console.print(f"Глав: {len(document.chapters)}, записей TOC: {len(document.toc)}")
        for index, chapter in enumerate(document.chapters, start=1):
            console.print(f"  {index:>3}. {chapter.title} — стр. {len(chapter.pages)}")


@app.command(name="extract-scans")
def extract_scans(
    ctx: typer.Context,
    scans_dir: Annotated[
        Path,
        typer.Argument(help="Каталог со сканами страниц (PNG/JPEG)."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Каталог для извлечённых иллюстраций."),
    ] = Path("images"),
) -> None:
    """Извлечь иллюстрации из локальных сканов средствами OpenCV/Pillow."""

    settings: Settings = ctx.obj[_CONFIG]
    verbose: bool = ctx.obj[_VERBOSE]
    with _handle_errors(verbose=verbose):
        from biblioatom.core.extract_scan_images import extract_scan_images

        if not scans_dir.is_dir():
            raise InputValidationError(
                "Scans directory does not exist.",
                context={"path": str(scans_dir)},
            )

        scans: list[tuple[int, Path]] = []
        for page, path in enumerate(sorted(scans_dir.glob("*"))):
            if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                scans.append((page, path))

        output.mkdir(parents=True, exist_ok=True)
        result = extract_scan_images(
            ScanExtractor(settings.scan_extraction),
            ImageProcessor(settings.image),
            scans,
            output,
        )
        console.print(
            f"[green]✓[/green] Извлечено {len(result.images)} иллюстраций "
            f"(сбойных сканов: {len(result.failed_scans)}) → {output}"
        )


@app.command()
def build(
    ctx: typer.Context,
    input_json: Annotated[
        Path,
        typer.Argument(help="JSON со страницами книги (вывод команды fetch)."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Путь итогового .epub."),
    ] = Path("book.epub"),
    chapter_mode: Annotated[
        ChapterMode,
        typer.Option("--chapter-mode", help="Режим разбивки на главы без TOC."),
    ] = ChapterMode.STRICT,
) -> None:
    """Собрать EPUB3 из ранее скачанного JSON."""

    settings: Settings = ctx.obj[_CONFIG]
    verbose: bool = ctx.obj[_VERBOSE]
    with _handle_errors(verbose=verbose):
        from biblioatom.core.analyze_structure import analyze_structure
        from biblioatom.core.build_epub import build_epub
        from biblioatom.models import PageModel, TocEntry

        if not input_json.is_file():
            raise InputValidationError(
                "Input JSON file does not exist.",
                context={"path": str(input_json)},
            )

        data = json.loads(input_json.read_text(encoding="utf-8"))
        pages = [PageModel.model_validate(p) for p in data.get("pages", [])]
        toc = [TocEntry.model_validate(t) for t in data.get("toc", [])]

        document = analyze_structure(
            StructureAnalyzer(chapter_mode.value),
            pages,
            toc,
            title=data.get("title", "Untitled"),
            book_id=data.get("book_id", ""),
            source=data.get("source"),
        )
        result = build_epub(EpubBuilder(settings.epub), document, output)
        for path in result.outputs:
            console.print(f"[green]✓[/green] EPUB → {path}")


@app.command()
def convert(
    ctx: typer.Context,
    source: Annotated[
        Path,
        typer.Argument(help="Исходный .epub."),
    ],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Путь .azw3 (по умолчанию — рядом)."),
    ] = None,
) -> None:
    """Сконвертировать EPUB в AZW3 через Calibre (ebook-convert)."""

    settings: Settings = ctx.obj[_CONFIG]
    verbose: bool = ctx.obj[_VERBOSE]
    with _handle_errors(verbose=verbose):
        from biblioatom.core.convert_to_azw3 import convert_to_azw3

        if not source.is_file():
            raise InputValidationError(
                "Source EPUB file does not exist.",
                context={"path": str(source)},
            )

        target = output or source.with_suffix(".azw3")
        result = convert_to_azw3(
            EbookConvertConverter(settings.conversion),
            source,
            target,
        )
        for path in result.outputs:
            console.print(f"[green]✓[/green] AZW3 → {path}")


@app.command()
def pipeline(
    ctx: typer.Context,
    source: Annotated[str, typer.Argument(help="Идентификатор книги или URL.")],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Путь итогового .epub."),
    ] = Path("book.epub"),
    from_page: Annotated[
        int,
        typer.Option("--from-page", help="Первая страница (0-based)."),
    ] = 0,
    to_page: Annotated[
        int | None,
        typer.Option("--to-page", help="Последняя страница (по умолчанию — авто)."),
    ] = None,
    chapter_mode: Annotated[
        ChapterMode,
        typer.Option("--chapter-mode", help="Режим разбивки на главы без TOC."),
    ] = ChapterMode.STRICT,
    images: Annotated[
        bool,
        typer.Option("--images", help="Извлекать иллюстрации со сканов."),
    ] = False,
    azw3: Annotated[
        bool,
        typer.Option("--azw3", help="Дополнительно собрать AZW3 (нужен Calibre)."),
    ] = False,
) -> None:
    """Полный пайплайн: загрузка → анализ → (сканы) → EPUB → (AZW3)."""

    settings: Settings = ctx.obj[_CONFIG]
    verbose: bool = ctx.obj[_VERBOSE]
    with _handle_errors(verbose=verbose):
        from biblioatom.core.run_pipeline import run_pipeline

        book_id = _book_id_from_source(source)
        fetcher = _build_fetcher(settings)
        try:
            result = run_pipeline(
                fetcher=fetcher,
                parser=Parser(settings.parsing),
                analyzer=StructureAnalyzer(chapter_mode.value),
                epub_builder=EpubBuilder(settings.epub),
                book_id=book_id,
                out_path=output,
                source=source,
                from_page=from_page,
                to_page=to_page,
                delay_ms=settings.http.delay_ms,
                extract_images=images,
                scan_extractor=ScanExtractor(settings.scan_extraction) if images else None,
                image_processor=ImageProcessor(settings.image) if images else None,
                convert_azw3=azw3,
                converter=EbookConvertConverter(settings.conversion) if azw3 else None,
            )
        finally:
            fetcher.close()

        console.print(
            f"[green]✓[/green] [bold]{result.title}[/bold] — "
            f"глав: {result.chapters}, иллюстраций: {len(result.images)}"
        )
        console.print(f"  EPUB → {result.epub_path}")
        if result.azw3_path is not None:
            console.print(f"  AZW3 → {result.azw3_path}")


def main_entry() -> None:
    """Точка входа консольного скрипта ``biblioatom`` (см. ``pyproject.toml``)."""

    app()


__all__ = ["ExitCode", "app", "main_entry"]
