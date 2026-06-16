"""Тесты сборщика EPUB 3 (``services/epub_builder.py``).

Без сети: на фикстуре :class:`StructuredDocument` собирается EPUB во временную
директорию, распаковывается ``zipfile`` и проверяется его структура:

* версия EPUB 3 в OPF, nav с ``properties="nav"`` и ``epub:type="toc"``;
* nav и главы присутствуют в spine/manifest;
* ``<figure>/<figcaption>`` для изображения;
* рабочие двусторонние якоря сносок (``ref_N`` ↔ ``fn_N``);
* well-formed XHTML (парсится lxml без ошибок);
* изображение в манифесте с правильным media-type и дедупликацией.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from lxml import etree

from biblioatom.config import EpubSettings
from biblioatom.models import (
    BookElement,
    ElementKind,
    ImageAsset,
    StructuredChapter,
    StructuredDocument,
    TocEntry,
)
from biblioatom.services.epub_builder import EpubBuilder

_OPF_PATH = "EPUB/content.opf"
_NAV_PATH = "EPUB/nav.xhtml"
_CH1_PATH = "EPUB/text/chapter_1.xhtml"
_CH2_PATH = "EPUB/text/chapter_2.xhtml"


@pytest.fixture
def image_file(tmp_path: Path) -> Path:
    """Создать минимальный JPEG-файл для встраивания (без Pillow)."""

    # Минимальный валидный-по-сигнатуре JPEG: содержимое для теста не важно,
    # builder лишь читает байты и кладёт их в ZIP.
    path = tmp_path / "0005_photo.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 16 + b"\xff\xd9")
    return path


@pytest.fixture
def document(image_file: Path) -> StructuredDocument:
    """Структурированный документ с двумя главами, сноской и изображением."""

    return StructuredDocument(
        title="Капица",
        book_id="kapitsa_1994",
        source="https://elib.biblioatom.ru",
        toc=[TocEntry(title="Введение", page=0), TocEntry(title="Иллюстрация", page=5)],
        chapters=[
            StructuredChapter(
                title="Введение",
                author="Капица П. Л.",
                elements=[
                    BookElement(kind=ElementKind.NOTE, text="Первый абзац.", page=0),
                    BookElement(kind=ElementKind.FOOTNOTE, text="Это сноска.", page=0),
                    BookElement(kind=ElementKind.NOTE, text="Второй абзац.", page=0),
                ],
            ),
            StructuredChapter(
                title="Иллюстрация",
                elements=[
                    BookElement(kind=ElementKind.CAPTION, text="Подпись к фото", page=5),
                    # Вторая подпись на той же странице — проверка дедупликации.
                    BookElement(kind=ElementKind.CAPTION, text="Та же страница", page=5),
                ],
            ),
        ],
    )


@pytest.fixture
def built_epub(
    document: StructuredDocument,
    image_file: Path,
    tmp_path: Path,
) -> zipfile.ZipFile:
    """Собрать EPUB и вернуть открытый ``ZipFile`` для инспекции."""

    out = tmp_path / "book.epub"
    images = [ImageAsset(page=5, path=image_file, caption="Подпись к фото")]
    result = EpubBuilder().build(document, out, images)
    assert result.outputs == [out]
    assert out.exists()
    return zipfile.ZipFile(out)


def _read(zf: zipfile.ZipFile, name: str) -> str:
    return zf.read(name).decode("utf-8")


def test_epub3_version_in_opf(built_epub: zipfile.ZipFile) -> None:
    opf = _read(built_epub, _OPF_PATH)
    assert 'version="3.0"' in opf


def test_nav_has_nav_properties_and_toc_type(built_epub: zipfile.ZipFile) -> None:
    opf = _read(built_epub, _OPF_PATH)
    assert 'properties="nav"' in opf
    nav = _read(built_epub, _NAV_PATH)
    assert 'epub:type="toc"' in nav


def test_nav_is_in_spine(built_epub: zipfile.ZipFile) -> None:
    opf = _read(built_epub, _OPF_PATH)
    spine = opf[opf.index("<spine") : opf.index("</spine>")]
    # nav-документ должен быть первым в spine (id nav-документа EbookLib — "nav").
    assert 'idref="nav"' in spine
    assert 'idref="chapter_1"' in spine
    assert 'idref="chapter_2"' in spine


def test_chapters_in_manifest(built_epub: zipfile.ZipFile) -> None:
    names = built_epub.namelist()
    assert _CH1_PATH in names
    assert _CH2_PATH in names


def test_figure_figcaption_for_image(built_epub: zipfile.ZipFile) -> None:
    ch2 = _read(built_epub, _CH2_PATH)
    assert "<figure>" in ch2
    assert "<figcaption>" in ch2
    assert "Подпись к фото" in ch2
    assert "images/0005_photo.jpg" in ch2


def test_footnote_anchors_are_linked(built_epub: zipfile.ZipFile) -> None:
    ch1 = _read(built_epub, _CH1_PATH)
    # Маркер-ссылка в тексте → сноска.
    assert 'id="ref_1"' in ch1
    assert 'href="#fn_1"' in ch1
    assert 'epub:type="noteref"' in ch1
    # Сноска → обратная ссылка на маркер.
    assert 'id="fn_1"' in ch1
    assert 'epub:type="footnote"' in ch1
    assert 'href="#ref_1"' in ch1


def test_all_documents_well_formed_xml(built_epub: zipfile.ZipFile) -> None:
    for name in (_CH1_PATH, _CH2_PATH, _NAV_PATH, _OPF_PATH):
        # Падение здесь означало бы невалидный XHTML/OPF.
        etree.fromstring(built_epub.read(name))


def test_image_in_manifest_with_media_type(built_epub: zipfile.ZipFile) -> None:
    opf = _read(built_epub, _OPF_PATH)
    assert 'media-type="image/jpeg"' in opf
    assert "EPUB/images/0005_photo.jpg" in built_epub.namelist()


def test_image_manifest_dedup(built_epub: zipfile.ZipFile) -> None:
    opf = _read(built_epub, _OPF_PATH)
    # Две подписи на одну страницу → ровно одна запись изображения в манифесте.
    assert opf.count("images/0005_photo.jpg") == 1


def test_metadata_title_language_author(built_epub: zipfile.ZipFile) -> None:
    opf = _read(built_epub, _OPF_PATH)
    assert "Капица" in opf
    assert ">ru<" in opf
    # source попадает в авторы (dc:creator).
    assert "elib.biblioatom.ru" in opf


def test_custom_css_from_settings(
    document: StructuredDocument,
    image_file: Path,
    tmp_path: Path,
) -> None:
    marker = ".custom-marker{color:red;}"
    builder = EpubBuilder(EpubSettings(css=marker))
    out = tmp_path / "custom.epub"
    builder.build(document, out, [ImageAsset(page=5, path=image_file)])
    with zipfile.ZipFile(out) as zf:
        css = zf.read("EPUB/styles/style.css").decode("utf-8")
    assert marker in css


def test_embed_images_disabled(
    document: StructuredDocument,
    image_file: Path,
    tmp_path: Path,
) -> None:
    builder = EpubBuilder(EpubSettings(embed_images=False))
    out = tmp_path / "noimg.epub"
    builder.build(document, out, [ImageAsset(page=5, path=image_file)])
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        ch2 = zf.read(_CH2_PATH).decode("utf-8")
    # Изображение не встроено: нет файла в ZIP и нет <figure>, подпись как абзац.
    assert "EPUB/images/0005_photo.jpg" not in names
    assert "<figure>" not in ch2
    assert 'class="image-caption"' in ch2


def test_empty_document_builds(tmp_path: Path) -> None:
    doc = StructuredDocument(title="Пусто", book_id="empty")
    out = tmp_path / "empty.epub"
    result = EpubBuilder().build(doc, out)
    assert out.exists()
    assert result.book_id == "empty"
    with zipfile.ZipFile(out) as zf:
        # Даже без глав документ должен содержать nav и быть EPUB3.
        assert _NAV_PATH in zf.namelist()
        assert 'version="3.0"' in zf.read(_OPF_PATH).decode("utf-8")
