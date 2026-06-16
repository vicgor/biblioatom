"""Тесты извлечения иллюстраций со сканов (``services/scan_extractor.py``).

Сканы генерируются синтетически через numpy/OpenCV — без сети и без бинарных
фикстур. Проверяется извлечение «фото» (тёмные прямоугольники на белом листе),
отсев текстового шума и работа граничных фильтров.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from biblioatom.config import ScanExtractionSettings
from biblioatom.errors import ScanExtractionError
from biblioatom.models import ExtractedImage
from biblioatom.services import ScanExtractorProtocol
from biblioatom.services.scan_extractor import ScanExtractor

# Размер синтетической страницы (h, w) — близко к пропорции книжного скана.
_PAGE_H = 1000
_PAGE_W = 800


def _blank_page() -> NDArray[np.uint8]:
    """Белый «лист» BGR."""

    return np.full((_PAGE_H, _PAGE_W, 3), 255, dtype=np.uint8)


def _draw_filled_rect(
    page: NDArray[np.uint8], x: int, y: int, w: int, h: int, color: int = 30
) -> None:
    """Нарисовать сплошной тёмный прямоугольник («фото») in-place."""

    cv2.rectangle(page, (x, y), (x + w, y + h), (color, color, color), thickness=-1)


def _encode(page: NDArray[np.uint8]) -> bytes:
    """Закодировать страницу в PNG-байты."""

    ok, buf = cv2.imencode(".png", page)
    assert ok
    return bytes(buf.tobytes())


def test_extractor_conforms_to_protocol() -> None:
    assert isinstance(ScanExtractor(), ScanExtractorProtocol)


def test_single_photo_is_extracted() -> None:
    """Один крупный тёмный прямоугольник извлекается как одно фото."""

    page = _blank_page()
    _draw_filled_rect(page, x=150, y=200, w=400, h=500)

    extractor = ScanExtractor()
    result = extractor.extract(_encode(page), page=5)

    assert len(result) == 1
    crop = result[0]
    assert isinstance(crop, ExtractedImage)
    assert crop.page == 5
    # Рамка примерно совпадает с нарисованной (± паддинг и толщина пикселей).
    assert crop.box.width == pytest.approx(400, abs=20)
    assert crop.box.height == pytest.approx(500, abs=20)
    assert crop.data  # непустые байты PNG


def test_multiple_photos_are_extracted() -> None:
    """Два разнесённых прямоугольника дают два кропа."""

    page = _blank_page()
    _draw_filled_rect(page, x=100, y=80, w=300, h=300)
    _draw_filled_rect(page, x=120, y=550, w=350, h=300)

    extractor = ScanExtractor()
    result = extractor.extract(_encode(page), page=7)

    assert len(result) == 2
    # Сортировка сверху-вниз: первый кроп выше второго.
    assert result[0].box.y < result[1].box.y


def test_text_noise_is_filtered_out() -> None:
    """Страница «текстового шума» (мелкие штрихи) не даёт ложных фото."""

    rng = np.random.default_rng(seed=42)
    page = _blank_page()
    # Имитация строк текста: множество мелких тёмных штрихов/«слов».
    for row in range(60, _PAGE_H - 60, 40):
        for col in range(60, _PAGE_W - 60, 70):
            word_w = int(rng.integers(20, 55))
            cv2.rectangle(page, (col, row), (col + word_w, row + 12), (40, 40, 40), -1)

    extractor = ScanExtractor()
    result = extractor.extract(_encode(page), page=3)

    assert result == []


def test_photo_among_text_is_extracted() -> None:
    """Фото на странице с текстом извлекается, а текст отсеивается."""

    page = _blank_page()
    # Текстовый шум в верхней части.
    for row in range(60, 300, 36):
        for col in range(60, _PAGE_W - 60, 70):
            cv2.rectangle(page, (col, row), (col + 40, row + 12), (40, 40, 40), -1)
    # Крупное фото в нижней части.
    _draw_filled_rect(page, x=150, y=450, w=420, h=420)

    extractor = ScanExtractor()
    result = extractor.extract(_encode(page), page=9)

    assert len(result) == 1
    assert result[0].box.width == pytest.approx(420, abs=25)


def test_too_small_object_is_rejected() -> None:
    """Объект меньше min_area_ratio отбрасывается."""

    page = _blank_page()
    # ~0.6% площади страницы при min_area_ratio=0.02 → отбрасывается.
    _draw_filled_rect(page, x=300, y=400, w=70, h=70)

    extractor = ScanExtractor()
    assert extractor.extract(_encode(page), page=1) == []


def test_too_elongated_object_is_rejected() -> None:
    """Сильно вытянутый объект (линия) отбрасывается фильтром aspect."""

    page = _blank_page()
    # Очень широкая тонкая полоса: aspect = 600/20 = 30 >> max_aspect (5).
    _draw_filled_rect(page, x=80, y=480, w=600, h=20)

    extractor = ScanExtractor()
    assert extractor.extract(_encode(page), page=1) == []


def test_low_fill_object_is_rejected() -> None:
    """Тонкая диагональная линия отбрасывается фильтром заполнения (extent).

    Bounding box диагонали велик, а площадь самого контура мала → низкий extent.
    Canny отключаем, чтобы две границы линии не образовали «плотный» контур.
    """

    page = _blank_page()
    cv2.line(page, (150, 200), (550, 700), (30, 30, 30), thickness=5)

    settings = ScanExtractionSettings(use_canny=False, min_fill_ratio=0.5)
    extractor = ScanExtractor(settings)
    assert extractor.extract(_encode(page), page=1) == []


def test_empty_bytes_raise_domain_error() -> None:
    extractor = ScanExtractor()
    with pytest.raises(ScanExtractionError):
        extractor.extract(b"", page=0)


def test_undecodable_bytes_raise_domain_error() -> None:
    extractor = ScanExtractor()
    with pytest.raises(ScanExtractionError):
        extractor.extract(b"not-an-image", page=0)


def test_crop_padding_expands_box() -> None:
    """Паддинг расширяет рамку кропа в пределах страницы."""

    page = _blank_page()
    _draw_filled_rect(page, x=200, y=200, w=300, h=300)

    settings = ScanExtractionSettings(crop_padding=10)
    extractor = ScanExtractor(settings)
    result = extractor.extract(_encode(page), page=1)

    assert len(result) == 1
    # С паддингом рамка шире самой фигуры.
    assert result[0].box.width >= 300
