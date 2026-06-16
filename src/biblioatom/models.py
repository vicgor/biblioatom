"""Доменные модели на Pydantic v2.

Модели описывают результат структурного анализа книги: страницы, оглавление,
типизированные блоки текста, главы и итоговый документ, а также ассеты
изображений и результат сборки.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ElementKind(StrEnum):
    """Тип семантического блока внутри страницы/главы."""

    CAPTION = "caption"
    FOOTNOTE = "footnote"
    NOTE = "note"
    EPIGRAPH = "epigraph"
    QUOTE = "quote"
    SIDEBAR = "sidebar"
    HEADING = "heading"
    # Trailing underscore в имени члена — чтобы не конфликтовать с builtin 'list'
    # в области видимости, где StrEnum-члены используются без квалификатора.
    # Сериализованное значение явно задано как "list" (без underscore), чтобы
    # JSON-представление было человекочитаемым и предсказуемым.
    LIST_ = "list"
    TABLE = "table"


class _Base(BaseModel):
    """Базовая модель с общей конфигурацией."""

    model_config = ConfigDict(extra="forbid", frozen=False)


class BookElement(_Base):
    """Типизированный блок текста, извлечённый со страницы."""

    kind: ElementKind
    text: str
    page: int = Field(ge=0)
    anchor: str | None = None
    ref: str | None = None


class TocEntry(_Base):
    """Запись оглавления (TOC)."""

    title: str
    author: str | None = None
    page: int = Field(ge=0)
    print_page: str | None = None
    level: int = Field(default=0, ge=0)


class BookMeta(_Base):
    """Метаданные книги со страницы просмотра.

    ``page_count_is_fallback`` отмечает, что ``max_page`` не удалось извлечь из
    HTML (нет ``data-rel`` или иного источника) и использовано значение по
    умолчанию из config. Признак нужен вышестоящему коду (``fetch_book``), чтобы
    не принять «выдуманный» предел за настоящий и предупредить о возможной
    неполноте загрузки.
    """

    title: str
    max_page: int = Field(ge=1)
    page_count_is_fallback: bool = False


class EmbeddedContent(_Base):
    """Содержимое страницы из RPC-ответа (``content``)."""

    valid: bool = True
    pagetext: str = ""
    pagehtml: str = ""


class PageModel(_Base):
    """Модель одной страницы книги."""

    page: int = Field(ge=0)
    print_page: str | None = None
    content: EmbeddedContent
    elements: list[BookElement] = Field(default_factory=list)


class StructuredChapter(_Base):
    """Глава структурированного документа."""

    title: str
    author: str | None = None
    level: int = Field(default=0, ge=0)
    pages: list[PageModel] = Field(default_factory=list)
    elements: list[BookElement] = Field(default_factory=list)


class StructuredDocument(_Base):
    """Полностью структурированная книга."""

    title: str
    book_id: str
    source: str | None = None
    toc: list[TocEntry] = Field(default_factory=list)
    chapters: list[StructuredChapter] = Field(default_factory=list)


class BoundingBox(_Base):
    """Прямоугольная область на странице (в пикселях, начало координат — левый верх)."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    @property
    def area(self) -> int:
        """Площадь рамки в пикселях."""

        return self.width * self.height


class ExtractedImage(_Base):
    """Кроп иллюстрации, извлечённый из скана (в памяти, до постобработки).

    Содержит сырые байты изображения и геометрию исходной рамки. Pydantic-поле
    ``data`` хранит ``bytes`` (например, закодированный PNG/JPEG из OpenCV).
    """

    model_config = ConfigDict(extra="forbid", frozen=False, arbitrary_types_allowed=True)

    page: int = Field(ge=0)
    data: bytes
    box: BoundingBox
    caption: str | None = None


class ImageAsset(_Base):
    """Ассет изображения (иллюстрация/скан), привязанный к странице."""

    page: int = Field(ge=0)
    path: Path
    caption: str | None = None
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)


class BuildResult(_Base):
    """Результат сборки выходных файлов."""

    book_id: str
    outputs: list[Path] = Field(default_factory=list)
    images: list[ImageAsset] = Field(default_factory=list)


__all__ = [
    "BookElement",
    "BookMeta",
    "BoundingBox",
    "BuildResult",
    "ElementKind",
    "EmbeddedContent",
    "ExtractedImage",
    "ImageAsset",
    "PageModel",
    "StructuredChapter",
    "StructuredDocument",
    "TocEntry",
]