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
import re
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

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
from biblioatom.services.local_fetcher import LocalFetcher
from biblioatom.services.parser import Parser
from biblioatom.services.progress import RichProgressReporter
from biblioatom.services.scan_extractor import ScanExtractor
from biblioatom.services.source_utils import book_id_from_source
from biblioatom.services.structure_analyzer import StructureAnalyzer
from biblioatom.services.workspace import BookWorkspace
from biblioatom.ui import console, err_console

if TYPE_CHECKING:
    from biblioatom.models import PageModel, TocEntry

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
_QUIET = "quiet"

#: Ведущие цифры в имени файла — используются для натуральной сортировки сканов.
_LEADING_DIGITS_RE = re.compile(r"^(\d+)")


class ChapterMode(StrEnum):
    """Режим определения границ глав при отсутствии TOC."""

    STRICT = "strict"
    NORMAL = "normal"


def _natural_sort_key(path: Path) -> int:
    """Ключ сортировки по ведущему числу в имени файла.

    Для имён вида ``0042_raw.png``, ``page_10.jpg``, ``10.jpeg`` и т.п. возвращает
    первое найденное целое число; если цифр нет — возвращает ``2**31 - 1``, чтобы
    файлы без номера оказались в конце, а не перемешались с нумерованными.
    """
    m = _LEADING_DIGITS_RE.search(path.stem)
    return int(m.group(1)) if m else 2**31 - 1


def _collect_scans(scans_dir: Path) -> list[tuple[int, Path]]:
    """Собрать список ``(page_number, path)`` из каталога со сканами.

    Файлы сортируются по натуральному числу, извлечённому из имени
    (:func:`_natural_sort_key`), а не лексикографически — иначе ``page_10.png``
    оказывается перед ``page_2.png``. Номер страницы берётся из того же числа;
    при его отсутствии используется порядковый номер перебора.
    """
    image_exts = {".png", ".jpg", ".jpeg"}
    candidates = [p for p in scans_dir.glob("*") if p.suffix.lower() in image_exts]
    candidates.sort(key=_natural_sort_key)
    result: list[tuple[int, Path]] = []
    for enum_idx, path in enumerate(candidates):
        m = _LEADING_DIGITS_RE.search(path.stem)
        page = int(m.group(1)) if m else enum_idx
        result.append((page, path))
    return result


def _load_book_from_json(
    path: Path,
) -> tuple[list[PageModel], list[TocEntry], str, str, str | None]:
    """Загрузить pages/toc/title/book_id/source из локального JSON (вывод fetch)."""
    from biblioatom.models import PageModel, TocEntry

    if not path.is_file():
        raise InputValidationError(
            "Input JSON file does not exist.",
            context={"path": str(path)},
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    pages = [PageModel.model_validate(p) for p in data.get("pages", [])]
    toc = [TocEntry.model_validate(t) for t in data.get("toc", [])]
    return (
        pages,
        toc,
        data.get("title", "Untitled"),
        data.get("book_id", ""),
        data.get("source"),
    )


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


def _build_fetcher(settings: Settings) -> tuple[Fetcher, Parser]:
    """Собрать ``Fetcher`` и ``Parser`` из конфигурации.

    Возвращает пару ``(fetcher, parser)``, чтобы один экземпляр ``Parser``
    использовался и внутри ``Fetcher``, и при вызовах use case — без дублирования.
    """
    parser = Parser(settings.parsing)
    fetcher = Fetcher(app=settings.app, http=settings.http, parser=parser)
    return fetcher, parser


def _workspace_for(settings: Settings, book_id: str, work_dir: Path | None) -> BookWorkspace:
    """Собрать BookWorkspace: --work-dir переопределяет settings.app.work_dir."""
    return BookWorkspace(work_dir=work_dir or settings.app.work_dir, book_id=book_id)


def _progress_reporter(*, quiet: bool, verbose: bool) -> RichProgressReporter | None:
    """Rich-прогресс только в «дефолтном» режиме: без ``-v``/``-vv`` и без ``--quiet``.

    С verbose идут структурные логи (живой бар мешал бы им), с quiet — тишина.
    На не-TTY Rich дополнительно отключает отрисовку сам.
    """

    if quiet or verbose:
        return None
    return RichProgressReporter()


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
    ctx.obj[_QUIET] = quiet


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
        from biblioatom.core.fetch_book import book_payload, fetch_book

        book_id = book_id_from_source(source)
        fetcher, parser = _build_fetcher(settings)
        reporter = _progress_reporter(quiet=ctx.obj[_QUIET], verbose=verbose)
        try:
            with reporter if reporter is not None else nullcontext():
                book = fetch_book(
                    fetcher,
                    parser,
                    book_id,
                    from_page=from_page,
                    to_page=to_page,
                    delay_ms=settings.http.delay_ms,
                    progress=reporter,
                )
        finally:
            fetcher.close()

        payload = book_payload(book)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(
            f"[green]✓[/green] Скачано {len(book.pages)} стр. "
            f"(ошибок: {len(book.failed_pages)}) → {output}"
        )


@app.command()
def download(
    ctx: typer.Context,
    source: Annotated[str, typer.Argument(help="Идентификатор книги или URL.")],
    from_page: Annotated[
        int,
        typer.Option("--from-page", help="Первая страница (0-based)."),
    ] = 0,
    to_page: Annotated[
        int | None,
        typer.Option("--to-page", help="Последняя страница (по умолчанию — авто)."),
    ] = None,
    refresh: Annotated[
        bool,
        typer.Option("--refresh", help="Перекачать заново, игнорируя кэш."),
    ] = False,
    work_dir: Annotated[
        Path | None,
        typer.Option("--work-dir", help="Корень рабочих каталогов (default: books)."),
    ] = None,
) -> None:
    """Скачать сырьё книги в рабочий каталог (books/<book_id>/raw/) + book.json."""
    settings: Settings = ctx.obj[_CONFIG]
    verbose: bool = ctx.obj[_VERBOSE]
    with _handle_errors(verbose=verbose):
        from biblioatom.core.download_book import download_book

        book_id = book_id_from_source(source)
        workspace = _workspace_for(settings, book_id, work_dir)
        fetcher, parser = _build_fetcher(settings)
        local = LocalFetcher(workspace, parser=parser)
        reporter = _progress_reporter(quiet=ctx.obj[_QUIET], verbose=verbose)
        try:
            with reporter if reporter is not None else nullcontext():
                result = download_book(
                    fetcher,
                    local,
                    parser,
                    workspace,
                    book_id,
                    from_page=from_page,
                    to_page=to_page,
                    delay_ms=settings.http.delay_ms,
                    refresh=refresh,
                    progress=reporter,
                )
        finally:
            fetcher.close()

        console.print(
            f"[green]✓[/green] [bold]{result.title}[/bold] — "
            f"страниц скачано: {result.pages_downloaded}, пропущено: {result.pages_skipped}, "
            f"сканов: {result.scans_downloaded} "
            f"(ошибок: {len(result.failed_pages) + len(result.failed_scans)})"
        )
        console.print(f"  Каталог → {workspace.root}")


@app.command()
def clean(
    ctx: typer.Context,
    source: Annotated[str, typer.Argument(help="Идентификатор книги или URL.")],
    raw: Annotated[
        bool,
        typer.Option("--raw", help="Удалить весь raw/ (сырьё целиком)."),
    ] = False,
    all_: Annotated[
        bool,
        typer.Option("--all", help="Удалить всё, кроме итогового .epub."),
    ] = False,
    work_dir: Annotated[
        Path | None,
        typer.Option("--work-dir", help="Корень рабочих каталогов (default: books)."),
    ] = None,
) -> None:
    """Очистить кэш книги (по умолчанию — только сырые сканы raw/scans/)."""
    settings: Settings = ctx.obj[_CONFIG]
    verbose: bool = ctx.obj[_VERBOSE]
    with _handle_errors(verbose=verbose):
        from biblioatom.core.clean_workspace import CleanScope, clean_workspace

        if raw and all_:
            raise InputValidationError(
                "Options --raw and --all are mutually exclusive.",
                context={"raw": raw, "all": all_},
            )
        scope = CleanScope.ALL if all_ else CleanScope.RAW if raw else CleanScope.SCANS
        book_id = book_id_from_source(source)
        workspace = _workspace_for(settings, book_id, work_dir)
        result = clean_workspace(workspace, scope)

        freed_mb = result.freed_bytes / (1024 * 1024)
        console.print(
            f"[green]✓[/green] Удалено объектов: {len(result.removed)}, "
            f"освобождено: {freed_mb:.1f} МБ ({scope})"
        )


@app.command()
def analyze(
    ctx: typer.Context,
    source: Annotated[
        str,
        typer.Argument(help="Идентификатор книги, URL или путь к локальному JSON (вывод fetch)."),
    ],
    chapter_mode: Annotated[
        ChapterMode,
        typer.Option("--chapter-mode", help="Режим разбивки на главы без TOC."),
    ] = ChapterMode.STRICT,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Вывести структуру документа в JSON."),
    ] = False,
) -> None:
    """Проанализировать структуру книги (главы/TOC).

    SOURCE может быть:
    - идентификатором книги (``kapitsa_1994``) или URL — книга скачивается;
    - путём к локальному ``.json``-файлу (вывод команды ``fetch``) — сеть не используется.
    """
    settings: Settings = ctx.obj[_CONFIG]
    verbose: bool = ctx.obj[_VERBOSE]
    with _handle_errors(verbose=verbose):
        from biblioatom.core.analyze_structure import analyze_structure

        local_path = Path(source)
        if local_path.suffix.lower() == ".json" or local_path.is_file():
            # Локальный JSON-файл — не идём в сеть.
            pages, toc, title, book_id, file_source = _load_book_from_json(local_path)
            resolved_source = file_source or source
        else:
            # Идентификатор или URL — скачиваем.
            from biblioatom.core.fetch_book import fetch_book

            book_id = book_id_from_source(source)
            fetcher, parser = _build_fetcher(settings)
            try:
                book = fetch_book(
                    fetcher,
                    parser,
                    book_id,
                    delay_ms=settings.http.delay_ms,
                )
            finally:
                fetcher.close()
            pages = book.pages
            toc = book.toc
            title = book.title
            book_id = book.book_id
            resolved_source = source

        document = analyze_structure(
            StructureAnalyzer(chapter_mode.value),
            pages,
            toc,
            title=title,
            book_id=book_id,
            source=resolved_source,
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

        scans = _collect_scans(scans_dir)

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

        pages, toc, title, book_id, file_source = _load_book_from_json(input_json)
        document = analyze_structure(
            StructureAnalyzer(chapter_mode.value),
            pages,
            toc,
            title=title,
            book_id=book_id,
            source=file_source,
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
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Путь итогового .epub (default: books/<book_id>/<book_id>.epub).",
        ),
    ] = None,
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
    refresh: Annotated[
        bool,
        typer.Option("--refresh", help="Перекачать сырьё заново, игнорируя кэш."),
    ] = False,
    work_dir: Annotated[
        Path | None,
        typer.Option("--work-dir", help="Корень рабочих каталогов (default: books)."),
    ] = None,
) -> None:
    """Полный пайплайн: [download при отсутствии кэша] → анализ → (сканы) → EPUB → (AZW3)."""
    settings: Settings = ctx.obj[_CONFIG]
    verbose: bool = ctx.obj[_VERBOSE]
    with _handle_errors(verbose=verbose):
        from biblioatom.core.run_pipeline import run_pipeline

        book_id = book_id_from_source(source)
        workspace = _workspace_for(settings, book_id, work_dir)
        network, parser = _build_fetcher(settings)
        local = LocalFetcher(workspace, parser=parser)
        reporter = _progress_reporter(quiet=ctx.obj[_QUIET], verbose=verbose)
        try:
            with reporter if reporter is not None else nullcontext():
                result = run_pipeline(
                    fetcher=local,
                    network_fetcher=network,
                    parser=parser,
                    analyzer=StructureAnalyzer(chapter_mode.value),
                    epub_builder=EpubBuilder(settings.epub),
                    workspace=workspace,
                    book_id=book_id,
                    out_path=output,
                    refresh=refresh,
                    source=source,
                    from_page=from_page,
                    to_page=to_page,
                    delay_ms=settings.http.delay_ms,
                    extract_images=images,
                    scan_extractor=ScanExtractor(settings.scan_extraction) if images else None,
                    image_processor=ImageProcessor(settings.image) if images else None,
                    convert_azw3=azw3,
                    converter=EbookConvertConverter(settings.conversion) if azw3 else None,
                    progress=reporter,
                )
        finally:
            network.close()

        console.print(
            f"[green]✓[/green] [bold]{result.title}[/bold] — "
            f"глав: {result.chapters}, иллюстраций: {len(result.images)}"
        )
        console.print(f"  EPUB → {result.epub_path}")
        if result.azw3_path is not None:
            console.print(f"  AZW3 → {result.azw3_path}")


@app.command(name="import-ris")
def import_ris(
    ctx: typer.Context,
    ris_file: Annotated[
        Path,
        typer.Argument(help="Путь к RIS-файлу."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Путь итогового JSON."),
    ] = Path("ris_import.json"),
) -> None:
    """Импортировать библиографические записи из RIS-файла."""
    verbose: bool = ctx.obj[_VERBOSE]
    with _handle_errors(verbose=verbose):
        from biblioatom.services.ris_parser import parse_ris_file

        entries = parse_ris_file(ris_file)

        payload = {
            "source": str(ris_file),
            "count": len(entries),
            "entries": [entry.model_dump(by_alias=True) for entry in entries],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[green]✓[/green] Импортировано {len(entries)} записей → {output}")


@app.command(name="export-ris")
def export_ris(
    ctx: typer.Context,
    input_json: Annotated[
        Path,
        typer.Argument(help="JSON с оглавлением книги (вывод команды fetch)."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Путь итогового RIS-файла."),
    ] = Path("export.ris"),
) -> None:
    """Экспортировать оглавление книги в формат RIS."""
    verbose: bool = ctx.obj[_VERBOSE]
    with _handle_errors(verbose=verbose):
        from biblioatom.services.ris_parser import toc_to_ris

        pages, toc, title, book_id, _ = _load_book_from_json(input_json)
        ris_text = toc_to_ris(toc, title=title)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(ris_text, encoding="utf-8")
        console.print(f"[green]✓[/green] Экспортировано {len(toc)} записей TOC → {output}")


def main_entry() -> None:
    """Точка входа консольного скрипта ``biblioatom`` (см. ``pyproject.toml``)."""
    app()


__all__ = ["ExitCode", "app", "main_entry"]
