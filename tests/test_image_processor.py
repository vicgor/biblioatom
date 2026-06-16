"""Тесты постобработки изображений (``services/image_processor.py``).

Кропы генерируются синтетически через Pillow — без сети и бинарных фикстур.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from biblioatom.config import ImageSettings
from biblioatom.errors import ImageProcessingError
from biblioatom.models import BoundingBox, ExtractedImage, ImageAsset
from biblioatom.services import ImageProcessorProtocol
from biblioatom.services.image_processor import ImageProcessor


def _crop(width: int, height: int, mode: str = "RGB", fmt: str = "PNG") -> ExtractedImage:
    """Синтетический кроп заданного размера/режима, закодированный в байты."""

    img = Image.new(mode, (width, height), color=128 if mode == "L" else (10, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return ExtractedImage(
        page=2,
        data=buf.getvalue(),
        box=BoundingBox(x=0, y=0, width=width, height=height),
    )


def test_processor_conforms_to_protocol() -> None:
    assert isinstance(ImageProcessor(), ImageProcessorProtocol)


def test_process_saves_jpeg_with_suffix(tmp_path: Path) -> None:
    processor = ImageProcessor()
    asset = processor.process(_crop(300, 200), tmp_path / "out")

    assert isinstance(asset, ImageAsset)
    assert asset.path.suffix == ".jpg"
    assert asset.path.exists()
    assert asset.page == 2
    with Image.open(asset.path) as saved:
        assert saved.format == "JPEG"
        assert saved.size == (300, 200)


def test_process_downsizes_when_over_max(tmp_path: Path) -> None:
    settings = ImageSettings(max_width=100, max_height=100)
    processor = ImageProcessor(settings)
    asset = processor.process(_crop(400, 200), tmp_path / "big")

    # Масштаб ограничен по ширине: 400→100 (×0.25), высота 200→50.
    assert asset.width == 100
    assert asset.height == 50


def test_process_does_not_upscale(tmp_path: Path) -> None:
    settings = ImageSettings(max_width=1000, max_height=1000)
    processor = ImageProcessor(settings)
    asset = processor.process(_crop(120, 80), tmp_path / "small")

    assert (asset.width, asset.height) == (120, 80)


def test_process_normalizes_mode_to_rgb(tmp_path: Path) -> None:
    """Grayscale-кроп конвертируется в RGB (требование JPEG)."""

    processor = ImageProcessor()
    asset = processor.process(_crop(50, 50, mode="L"), tmp_path / "gray")

    with Image.open(asset.path) as saved:
        assert saved.mode == "RGB"


def test_process_keeps_caption(tmp_path: Path) -> None:
    crop = _crop(60, 60)
    crop = crop.model_copy(update={"caption": "Рис. 1"})
    asset = ImageProcessor().process(crop, tmp_path / "cap")
    assert asset.caption == "Рис. 1"


def test_process_invalid_bytes_raise_domain_error(tmp_path: Path) -> None:
    crop = ExtractedImage(
        page=0, data=b"not-an-image", box=BoundingBox(x=0, y=0, width=10, height=10)
    )
    with pytest.raises(ImageProcessingError):
        ImageProcessor().process(crop, tmp_path / "bad")


def test_process_png_output_format(tmp_path: Path) -> None:
    settings = ImageSettings(output_format="PNG")
    asset = ImageProcessor(settings).process(_crop(40, 40), tmp_path / "p")
    assert asset.path.suffix == ".png"
    with Image.open(asset.path) as saved:
        assert saved.format == "PNG"
