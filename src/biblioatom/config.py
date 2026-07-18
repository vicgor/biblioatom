"""Конфигурация приложения на pydantic-settings.

Настройки сгруппированы по доменам (App/Http/Parsing/Structure/ScanExtraction/
Image/Epub/Conversion/Logging). Значения читаются из переменных окружения с
префиксом ``BIBLIOATOM_`` и вложенным разделителем ``__`` либо из файла ``.env``.

Пример переопределения вложенного поля::

    BIBLIOATOM_HTTP__TIMEOUT=60
    BIBLIOATOM_LOGGING__LEVEL=DEBUG
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from biblioatom.errors import ConfigurationError


class AppSettings(BaseModel):
    """Общие настройки приложения."""

    base_url: str = "https://elib.biblioatom.ru"
    rpc_path: str = "/rpc/bookviewer/cp/"
    output_dir: str = "output"
    user_agent: str = "biblioatom/0.2 (+https://github.com/vicgor/biblioatom)"


class HttpSettings(BaseModel):
    """Настройки HTTP-клиента и политики ретраев."""

    timeout: float = Field(default=30.0, gt=0)
    connect_timeout: float = Field(default=10.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    backoff_factor: float = Field(default=0.5, ge=0)
    backoff_max: float = Field(default=10.0, ge=0)
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)
    delay_ms: int = Field(default=300, ge=0)


class ParsingSettings(BaseModel):
    """Настройки HTML-парсинга и CSS-селекторов."""

    fallback_max_page: int = Field(default=545, ge=1)
    toc_selector: str = 'aside[data-type="tree-box-contents"]'
    page_text_selector: str = "div.pagetext"
    img_block_selector: str = "p.img"
    footnote_selector: str = "p.ftn"


class StructureSettings(BaseModel):
    """Настройки структурного анализа (разбивка на главы)."""

    min_chapter_pages: int = Field(default=1, ge=0)
    merge_empty_front_matter: bool = True
    heading_max_words: int = Field(default=12, ge=1)


class ScanExtractionSettings(BaseModel):
    """Настройки извлечения иллюстраций со сканов (OpenCV).

    Фильтры контуров отбрасывают текстовый шум и оставляют прямоугольные
    фото/иллюстрации:

    * площадь — доля от площади страницы (``min_area_ratio``/``max_area_ratio``);
    * соотношение сторон ``aspect = w / h`` (``min_aspect``/``max_aspect``);
    * заполнение (``extent = area / (w*h)``) — ``min_fill_ratio``;
    * прямоугольность (``area / area(minAreaRect)``) — ``min_rectangularity``;
    * паддинг кропа в пикселях — ``crop_padding``.
    """

    blur_kernel: int = Field(default=5, ge=1)
    min_area_ratio: float = Field(default=0.012, ge=0, le=1)
    max_area_ratio: float = Field(default=0.9, ge=0, le=1)
    min_aspect: float = Field(default=0.2, gt=0)
    max_aspect: float = Field(default=5.0, gt=0)
    min_fill_ratio: float = Field(default=0.52, ge=0, le=1)
    min_rectangularity: float = Field(default=0.72, ge=0, le=1)
    use_canny: bool = True
    canny_threshold1: float = Field(default=50.0, ge=0)
    canny_threshold2: float = Field(default=150.0, ge=0)
    morph_kernel: int = Field(default=9, ge=1)
    morph_iterations: int = Field(default=2, ge=0)
    crop_padding: int = Field(default=4, ge=0)
    merge_gap_px: int = Field(
        default=100,
        ge=1,
        description="Порог вертикальной близости контуров при группировке (пикселей).",
    )
    min_contour_area: int = Field(
        default=40000,
        ge=1,
        description="Минимальная площадь контура в пикселях (отсев текстового шума).",
    )
    margin_px: int = Field(
        default=50,
        ge=1,
        description=(
            "Ширина полей страницы для оценки уровня белого и исключения колонтитулов (пикселей)."
            " Должна быть ≥ 1: при 0 срез gray[:, -0:] вернул бы всю страницу, а не поле."
        ),
    )
    white_percentile: float = Field(
        default=95.0,
        ge=50.0,
        le=100.0,
        description="Перцентиль яркости полей для определения уровня белого.",
    )
    white_offset: float = Field(
        default=35.0,
        ge=0.0,
        description=(
            "Отступ от уровня белого вниз: пиксели темнее"
            " (white_level - white_offset) считаются фото."
        ),
    )
    dark_lower_bound: int = Field(
        default=55,
        ge=0,
        le=255,
        description="Нижний порог яркости фото-пикселей (отсев чёрного текста/артефактов).",
    )
    # _binarize_dark_regions
    adaptive_block_size: int = Field(
        default=51,
        ge=3,
        description="Размер окна адаптивной бинаризации (нечётное число).",
    )
    adaptive_c: float = Field(
        default=10.0,
        description="Константа C для adaptiveThreshold (вычитается из среднего).",
    )
    dark_morph_close_iter: int = Field(
        default=2,
        ge=1,
        description="Число итераций морфологического закрытия в _binarize_dark_regions.",
    )
    dark_open_kernel: int = Field(
        default=5,
        ge=1,
        description=(
            "Размер ядра MORPH_OPEN для удаления шума"
            " в _binarize_dark_regions и _detect_large_dark_regions."
        ),
    )
    # _detect_boxes (fallback 2)
    small_region_area_ratio: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description=(
            "Порог площади (доля от страницы) ниже которого fallback-1 объект считается 'мелким'."
        ),
    )
    # fallback 3: вернуть весь скан как одно изображение, если ничего не найдено
    full_image_fallback: bool = Field(
        default=True,
        description=(
            "Если все методы детекции вернули пусто — вернуть весь скан как одно изображение."
        ),
    )

    @field_validator("blur_kernel", "morph_kernel", "adaptive_block_size")
    @classmethod
    def _validate_odd_kernel(cls, v: int) -> int:
        """Размер ядра OpenCV должен быть нечётным (требование GaussianBlur/adaptiveThreshold)."""

        if v % 2 == 0:
            raise ValueError("Размер ядра должен быть нечётным.")
        return v


class ImageSettings(BaseModel):
    """Настройки постобработки изображений (Pillow)."""

    output_format: str = "JPEG"
    quality: int = Field(default=85, ge=1, le=100)
    max_width: int | None = Field(default=1600, ge=1)
    max_height: int | None = Field(default=2400, ge=1)
    target_mode: str = "RGB"


class EpubSettings(BaseModel):
    """Настройки сборки EPUB."""

    language: str = "ru"
    epub_version: int = Field(default=3, ge=2, le=3)
    embed_images: bool = True
    css: str | None = None


class ConversionSettings(BaseModel):
    """Настройки конвертации форматов через внешний инструмент."""

    ebook_convert_bin: str = "ebook-convert"
    timeout: float = Field(default=300.0, gt=0)


class LoggingSettings(BaseModel):
    """Настройки логирования (structlog)."""

    level: str = "INFO"
    json_logs: bool = False

    @field_validator("level")
    @classmethod
    def _validate_level(cls, v: str) -> str:
        """Проверить, что уровень — один из допустимых имён logging."""

        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        normalized = v.upper()
        if normalized not in allowed:
            raise ValueError(
                f"Недопустимый уровень логирования {v!r}. "
                f"Допустимые значения: {', '.join(sorted(allowed))}."
            )
        return normalized


class Settings(BaseSettings):
    """Корневые настройки приложения с вложенными группами."""

    model_config = SettingsConfigDict(
        env_prefix="BIBLIOATOM_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app: AppSettings = Field(default_factory=AppSettings)
    http: HttpSettings = Field(default_factory=HttpSettings)
    parsing: ParsingSettings = Field(default_factory=ParsingSettings)
    structure: StructureSettings = Field(default_factory=StructureSettings)
    scan_extraction: ScanExtractionSettings = Field(default_factory=ScanExtractionSettings)
    image: ImageSettings = Field(default_factory=ImageSettings)
    epub: EpubSettings = Field(default_factory=EpubSettings)
    conversion: ConversionSettings = Field(default_factory=ConversionSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


def get_settings() -> Settings:
    """Собрать настройки из окружения/``.env`` со значениями по умолчанию."""

    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigurationError(
            "Invalid configuration. Check environment variables or .env file.",
            context={"details": exc.errors(include_url=False)},
        ) from exc


__all__ = [
    "AppSettings",
    "ConversionSettings",
    "EpubSettings",
    "HttpSettings",
    "ImageSettings",
    "LoggingSettings",
    "ParsingSettings",
    "ScanExtractionSettings",
    "Settings",
    "StructureSettings",
    "get_settings",
]
