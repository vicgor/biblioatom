"""Извлечение фото/иллюстраций со сканов через OpenCV (green-field, без OCR).

Реализует :class:`~biblioatom.services.ScanExtractorProtocol`. Пайплайн:

    grayscale → GaussianBlur → бинаризация (Otsu) и/или Canny → морфология
    (close/dilate) → findContours → фильтр (площадь / aspect / fill / rect) →
    boundingRect → crop.

Текстовый шум (мелкие/вытянутые/неплотные контуры) отсеивается фильтрами,
параметры которых берутся из :class:`~biblioatom.config.ScanExtractionSettings`.
Зависимость от OpenCV/numpy изолирована в этом модуле; use case оперирует только
доменными моделями (:class:`~biblioatom.models.ExtractedImage`).
"""

from __future__ import annotations

import time

import cv2
import numpy as np
from cv2.typing import MatLike

from biblioatom.config import ScanExtractionSettings
from biblioatom.errors import ScanExtractionError
from biblioatom.logging_config import get_logger
from biblioatom.models import BoundingBox, ExtractedImage

_logger = get_logger(__name__)

# Кодек по умолчанию для сохранения кропа в байты (PNG — без потерь).
_CROP_ENCODE_EXT = ".png"


class ScanExtractor:
    """Извлекатель иллюстраций на OpenCV, реализующий ``ScanExtractorProtocol``."""

    def __init__(self, settings: ScanExtractionSettings | None = None) -> None:
        self._settings = settings or ScanExtractionSettings()

    # -- публичный API ----------------------------------------------------

    def extract(self, image: bytes, page: int) -> list[ExtractedImage]:
        """Найти прямоугольные иллюстрации на скане и вернуть их кропы.

        :param image: закодированные байты исходного скана (PNG/JPEG).
        :param page: номер страницы для проставления в результат.
        :raises ScanExtractionError: при ошибке декодирования или обработки.
        """

        started = time.perf_counter()
        try:
            scan = self._decode(image)
            boxes = self._detect_boxes(scan)
            crops = [self._crop(scan, box, page) for box in boxes]
        except ScanExtractionError:
            raise
        except cv2.error as exc:
            raise ScanExtractionError(
                "OpenCV failed to process the scan.",
                context={"page": page, "error": str(exc)},
            ) from exc

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        _logger.info(
            "scan_extractor.done",
            page=page,
            found=len(crops),
            duration_ms=duration_ms,
        )
        return crops

    # -- этапы пайплайна --------------------------------------------------

    def _decode(self, image: bytes) -> MatLike:
        """Декодировать байты в BGR-массив OpenCV."""

        if not image:
            raise ScanExtractionError("Empty scan bytes.", context={"size": 0})
        buffer = np.frombuffer(image, dtype=np.uint8)
        scan = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if scan is None:
            raise ScanExtractionError(
                "Failed to decode scan bytes into an image.",
                context={"size": len(image)},
            )
        return scan

    def _binarize(self, scan: MatLike) -> MatLike:
        """Свести скан к бинарной маске объектов (Otsu и/или Canny + морфология)."""

        gray = cv2.cvtColor(scan, cv2.COLOR_BGR2GRAY)
        k = self._settings.blur_kernel
        blurred = cv2.GaussianBlur(gray, (k, k), 0)

        # Otsu даёт объекты (тёмное на светлом листе) белыми за счёт инверсии.
        _, otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        mask = otsu

        if self._settings.use_canny:
            edges = cv2.Canny(
                blurred,
                self._settings.canny_threshold1,
                self._settings.canny_threshold2,
            )
            # Объединяем заливку Otsu и контуры Canny: рамки фото становятся
            # сплошными, что устойчивее к фону/градиентам внутри иллюстрации.
            mask = cv2.bitwise_or(otsu, edges)

        # Морфология «закрывает» разрывы внутри фото и склеивает рамку в один
        # контур; текстовые штрихи при этом не сливаются в крупный прямоугольник.
        m = self._settings.morph_kernel
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (m, m))
        closed = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            kernel,
            iterations=self._settings.morph_iterations,
        )
        return closed.astype(np.uint8)

    def _binarize_dark_regions(self, scan: MatLike) -> MatLike:
        """Найти тёмные области (фото/иллюстрации) на фоне светлого листа.

        Используется как fallback когда стандартная бинаризация не находит
        достаточно крупных контуров. Фото обычно темнее текста и фона.
        """
        gray = cv2.cvtColor(scan, cv2.COLOR_BGR2GRAY)
        k = self._settings.blur_kernel
        blurred = cv2.GaussianBlur(gray, (k, k), 0)

        # Адаптивная пороговая фильтрация для выделения тёмных областей.
        s = self._settings
        mask = cv2.adaptiveThreshold(
            blurred,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            s.adaptive_block_size,
            s.adaptive_c,
        )

        # Морфология: закрыть разрывы, убрать мелкий шум
        m = s.morph_kernel
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (m, m))
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=s.dark_morph_close_iter)

        # Убрать мелкие объекты (шум)
        ko = s.dark_open_kernel
        kernel_open = cv2.getStructuringElement(cv2.MORPH_RECT, (ko, ko))
        closed = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel_open, iterations=1)

        return closed.astype(np.uint8)

    def _detect_boxes(self, scan: MatLike) -> list[BoundingBox]:
        """Найти и отфильтровать прямоугольные области-кандидаты."""

        mask = self._binarize(scan)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        page_area = float(scan.shape[0] * scan.shape[1])
        boxes: list[BoundingBox] = []
        for contour in contours:
            box = self._accept_contour(contour, page_area)
            if box is not None:
                boxes.append(box)

        # Fallback 1: детекция тёмных областей (фото на светлом фоне).
        if not boxes:
            mask_dark = self._binarize_dark_regions(scan)
            contours_dark, _ = cv2.findContours(
                mask_dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            for contour in contours_dark:
                box = self._accept_contour(contour, page_area)
                if box is not None:
                    boxes.append(box)

        # Fallback 2: поиск крупных тёмных областей без чётких рамок.
        # Если fallback 1 нашёл только мелкие объекты, ищем по-другому.
        small_ratio = self._settings.small_region_area_ratio
        if not boxes or all((b.width * b.height) / page_area < small_ratio for b in boxes):
            large_boxes = self._detect_large_dark_regions(scan, page_area)
            if large_boxes:
                boxes = large_boxes

        # Стабильный порядок: сверху-вниз, затем слева-направо.
        boxes.sort(key=lambda b: (b.y, b.x))
        return boxes

    def _detect_large_dark_regions(self, scan: MatLike, page_area: float) -> list[BoundingBox]:
        """Найти крупные тёмные области (фото) без чётких рамок.

        Используется как последний fallback когда стандартные методы не нашли
        иллюстраций. Фото имеет фон темнее белых полей страницы, но светлее текста.
        """
        gray = cv2.cvtColor(scan, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        s = self._settings
        m = s.margin_px
        # Определяем уровень белого по полям страницы (крайние margin_px px)
        margins = np.concatenate(
            [
                gray[:, :m].ravel(),
                gray[:, -m:].ravel(),
                gray[:m, :].ravel(),
                gray[-m:, :].ravel(),
            ]
        )
        white_level = float(np.percentile(margins, s.white_percentile))

        # Фото: яркость ниже белого поля, но выше текста
        photo_mask = (gray < white_level - s.white_offset) & (gray > s.dark_lower_bound)

        # Морфология: убрать мелкий шум, склеить близкие пиксели
        ko = self._settings.dark_open_kernel
        kernel_open = np.ones((ko, ko), np.uint8)
        cleaned = cv2.morphologyEx((photo_mask * 255).astype(np.uint8), cv2.MORPH_OPEN, kernel_open)

        # Найти контуры без агрессивной дилатации
        contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Группируем похожие контуры
        boxes = self._merge_nearby_contours(list(contours), h, w, page_area)

        return boxes

    def _merge_nearby_contours(
        self, contours: list[MatLike], h: int, w: int, page_area: float
    ) -> list[BoundingBox]:
        """Сгруппировать близкие контуры в прямоугольные области-фото."""
        candidates: list[tuple[int, int, int, int, float]] = []

        for contour in contours:
            x, y, cw, ch = cv2.boundingRect(contour)
            area = cv2.contourArea(contour)

            if area < self._settings.min_contour_area:
                continue

            # Не на самых краях (заголовки/колонтитулы)
            m = self._settings.margin_px
            if y < m or y + ch > h - m:
                continue

            candidates.append((x, y, cw, ch, area))

        if not candidates:
            return []

        # Группируем по вертикальной близости (< merge_gap_px)
        candidates.sort(key=lambda c: (c[1], c[0]))
        groups: list[list[tuple[int, int, int, int, float]]] = []
        current_group = [candidates[0]]

        for c in candidates[1:]:
            prev_y2 = current_group[-1][1] + current_group[-1][3]
            if c[1] - prev_y2 < self._settings.merge_gap_px:
                current_group.append(c)
            else:
                groups.append(current_group)
                current_group = [c]
        groups.append(current_group)

        # Для каждой группы берём объединённый bounding box
        boxes: list[BoundingBox] = []
        for group in groups:
            x_min = min(c[0] for c in group)
            y_min = min(c[1] for c in group)
            x_max = max(c[0] + c[2] for c in group)
            y_max = max(c[1] + c[3] for c in group)
            cw = x_max - x_min
            ch = y_max - y_min

            area_ratio = (cw * ch) / page_area
            if area_ratio >= self._settings.min_area_ratio:
                boxes.append(BoundingBox(x=x_min, y=y_min, width=cw, height=ch))

        return boxes

    def _accept_contour(self, contour: MatLike, page_area: float) -> BoundingBox | None:
        """Применить фильтры площади/aspect/fill/rectangularity к контуру.

        Возвращает :class:`BoundingBox`, если контур похож на иллюстрацию, иначе
        ``None`` (контур отбрасывается как текстовый шум).
        """

        s = self._settings
        x, y, w, h = cv2.boundingRect(contour)
        if w <= 0 or h <= 0:
            return None

        bbox_area = float(w * h)
        area_ratio = bbox_area / page_area if page_area > 0 else 0.0
        if area_ratio < s.min_area_ratio or area_ratio > s.max_area_ratio:
            return None

        aspect = w / h
        if aspect < s.min_aspect or aspect > s.max_aspect:
            return None

        contour_area = float(cv2.contourArea(contour))
        # extent (fill) — доля заполнения bounding box контуром: у плотного фото
        # близко к 1, у текстового штриха/линии — мало.
        extent = contour_area / bbox_area if bbox_area > 0 else 0.0
        if extent < s.min_fill_ratio:
            return None

        # rectangularity — насколько контур близок к минимальному повёрнутому
        # прямоугольнику; отсеивает кривые/диагональные кляксы.
        (_, _), (rw, rh), _ = cv2.minAreaRect(contour)
        rect_area = rw * rh
        rectangularity = contour_area / rect_area if rect_area > 0 else 0.0
        if rectangularity < s.min_rectangularity:
            return None

        return BoundingBox(x=x, y=y, width=w, height=h)

    def _crop(self, scan: MatLike, box: BoundingBox, page: int) -> ExtractedImage:
        """Вырезать область с паддингом и закодировать в байты."""

        pad = self._settings.crop_padding
        height, width = scan.shape[:2]
        x0 = max(box.x - pad, 0)
        y0 = max(box.y - pad, 0)
        x1 = min(box.x + box.width + pad, width)
        y1 = min(box.y + box.height + pad, height)

        crop = scan[y0:y1, x0:x1]
        ok, encoded = cv2.imencode(_CROP_ENCODE_EXT, crop)
        if not ok:
            raise ScanExtractionError(
                "Failed to encode the cropped illustration.",
                context={"page": page, "box": box.model_dump()},
            )

        return ExtractedImage(
            page=page,
            data=encoded.tobytes(),
            box=BoundingBox(x=x0, y=y0, width=x1 - x0, height=y1 - y0),
        )


__all__ = ["ScanExtractor"]
