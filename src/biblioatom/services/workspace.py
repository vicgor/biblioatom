"""Раскладка рабочего каталога книги (``books/<book_id>/``).

Чистый value-object без I/O-логики (кроме ``ensure_dirs``/``has_raw``):
инкапсулирует соглашение о путях, чтобы download/pipeline/clean и
``LocalFetcher`` не дублировали строки-форматы имён файлов.

Структура::

    <work_dir>/<book_id>/
    ├── raw/
    │   ├── meta.html            # сырой ответ страницы книги
    │   ├── toc.html             # сырой ответ p0
    │   ├── pages/p0000.json …   # сырой JSON RPC каждой страницы
    │   └── scans/0000.jpg …     # сырые JPEG-сканы (имя = CDN-номер)
    ├── book.json                # распарсенный артефакт
    ├── images/                  # обработанные кропы
    └── <book_id>.epub           # итоговая сборка
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class BookWorkspace:
    """Пути рабочего каталога одной книги."""

    work_dir: Path
    book_id: str

    @property
    def root(self) -> Path:
        return self.work_dir / self.book_id

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    @property
    def meta_path(self) -> Path:
        return self.raw_dir / "meta.html"

    @property
    def toc_path(self) -> Path:
        return self.raw_dir / "toc.html"

    @property
    def pages_dir(self) -> Path:
        return self.raw_dir / "pages"

    @property
    def scans_dir(self) -> Path:
        return self.raw_dir / "scans"

    @property
    def book_json_path(self) -> Path:
        return self.root / "book.json"

    @property
    def images_dir(self) -> Path:
        return self.root / "images"

    @property
    def epub_path(self) -> Path:
        return self.root / f"{self.book_id}.epub"

    def page_path(self, page: int) -> Path:
        """Путь сырого JSON RPC-ответа страницы ``page`` (0-based)."""
        return self.pages_dir / f"p{page:04d}.json"

    def scan_path(self, cdn_page: int) -> Path:
        """Путь сырого JPEG-скана по CDN-номеру страницы."""
        return self.scans_dir / f"{cdn_page:04d}.jpg"

    def has_raw(self) -> bool:
        """Есть ли скачанное сырьё (маркер — ``meta.html``)."""
        return self.meta_path.is_file()

    def ensure_dirs(self) -> None:
        """Создать каталоги сырья (идемпотентно)."""
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        self.scans_dir.mkdir(parents=True, exist_ok=True)


__all__ = ["BookWorkspace"]
