import argparse
import datetime
import json
import sys
import time
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
    parser.add_argument("--images", action="store_true",
                        help="Скачать JPG для страниц с иллюстрациями в outdir/images/")

    return parser.parse_args()


def _progress(done, total, page_no):
    pct = int((done + 1) / total * 100) if total else 0
    print(f"\r  [{pct:3d}%] стр. {page_no} ({done + 1}/{total})", end="", flush=True)


def _download_images(src, outdir, delay_ms):
    book_id = src.get("book_id", "")
    if not book_id:
        print("Нет book_id в данных — пропускаю изображения")
        return

    pages = convert.build_book_models(src)
    photo_pages = []
    for pg in pages:
        captions = [b["text"] for b in pg["blocks"] if b["type"] == "image-caption"]
        if captions:
            photo_pages.append((pg["page"], captions[0]))

    if not photo_pages:
        print("Страниц с иллюстрациями не найдено")
        return

    img_dir = Path(outdir) / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    print(f"Скачиваю иллюстрации: {len(photo_pages)} стр. → {img_dir}/")

    ok = err = 0
    for i, (page_no, caption) in enumerate(photo_pages):
        pct = int((i + 1) / len(photo_pages) * 100)
        print(f"\r  [{pct:3d}%] стр. {page_no} ({i + 1}/{len(photo_pages)})", end="", flush=True)

        data = fetch.fetch_image(book_id, page_no)
        if data:
            slug = convert.slugify(caption)[:60].rstrip("_")
            fname = img_dir / f"{page_no:04d}_{slug}.jpg"
            fname.write_bytes(data)
            ok += 1
        else:
            err += 1

        if delay_ms > 0 and i < len(photo_pages) - 1:
            time.sleep(delay_ms / 1000.0)

    print()
    print(f"  Сохранено: {ok} файлов, ошибок: {err}")


def main():
    args = parse_args()
    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]

    unknown = set(formats) - convert.FORMATS
    if unknown:
        sys.exit(f"Неизвестные форматы: {', '.join(sorted(unknown))}")

    if args.from_json:
        src_path = Path(args.from_json)
        if not src_path.exists():
            sys.exit(f"Файл не найден: {src_path}")
        print(f"Читаю JSON: {src_path}")
        with open(src_path, encoding="utf-8") as f:
            src = json.load(f)
    else:
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
        print()

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

    if args.images:
        print()
        _download_images(src, args.outdir, args.delay)

    print("Готово.")
