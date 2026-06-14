import argparse
import datetime
import json
import sys
from pathlib import Path

from biblioatom import convert, fetch


def parse_args():
    parser = argparse.ArgumentParser(
        prog="biblioatom",
        description="Скачать и конвертировать книгу с elib.biblioatom.ru",
    )

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("book_id", nargs="?", metavar="BOOK_ID",
                        help="Идентификатор книги (например, kapitsa_1994)")
    source.add_argument("--from-json", "-j", metavar="FILE",
                        help="Конвертировать существующий JSON без скачивания")

    parser.add_argument("--from-page", type=int, default=0, metavar="N",
                        help="Первая страница (по умолчанию: 0)")
    parser.add_argument("--to-page", type=int, default=None, metavar="N",
                        help="Последняя страница (по умолчанию: авто)")

    parser.add_argument("--formats", "-f", default="epub,json",
                        help="Форматы через запятую: epub,fb2,html,txt,json (по умолчанию: epub,json)")
    parser.add_argument("--outdir", "-o", default=".", metavar="DIR",
                        help="Папка для результатов (по умолчанию: .)")
    parser.add_argument("--prefix", default="",
                        help="Префикс имени файла")
    parser.add_argument("--chapter-mode", choices=["strict", "normal"], default="strict",
                        help="Режим определения глав (по умолчанию: strict)")
    parser.add_argument("--delay", type=int, default=300, metavar="MS",
                        help="Задержка между запросами в мс (по умолчанию: 300)")

    return parser.parse_args()


def _progress(done, total, page_no):
    pct = int((done + 1) / total * 100) if total else 0
    print(f"\r  [{pct:3d}%] стр. {page_no} ({done + 1}/{total})", end="", flush=True)


def main():
    args = parse_args()
    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]

    unknown = set(formats) - convert.FORMATS
    if unknown:
        sys.exit(f"Неизвестные форматы: {', '.join(sorted(unknown))}")

    if args.from_json:
        # Convert-only mode
        src_path = Path(args.from_json)
        if not src_path.exists():
            sys.exit(f"Файл не найден: {src_path}")
        print(f"Читаю JSON: {src_path}")
        with open(src_path, encoding="utf-8") as f:
            src = json.load(f)
    else:
        # Download + convert
        book_id = args.book_id
        print(f"Получаю метаданные книги: {book_id}")
        title, max_page = fetch.fetch_book_meta(book_id)
        print(f"Название: {title}")
        print("Получаю оглавление...")
        toc = fetch.fetch_toc(book_id)
        print(f"Оглавление: {len(toc)} записей" if toc else "Оглавление не найдено")

        from_page = args.from_page
        to_page = args.to_page if args.to_page is not None else max_page
        print(f"Диапазон страниц: {from_page}–{to_page}  (задержка: {args.delay} мс)")

        items = fetch.download_book(
            book_id,
            from_page,
            to_page,
            delay_ms=args.delay,
            progress_cb=_progress,
        )
        print()  # завершить строку прогресса

        ok_count = sum(1 for it in items if "error" not in it)
        err_count = len(items) - ok_count
        print(f"Скачано: {ok_count} стр., ошибок: {err_count}")

        src = {
            "title": title,
            "book_id": book_id,
            "source": f"https://elib.biblioatom.ru/text/{book_id}/",
            "page_range": [from_page, to_page],
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "toc": toc,
            "items": items,
        }

    print(f"\nКонвертирую форматы: {', '.join(formats)}")
    written = convert.build_book(
        src,
        formats=formats,
        outdir=args.outdir,
        prefix=args.prefix,
        chapter_mode=args.chapter_mode,
    )

    for path in written:
        print(f"  → {path}")

    print("Готово.")
