"""Постобработка извлечённых кропов через Pillow (green-field).

Реализует :class:`~biblioatom.services.ImageProcessorProtocol`. Шаги: загрузка
байтов кропа → нормализация цветового режима → опциональный ресайз по максимальным
размерам (с сохранением пропорций) → сохранение в нужном формате с качеством.
Параметры берутся из :class:`~biblioatom.config.ImageSettings`. Ошибки
оборачиваются в :class:`~biblioatom.errors.ImageProcessingError`.
"""

from __future__ import annotations

import io
import time
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from biblioatom.config import ImageSettings
from biblioatom.errors import ImageProcessingError
from biblioatom.logging_config import get_logger
from biblioatom.models import ExtractedImage, ImageAsset

_logger = get_logger(__name__)

# Расширение файла по формату Pillow (для прочих форматов берётся lower-case имя).
_FORMAT_SUFFIX = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}


class ImageProcessor:
    """Постобработчик изображений на Pillow, реализующий ``ImageProcessorProtocol``."""

    def __init__(self, settings: ImageSettings | None = None) -> None:
        self._settings = settings or ImageSettings()

    def process(self, image: ExtractedImage, out_path: Path) -> ImageAsset:
        """Нормализовать кроп, при необходимости уменьшить и сохранить.

        :param image: извлечённый кроп (байты + геометрия).
        :param out_path: путь сохранения; суффикс приводится к ``output_format``.
        :raises ImageProcessingError: при сбое декодирования/сохранения.
        """

        started = time.perf_counter()
        try:
            asset = self._process(image, out_path)
        except ImageProcessingError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ImageProcessingError(
                "Failed to post-process the extracted image.",
                context={"page": image.page, "out_path": str(out_path), "error": str(exc)},
            ) from exc

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        _logger.info(
            "image_processor.done",
            page=image.page,
            path=str(asset.path),
            width=asset.width,
            height=asset.height,
            duration_ms=duration_ms,
        )
        return asset

    def _process(self, image: ExtractedImage, out_path: Path) -> ImageAsset:
        with Image.open(io.BytesIO(image.data)) as img:
            converted = self._normalize_mode(img)
            resized = self._resize(converted)
            target = self._target_path(out_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            resized.save(
                target,
                format=self._settings.output_format,
                quality=self._settings.quality,
            )
            width, height = resized.size

        return ImageAsset(
            page=image.page,
            path=target,
            caption=image.caption,
            width=width,
            height=height,
        )

    def _normalize_mode(self, img: Image.Image) -> Image.Image:
        """Привести изображение к целевому режиму (например, RGB для JPEG)."""

        target_mode = self._settings.target_mode
        if img.mode == target_mode:
            return img.copy()
        return img.convert(target_mode)

    def _resize(self, img: Image.Image) -> Image.Image:
        """Уменьшить изображение под ограничения max_width/max_height (без увеличения)."""

        max_w = self._settings.max_width
        max_h = self._settings.max_height
        if max_w is None and max_h is None:
            return img

        width, height = img.size
        # Бесконечность для отсутствующего ограничения → масштаб по нему не считается.
        scale_w = max_w / width if max_w is not None else float("inf")
        scale_h = max_h / height if max_h is not None else float("inf")
        scale = min(scale_w, scale_h)
        if scale >= 1.0:
            return img

        new_size = (max(int(width * scale), 1), max(int(height * scale), 1))
        return img.resize(new_size, Image.Resampling.LANCZOS)

    def _target_path(self, out_path: Path) -> Path:
        """Подобрать корректный суффикс файла под выбранный формат."""

        suffix = _FORMAT_SUFFIX.get(
            self._settings.output_format.upper(),
            f".{self._settings.output_format.lower()}",
        )
        return out_path.with_suffix(suffix)


__all__ = ["ImageProcessor"]
