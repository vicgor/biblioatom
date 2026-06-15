# biblioatom

Python CLI для скачивания и конвертации книг с [elib.biblioatom.ru](https://elib.biblioatom.ru) через публичный RPC-эндпоинт. Книги доступны без авторизации.

## Установка

```bash
pip install -e .
```

Требует Python 3.9+. Зависимостей от сторонних пакетов нет — только стандартная библиотека.

## Использование

```bash
# Скачать книгу и сохранить как EPUB + JSON
biblioatom kapitsa_1994 -o output/

# Скачать с иллюстрациями (JPG встроены в EPUB)
biblioatom kapitsa_1994 --images -o output/

# Выбрать форматы
biblioatom kapitsa_1994 -f epub,fb2,html,txt,json -o output/

# Скачать только часть книги
biblioatom kapitsa_1994 --from-page 10 --to-page 50 -o output/

# Конвертировать из ранее сохранённого JSON
biblioatom --from-json export.json -f epub,fb2 -o output/
biblioatom --from-json export.json --images -f epub -o output/
```

`BOOK_ID` — последний сегмент URL книги, например для `https://elib.biblioatom.ru/text/kapitsa_1994/` это `kapitsa_1994`.

## Опции

| Флаг | По умолчанию | Описание |
|------|-------------|----------|
| `BOOK_ID` | — | Идентификатор книги |
| `--from-json FILE` | — | Конвертировать из JSON вместо скачивания |
| `-f, --formats` | `epub,json` | Форматы через запятую: `epub`, `fb2`, `html`, `txt`, `json` |
| `-o, --outdir DIR` | `.` | Папка для результатов |
| `--images` | выкл. | Скачать JPG иллюстраций и встроить в EPUB |
| `--from-page N` | `0` | Первая страница |
| `--to-page N` | авто | Последняя страница |
| `--delay MS` | `300` | Задержка между запросами в мс |
| `--prefix STR` | — | Префикс имени выходного файла |
| `--chapter-mode` | `strict` | Режим разбивки на главы: `strict` или `normal` |

## Выходные форматы

- **`epub`** — EPUB 3 с оглавлением (NCX + nav.xhtml), разбивкой по главам из TOC сайта, CSS-стилями. При `--images` JPG-иллюстрации встроены как `<figure><img/><figcaption/>`.
- **`fb2`** — FictionBook 2.0 с секциями по главам.
- **`html`** — единый HTML-файл.
- **`txt`** — простой текст с постраничными маркерами `PAGE N`.
- **`json`** — сырые данные (title, book_id, toc, items с pagetext/pagehtml) для последующей конвертации.

## Имена файлов

Файлы именуются автоматически по шаблону:  
`{title_slug}_{book_id}_{from_page}-{to_page}.{ext}`

При `--images` JPG сохраняются в `outdir/images/{page:04d}_{caption_slug}.jpg`.

## JSON-формат

Сохранённый `--from-json` файл совместим с оригинальным `biblioatom-extractor/convert_book.py`. Поле `content` может быть dict или JSON-строкой — оба варианта поддерживаются.

```json
{
  "title": "...",
  "book_id": "kapitsa_1994",
  "source": "https://elib.biblioatom.ru/text/kapitsa_1994/",
  "page_range": [0, 545],
  "generated_at": "2026-06-14T12:00:00",
  "toc": [{"title": "...", "author": "", "page": 6, "print_page": 5, "level": 0}],
  "items": [{"page": 0, "content": {"valid": true, "pagetext": "...", "pagehtml": "..."}}]
}
```
