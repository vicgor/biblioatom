"""Сборка EPUB 3 из структурированного документа через EbookLib.

Реализует :class:`~biblioatom.services.EpubBuilderProtocol`. Концепция структуры
(главы → XHTML-секции, TOC, встраивание изображений, дедупликация манифеста)
перенесена из legacy ``convert.build_epub``; разметку и упаковку ZIP теперь
строит EbookLib, а не ручной ``zipfile``.

Исправленные при переносе low-баги ревью (см. план миграции, раздел 5):

* Генерируется именно **EPUB 3** (``EpubBook`` версии 3.0 с корректным
  nav-документом ``properties="nav"`` и ``<nav epub:type="toc">``), а не EPUB 2 с
  осиротевшим ``nav.xhtml``. nav включён в spine.
* **Рабочие двусторонние якоря сносок**: маркер-ссылка
  ``<a id="ref_N" href="#fn_N" epub:type="noteref">`` в тексте и обратная ссылка
  из ``<aside id="fn_N" epub:type="footnote">…<a href="#ref_N">↩</a></aside>``.
* Изображения оформляются как ``<figure><img/><figcaption/></figure>``; подпись
  берётся из блока ``BookElement(kind=CAPTION)``.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from ebooklib import epub

from biblioatom.config import EpubSettings
from biblioatom.errors import EpubBuildError
from biblioatom.models import (
    BookElement,
    BuildResult,
    ElementKind,
    ImageAsset,
    StructuredChapter,
    StructuredDocument,
)

# CSS по умолчанию, если в EpubSettings.css не задан собственный стиль.
_DEFAULT_CSS = (
    "body{font-family:serif;line-height:1.5;}"
    "h1,h2{margin:1em 0 .5em;}"
    "p{margin:0 0 .8em;white-space:pre-wrap;}"
    ".footnotes{margin-top:2em;border-top:1px solid #ccc;padding-top:1em;font-size:.92em;}"
    ".chapter-subtitle{font-style:italic;color:#444;}"
    "figure{margin:1.2em 0;text-align:center;}"
    "figure img{max-width:100%;height:auto;}"
    "figcaption{font-style:italic;font-size:.9em;color:#555;margin-top:.4em;}"
    "aside{margin:.4em 0;}"
)

# Соответствие расширения файла изображения media-type для манифеста EPUB.
_IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


def _media_type_for(path: Path) -> str:
    """Вернуть media-type изображения по расширению (по умолчанию JPEG)."""

    return _IMAGE_MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")


class EpubBuilder:
    """Сборщик EPUB 3, реализующий ``EpubBuilderProtocol``.

    Параметры (язык, версия, CSS, встраивание изображений) берутся из
    :class:`~biblioatom.config.EpubSettings`, а не хардкодятся.
    """

    def __init__(self, settings: EpubSettings | None = None) -> None:
        self._settings = settings or EpubSettings()

    # -- публичный API ----------------------------------------------------

    def build(
        self,
        document: StructuredDocument,
        out_path: Path,
        images: list[ImageAsset] | None = None,
    ) -> BuildResult:
        """Собрать EPUB 3 и записать его в ``out_path``.

        :param document: структурированный документ (главы, TOC, метаданные).
        :param out_path: путь итогового ``.epub``.
        :param images: ассеты изображений, привязанные к страницам; используются
            для встраивания иллюстраций (если ``EpubSettings.embed_images``).
        :raises EpubBuildError: при сбое сборки/записи файла.
        """

        try:
            return self._build(document, out_path, images or [])
        except EpubBuildError:
            raise
        except epub.EpubException as exc:
            # EbookLib поднимает EpubException при внутренних ошибках сборки
            # (некорректные метаданные, битый манифест и т.п.).
            raise EpubBuildError(
                "EbookLib reported an error while building the EPUB.",
                context={"out_path": str(out_path), "error": str(exc)},
            ) from exc
        except OSError as exc:
            # write_epub пишет ZIP на диск; read_bytes() читает файлы изображений.
            # Оба могут поднять OSError (нет места, нет прав, битый путь).
            raise EpubBuildError(
                "I/O error while building the EPUB.",
                context={"out_path": str(out_path), "error": str(exc)},
            ) from exc

    # -- внутренняя реализация --------------------------------------------

    def _build(
        self,
        document: StructuredDocument,
        out_path: Path,
        images: list[ImageAsset],
    ) -> BuildResult:
        book = epub.EpubBook()
        # uid из book_id (или заголовка) — стабильный идентификатор издания.
        uid = document.book_id or document.title or "biblioatom-book"
        book.set_identifier(uid)
        book.set_title(document.title or "Untitled")
        book.set_language(self._settings.language)
        if document.source:
            book.add_author(document.source)

        # CSS-стиль, подключаемый ко всем XHTML-документам.
        css_text = self._settings.css or _DEFAULT_CSS
        style = epub.EpubItem(
            uid="style",
            file_name="styles/style.css",
            media_type="text/css",
            content=css_text.encode("utf-8"),
        )
        book.add_item(style)

        # Индекс изображений по странице: только первый ассет на страницу
        # (дедупликация манифеста, как в legacy ``_img_for_page``).
        images_by_page = self._index_images(images)
        embedded: dict[int, str] = {}
        used_images: list[ImageAsset] = []

        chapter_items: list[epub.EpubHtml] = []
        toc_links: list[epub.Link] = []

        for idx, chapter in enumerate(document.chapters, start=1):
            xhtml, chapter_images = self._render_chapter(
                chapter,
                idx,
                images_by_page,
                embedded,
                book,
            )
            xhtml.add_item(style)
            book.add_item(xhtml)
            chapter_items.append(xhtml)
            toc_links.append(epub.Link(xhtml.file_name, chapter.title or f"Глава {idx}", xhtml.id))
            used_images.extend(chapter_images)

        # TOC (для nav и ncx) и навигационные элементы.
        book.toc = tuple(toc_links)
        # EpubNcx — совместимость с EPUB2-ридерами; EpubNav — обязательный для
        # EPUB3 навигационный документ (properties="nav", <nav epub:type="toc">).
        book.add_item(epub.EpubNcx())
        nav = epub.EpubNav()
        book.add_item(nav)

        # Spine включает nav-документ первым, затем главы. Это даёт корректный
        # EPUB3 spine (а не осиротевший nav, как в legacy).
        book.spine = [nav, *chapter_items]

        out_path.parent.mkdir(parents=True, exist_ok=True)
        # EpubWriter по умолчанию пишет EPUB 3.0 (package version="3.0",
        # nav-документ с properties="nav"). Дополнительные опции не требуются.
        epub.write_epub(str(out_path), book)

        return BuildResult(
            book_id=document.book_id,
            outputs=[out_path],
            images=used_images,
        )

    @staticmethod
    def _index_images(images: list[ImageAsset]) -> dict[int, ImageAsset]:
        """Построить отображение страница → первый ассет (дедуп по странице)."""

        by_page: dict[int, ImageAsset] = {}
        for asset in images:
            by_page.setdefault(asset.page, asset)
        return by_page

    def _render_chapter(
        self,
        chapter: StructuredChapter,
        idx: int,
        images_by_page: dict[int, ImageAsset],
        embedded: dict[int, str],
        book: epub.EpubBook,
    ) -> tuple[epub.EpubHtml, list[ImageAsset]]:
        """Отрендерить главу в один XHTML-документ EPUB.

        Возвращает XHTML-элемент и список ассетов изображений, фактически
        встроенных в этой главе (для накопления в ``BuildResult``).
        """

        title = chapter.title or f"Глава {idx}"
        body: list[str] = [f"<h2>{escape(title)}</h2>"]
        if chapter.author:
            body.append(f'<p class="chapter-subtitle">{escape(chapter.author)}</p>')

        # Сноски накапливаются и выводятся в конце главы единым блоком <aside>.
        footnotes: list[BookElement] = []
        chapter_images: list[ImageAsset] = []

        embed = self._settings.embed_images
        for block in chapter.elements:
            text = block.text.strip()
            if not text:
                continue
            if block.kind == ElementKind.FOOTNOTE:
                footnotes.append(block)
                n = len(footnotes)
                # Маркер-ссылка на сноску в потоке текста.
                body.append(
                    f'<sup><a id="ref_{n}" href="#fn_{n}" epub:type="noteref">[{n}]</a></sup>'
                )
                continue
            if block.kind == ElementKind.CAPTION:
                asset = images_by_page.get(block.page) if embed else None
                if asset is not None:
                    href = self._ensure_image(asset, embedded, book)
                    if asset not in chapter_images:
                        chapter_images.append(asset)
                    body.append(
                        "<figure>"
                        f'<img src="{escape(href)}" alt="{escape(text[:120])}"/>'
                        f"<figcaption>{escape(text)}</figcaption>"
                        "</figure>"
                    )
                else:
                    body.append(f'<p class="image-caption">{escape(text)}</p>')
                continue
            body.append(f"<p>{escape(text)}</p>")

        if footnotes:
            body.append('<section class="footnotes" epub:type="footnotes">')
            for n, fn in enumerate(footnotes, start=1):
                body.append(
                    f'<aside id="fn_{n}" epub:type="footnote">'
                    f"<p>{escape(fn.text.strip())} "
                    f'<a href="#ref_{n}">↩</a></p>'
                    "</aside>"
                )
            body.append("</section>")

        file_name = f"text/chapter_{idx}.xhtml"
        item = epub.EpubHtml(
            uid=f"chapter_{idx}",
            file_name=file_name,
            title=title,
            lang=self._settings.language,
        )
        item.content = self._wrap_xhtml(title, "\n".join(body))
        return item, chapter_images

    def _ensure_image(
        self,
        asset: ImageAsset,
        embedded: dict[int, str],
        book: epub.EpubBook,
    ) -> str:
        """Добавить изображение в манифест один раз и вернуть его href.

        Дедупликация по номеру страницы: повторные подписи к одному изображению
        не создают дублирующих записей в манифесте.
        """

        existing = embedded.get(asset.page)
        if existing is not None:
            return existing

        file_name = f"images/{asset.path.name}"
        img = epub.EpubImage(
            uid=f"img_{asset.page:04d}",
            file_name=file_name,
            media_type=_media_type_for(asset.path),
            content=asset.path.read_bytes(),
        )
        book.add_item(img)
        embedded[asset.page] = file_name
        return file_name

    def _wrap_xhtml(self, title: str, body_html: str) -> bytes:
        """Обернуть тело главы в well-formed XHTML с epub-namespace (bytes).

        Возвращаются именно ``bytes``: lxml (используется EbookLib для повторного
        разбора тела) отказывается принимать ``str`` с XML-декларацией кодировки.

        Объявлен ``xmlns:epub`` — без него атрибуты ``epub:type`` (noteref/
        footnote) сделали бы документ невалидным. Ссылка на CSS не вставляется
        вручную: EbookLib сам добавит ``<link>`` для прикреплённого через
        ``add_item`` stylesheet.
        """

        lang = self._settings.language
        doc = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<!DOCTYPE html>\n"
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops" '
            f'lang="{escape(lang)}" xml:lang="{escape(lang)}">\n'
            f"<head><title>{escape(title)}</title></head>\n"
            f"<body>\n{body_html}\n</body></html>"
        )
        return doc.encode("utf-8")


__all__ = ["EpubBuilder"]