"""Структурный анализ книги: классификация блоков и разбивка на главы.

Ядро доменных знаний, типизированное на Pydantic-модели
(``BookElement``/``PageModel``/``StructuredChapter``/``StructuredDocument``).
Эвристики заголовков/авторов — самый ценный актив проекта.

Ключевые гарантии:

* ``is_probable_author_line`` распознаёт дефисные фамилии
  (например ``"Боровик-Романов А. С."``).
* ``split_by_toc`` не теряет страницы с номером меньше ``toc[0].page``
  — для них создаётся front-matter глава.
* ``parse_embedded_content`` сужает перехват исключений до
  ``json.JSONDecodeError`` вместо широкого ``Exception``.
"""

from __future__ import annotations

import json
import re

from biblioatom.models import (
    BookElement,
    ElementKind,
    EmbeddedContent,
    PageModel,
    StructuredChapter,
    StructuredDocument,
    TocEntry,
)
from biblioatom.services.html_cleaner import (
    clean_pagehtml,
    normalize_text,
    strip_tags_preserve_text,
)

# Нормализованные ключи служебных front-matter заголовков (uppercase, Ё→Е).
# Кандидат на вынос в config; пока оставлено модульной константой,
# чтобы не протаскивать config в чистые функции анализа.
FRONT_MATTER_TITLES = {
    "ОБЛОЖКА",
    "ФРОНТИСПИС",
    "РОССИЙСКАЯ АКАДЕМИЯ НАУК",
}

STRICT_MIN_PAGE_FOR_CHAPTER = 5

# Обычный абзац основного текста не имеет специализированного вида в ElementKind,
# поэтому маппится на NOTE как наименее специализированный прозаический вид.
# Заголовки/сноски/подписи получают свои виды (HEADING/FOOTNOTE/CAPTION).
_BODY_KIND = ElementKind.NOTE

_P_RE = re.compile(r"<p(?P<attrs>[^>]*)>(?P<body>.*?)</p>", re.I | re.S)
_CLASS_RE = re.compile(r'class=["\']([^"\']+)["\']', re.I)
_PARA_SPLIT_RE = re.compile(r"\n\s*\n")
_HEADING_MARKS_RE = re.compile(r"[*]+$")
_WS_RE = re.compile(r"\s+")
_INITIALS_RE = re.compile(r"^[А-ЯA-Z]\.?[А-ЯA-Z]?\.$")
# Слово-фамилия: допускаем внутренние дефисы (Боровик-Романов), но не ведущий/
# хвостовой дефис.
_SURNAME_RE = re.compile(r"^[А-ЯA-ZЁ][а-яa-zё]+(?:-[А-ЯA-ZЁ][а-яa-zё]+)*$")


# ---------------------------------------------------------------------------
# Извлечение блоков из HTML
# ---------------------------------------------------------------------------


def extract_blocks(pagehtml: str, page: int, fallback_text: str = "") -> list[BookElement]:
    """Извлечь типизированные блоки (``BookElement``) со страницы.

    Сопоставляются только элементы ``<p>``. ``<div class="comp-draft">`` — это
    контейнер, оборачивающий ``<p class="text|img|ftn">``; сопоставление div
    схлопнуло бы все вложенные абзацы в один блок, потеряв их индивидуальные
    атрибуты class.
    """

    blocks: list[BookElement] = []

    if pagehtml:
        for m in _P_RE.finditer(pagehtml):
            attrs = m.group("attrs") or ""
            body = m.group("body") or ""

            class_match = _CLASS_RE.search(attrs)
            classes = class_match.group(1).split() if class_match else []

            text = strip_tags_preserve_text(body)
            if not text:
                continue
            if "page-no" in classes:
                continue

            if "ftn" in classes:
                blocks.append(BookElement(kind=ElementKind.FOOTNOTE, text=text, page=page))
            elif "img" in classes:
                blocks.append(BookElement(kind=ElementKind.CAPTION, text=text, page=page))
            else:
                blocks.append(BookElement(kind=_BODY_KIND, text=text, page=page))

    if not blocks and fallback_text:
        cleaned = normalize_text(fallback_text)
        for part in _PARA_SPLIT_RE.split(cleaned):
            part = part.strip()
            if part:
                blocks.append(BookElement(kind=_BODY_KIND, text=part, page=page))

    return blocks


# ---------------------------------------------------------------------------
# Разбор встроенного содержимого страницы
# ---------------------------------------------------------------------------


def parse_embedded_content(raw: str | dict[str, object] | None) -> EmbeddedContent:
    """Разобрать поле ``content`` страницы в :class:`EmbeddedContent`.

    ``raw`` — либо JSON-строка, либо уже распарсенный словарь (поддерживаются оба
    формата входных данных). При ошибке разбора JSON возвращается ``valid=False``
    с исходным текстом в ``pagetext``.
    """

    if isinstance(raw, dict):
        return _embedded_from_dict(raw)
    if not raw:
        return EmbeddedContent()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return EmbeddedContent(valid=False, pagetext=str(raw), pagehtml="")
    if isinstance(parsed, dict):
        return _embedded_from_dict(parsed)
    return EmbeddedContent(valid=False, pagetext=str(raw), pagehtml="")


def _embedded_from_dict(data: dict[str, object]) -> EmbeddedContent:
    """Построить :class:`EmbeddedContent` из словаря, приводя типы полей."""

    return EmbeddedContent(
        valid=bool(data.get("valid", True)),
        pagetext=str(data.get("pagetext", "") or ""),
        pagehtml=str(data.get("pagehtml", "") or ""),
    )


def _extract_page_no_from_html(pagehtml: str) -> str | None:
    """Извлечь печатный номер страницы из HTML (<p class='page'/'page-no'>)."""
    for m in _P_RE.finditer(pagehtml):
        attrs = m.group("attrs") or ""
        body = m.group("body") or ""
        class_match = _CLASS_RE.search(attrs)
        classes = class_match.group(1).split() if class_match else []
        if "page" in classes or "page-no" in classes:
            text = strip_tags_preserve_text(body).strip()
            if text:
                return text
    return None


def page_to_model(page: int, content: EmbeddedContent, print_page: str | None = None) -> PageModel:
    """Построить :class:`PageModel` из содержимого страницы.

    Текст нормализуется, HTML чистится, блоки извлекаются с привязкой к номеру
    страницы.
    """

    pagetext = normalize_text(content.pagetext)
    pagehtml = clean_pagehtml(content.pagehtml)
    normalized = EmbeddedContent(valid=content.valid, pagetext=pagetext, pagehtml=pagehtml)
    elements = extract_blocks(pagehtml, page=page, fallback_text=pagetext)

    if print_page is None:
        print_page = _extract_page_no_from_html(content.pagehtml)

    return PageModel(page=page, print_page=print_page, content=normalized, elements=elements)


# ---------------------------------------------------------------------------
# Эвристики заголовков / авторов
# ---------------------------------------------------------------------------


def strip_heading_marks(text: str) -> str:
    """Убрать хвостовые звёздочки-маркеры из заголовка."""

    return _HEADING_MARKS_RE.sub("", text.strip()).strip()


def normalized_heading_key(text: str) -> str:
    """Нормализованный ключ заголовка (uppercase, Ё→Е, схлопнутые пробелы)."""

    t = strip_heading_marks(text).upper()
    t = t.replace("Ё", "Е")
    return _WS_RE.sub(" ", t).strip()


def is_probable_heading(text: str) -> bool:
    """Эвристика: похож ли текст на заголовок (преимущественно прописные)."""

    t = (text or "").strip()
    if not t:
        return False
    t = _WS_RE.sub(" ", t)
    plain = t.strip("•*-—– ")
    if len(plain) < 4 or len(plain) > 120:
        return False
    if plain.endswith(".") and len(plain) > 40:
        return False
    letters = [ch for ch in plain if ch.isalpha()]
    if not letters:
        return False
    upper = sum(1 for ch in letters if ch.isupper())
    if upper / len(letters) < 0.6:
        return False
    return not len(plain.split()) > 12


def is_probable_author_line(text: str) -> bool:
    """Эвристика: похож ли текст на строку автора (инициалы + фамилия).

    Регулярка фамилии допускает внутренний дефис, поэтому распознаёт дефисные
    фамилии вида ``"Боровик-Романов А. С."``.
    """

    t = (text or "").strip()
    if not t or len(t) > 80:
        return False
    if any(ch.isdigit() for ch in t):
        return False
    words = t.split()
    if len(words) < 2 or len(words) > 6:
        return False
    initials = sum(1 for w in words if _INITIALS_RE.match(w))
    surname_like = any(_SURNAME_RE.match(w) for w in words)
    return initials >= 1 and surname_like


def is_front_matter_heading(text: str) -> bool:
    """Является ли заголовок служебной front-matter записью (обложка и т.п.)."""

    return normalized_heading_key(text) in FRONT_MATTER_TITLES


def should_start_chapter(text: str, page_no: int, mode: str) -> bool:
    """Решить, начинает ли блок новую главу, с учётом режима ``normal``/``strict``."""

    if not is_probable_heading(text):
        return False
    if mode == "normal":
        return True
    # Дешёвая числовая проверка идёт первой (ранний выход), чтобы не выполнять
    # дорогую нормализацию ключа для заведомо ранних страниц.
    if page_no < STRICT_MIN_PAGE_FOR_CHAPTER:
        return False
    # Ключ нормализуется один раз и переиспользуется: сравниваем его с
    # FRONT_MATTER_TITLES напрямую вместо повторной нормализации в
    # is_front_matter_heading.
    key = normalized_heading_key(text)
    if key in FRONT_MATTER_TITLES:
        return False
    return not (len(key.split()) <= 2 and not key.endswith(":"))


# ---------------------------------------------------------------------------
# Эвристическая разбивка на главы (когда нет TOC)
# ---------------------------------------------------------------------------


def split_into_chapters(pages: list[PageModel], mode: str = "strict") -> list[StructuredChapter]:
    """Разбить страницы на главы эвристически (по заголовкам), без TOC."""

    chapters: list[StructuredChapter] = []
    current = StructuredChapter(title="Front Matter")
    # Аккумулируем сами объекты PageModel (а не только их номера), чтобы
    # установить ``current.pages`` — иначе поле остаётся пустым и downstream
    # (EPUB-builder, CLI) теряет содержимое. Порядок добавления = порядок входа.
    current_pages: list[PageModel] = []
    pending_author = ""

    def _attach_page(pg: PageModel) -> None:
        # Внутри текущей главы одну и ту же страницу не добавляем дважды
        # (страница может содержать несколько блоков). Это не запрещает
        # пограничной странице попасть и в соседнюю главу: при заголовке в
        # середине страницы её хвост остаётся здесь, а новая глава ниже стартует
        # с того же pg — страница окажется в pages обеих глав (ожидаемо).
        if not current_pages or current_pages[-1].page != pg.page:
            current_pages.append(pg)

    for pg in pages:
        elements = pg.elements
        i = 0
        while i < len(elements):
            block = elements[i]
            btext = block.text.strip()
            if not btext:
                i += 1
                continue

            if (
                block.kind == _BODY_KIND
                and is_probable_author_line(btext)
                and i + 1 < len(elements)
            ):
                nxt = elements[i + 1]
                if nxt.kind == _BODY_KIND and should_start_chapter(nxt.text, pg.page, mode):
                    pending_author = btext
                    i += 1
                    continue

            if block.kind == _BODY_KIND and should_start_chapter(btext, pg.page, mode):
                if current.elements or current_pages:
                    current.pages = current_pages
                    chapters.append(current)
                current = StructuredChapter(
                    title=strip_heading_marks(btext),
                    author=pending_author or None,
                )
                current_pages = [pg]
                pending_author = ""
                i += 1
                continue

            _attach_page(pg)
            current.elements.append(BookElement(kind=block.kind, text=btext, page=pg.page))
            i += 1

    if current.elements or current_pages:
        current.pages = current_pages
        chapters.append(current)

    return _merge_empty_front_matter(chapters)


def _merge_empty_front_matter(chapters: list[StructuredChapter]) -> list[StructuredChapter]:
    """Отбросить front-matter, не содержащий блоков основного текста.

    «Пустой» front-matter — глава с заголовком ``"Front Matter"`` без ни одного
    ``BookElement`` (даже если за ней закреплены ``pages``: страница без значимых
    блоков, например только номер страницы). Такой служебный раздел не должен
    порождать отдельную главу при наличии хотя бы одной содержательной.

    После фикса аккумуляции ``pages`` (см. :func:`split_into_chapters`)
    front-matter теперь почти всегда имеет непустой ``pages``, поэтому фильтр
    опирается строго на ``elements``, а не на ``pages``.
    """

    if len(chapters) >= 2 and chapters[0].title == "Front Matter" and not chapters[0].elements:
        return chapters[1:]
    return chapters


# ---------------------------------------------------------------------------
# Разбивка на главы по TOC
# ---------------------------------------------------------------------------


def split_by_toc(pages: list[PageModel], toc: list[TocEntry]) -> list[StructuredChapter]:
    """Построить главы из распарсенного TOC вместо эвристики заголовков.

    ``toc[i].page`` — физический 0-based индекс страницы.

    Когда две соседние записи TOC начинаются на одной странице (заголовок
    раздела и его первая статья), ранняя запись становится содержательно-пустым
    разделителем; страница принадлежит последней записи на этой странице.

    Исправлен blocking-баг: страницы с номером меньше ``toc[0].page`` ранее
    терялись. Теперь для них создаётся front-matter глава, чтобы ни одна
    страница не пропадала.
    """

    if not toc:
        return []

    page_content = {pg.page: pg for pg in pages}
    first_toc_page = toc[0].page

    chapters: list[StructuredChapter] = []

    # Front-matter: страницы до первой записи TOC.
    front_pages = sorted(p for p in page_content if p < first_toc_page)
    if front_pages:
        chapters.append(_build_toc_chapter("Front Matter", None, 0, front_pages, page_content))

    for i, entry in enumerate(toc):
        next_entry = toc[i + 1] if i + 1 < len(toc) else None
        is_divider = next_entry is not None and next_entry.page == entry.page

        if is_divider:
            chapter_pages: list[int] = []
        else:
            # Владеем всеми страницами от entry.page до (не включая) следующей
            # записи, начинающейся со строго большего номера страницы.
            next_diff_page: int | None = None
            for j in range(i + 1, len(toc)):
                if toc[j].page > entry.page:
                    next_diff_page = toc[j].page
                    break

            if next_diff_page is None:
                chapter_pages = sorted(p for p in page_content if p >= entry.page)
            else:
                chapter_pages = sorted(
                    p for p in range(entry.page, next_diff_page) if p in page_content
                )

        chapters.append(
            _build_toc_chapter(
                entry.title,
                entry.author,
                entry.level,
                chapter_pages,
                page_content,
            )
        )

    return chapters


def _build_toc_chapter(
    title: str,
    author: str | None,
    level: int,
    chapter_pages: list[int],
    page_content: dict[int, PageModel],
) -> StructuredChapter:
    """Собрать главу TOC: перенести непустые блоки указанных страниц.

    ``print_page`` намеренно НЕ принимается: на уровне ``StructuredChapter`` его
    хранить негде, а исходное значение остаётся в ``TocEntry`` документа. Раньше
    параметр принимался и тут же выбрасывался (``_ = print_page``).
    """

    elements: list[BookElement] = []
    pages: list[PageModel] = []
    for pno in chapter_pages:
        pg = page_content[pno]
        pages.append(pg)
        for block in pg.elements:
            if block.text.strip():
                elements.append(
                    BookElement(
                        kind=block.kind,
                        text=block.text.strip(),
                        page=pno,
                        anchor=block.anchor,
                        ref=block.ref,
                    )
                )
    return StructuredChapter(
        title=title, author=author, level=level, pages=pages, elements=elements
    )


# ---------------------------------------------------------------------------
# Реализация StructureAnalyzerProtocol
# ---------------------------------------------------------------------------


class StructureAnalyzer:
    """Структурный анализатор, реализующий ``StructureAnalyzerProtocol``."""

    def __init__(self, chapter_mode: str = "strict") -> None:
        self._chapter_mode = chapter_mode

    def analyze(self, pages: list[PageModel], toc: list[TocEntry]) -> StructuredDocument:
        """Построить структурированный документ из страниц и оглавления."""

        if toc:
            chapters = split_by_toc(pages, toc)
        else:
            chapters = split_into_chapters(pages, mode=self._chapter_mode)
        return StructuredDocument(title="", book_id="", toc=toc, chapters=chapters)


__all__ = [
    "FRONT_MATTER_TITLES",
    "STRICT_MIN_PAGE_FOR_CHAPTER",
    "StructureAnalyzer",
    "extract_blocks",
    "is_front_matter_heading",
    "is_probable_author_line",
    "is_probable_heading",
    "normalized_heading_key",
    "page_to_model",
    "parse_embedded_content",
    "should_start_chapter",
    "split_by_toc",
    "split_into_chapters",
    "strip_heading_marks",
]
