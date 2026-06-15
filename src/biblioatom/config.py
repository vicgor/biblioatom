"""Конфигурация приложения на pydantic-settings.

Настройки сгруппированы по доменам (App/Http/Parsing/Structure/ScanExtraction/
Image/Epub/Conversion/Logging). Значения читаются из переменных окружения с
префиксом ``BIBLIOATOM_`` и вложенным разделителем ``__`` либо из файла ``.env``.

Пример переопределения вложенного поля::

    BIBLIOATOM_HTTP__TIMEOUT=60
    BIBLIOATOM_LOGGING__LEVEL=DEBUG
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    """Настройки извлечения иллюстраций со сканов (OpenCV)."""

    blur_kernel: int = Field(default=5, ge=1)
    min_area_ratio: float = Field(default=0.02, ge=0, le=1)
    max_area_ratio: float = Field(default=0.9, ge=0, le=1)
    min_aspect: float = Field(default=0.2, gt=0)
    max_aspect: float = Field(default=5.0, gt=0)
    min_fill_ratio: float = Field(default=0.5, ge=0, le=1)


class ImageSettings(BaseModel):
    """Настройки постобработки изображений (Pillow)."""

    output_format: str = "JPEG"
    quality: int = Field(default=85, ge=1, le=100)
    max_width: int | None = Field(default=1600, ge=1)
    max_height: int | None = Field(default=2400, ge=1)


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

    return Settings()


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
