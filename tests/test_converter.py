"""Тесты конвертера EPUB→AZW3 (``services/converter.py``).

``subprocess.run`` и ``shutil.which`` мокируются — реальный Calibre не нужен.
Проверяется: успех (returncode 0), сбой (ExternalToolExecutionError со stderr),
отсутствие бинаря (ExternalToolNotFoundError), таймаут и то, что вызов идёт
БЕЗ ``shell=True`` с корректным списком аргументов.

Реальный вызов ``ebook-convert`` выполняется только при наличии бинаря
(``skipif``).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from biblioatom.config import ConversionSettings
from biblioatom.errors import ExternalToolExecutionError, ExternalToolNotFoundError
from biblioatom.services.converter import EbookConvertConverter


class _Completed:
    """Заглушка результата ``subprocess.run``."""

    def __init__(self, returncode: int, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = stderr


def test_convert_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/ebook-convert")
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> _Completed:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _Completed(0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    source = tmp_path / "book.epub"
    source.write_bytes(b"epub")
    target = tmp_path / "out" / "book.azw3"

    result = EbookConvertConverter().convert(source, target)

    assert result == target
    # Вызов без shell, аргументы — список [binary, source, target].
    assert "shell" not in captured["kwargs"]
    assert captured["cmd"] == ["/usr/bin/ebook-convert", str(source), str(target)]
    # Целевая директория создаётся.
    assert target.parent.is_dir()


def test_convert_failure_raises_with_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/ebook-convert")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: _Completed(1, stderr="conversion boom"),
    )

    source = tmp_path / "book.epub"
    source.write_bytes(b"epub")
    target = tmp_path / "book.azw3"

    with pytest.raises(ExternalToolExecutionError) as exc_info:
        EbookConvertConverter().convert(source, target)

    assert exc_info.value.context["stderr"] == "conversion boom"
    assert exc_info.value.context["returncode"] == 1


def test_convert_missing_binary_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)

    # subprocess.run не должен вызываться при отсутствии бинаря.
    def fail_run(*args: Any, **kwargs: Any) -> _Completed:  # pragma: no cover
        raise AssertionError("subprocess.run must not be called")

    monkeypatch.setattr(subprocess, "run", fail_run)

    with pytest.raises(ExternalToolNotFoundError):
        EbookConvertConverter().convert(tmp_path / "b.epub", tmp_path / "b.azw3")


def test_convert_timeout_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/ebook-convert")

    def raise_timeout(cmd: list[str], **kwargs: Any) -> _Completed:
        raise subprocess.TimeoutExpired(cmd, timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    source = tmp_path / "book.epub"
    source.write_bytes(b"epub")

    with pytest.raises(ExternalToolExecutionError) as exc_info:
        EbookConvertConverter(ConversionSettings(timeout=1.0)).convert(
            source, tmp_path / "book.azw3"
        )
    assert exc_info.value.context["timeout"] == 1.0


def test_custom_binary_name_from_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def fake_which(name: str) -> str | None:
        seen["name"] = name
        return None

    monkeypatch.setattr(shutil, "which", fake_which)

    settings = ConversionSettings(ebook_convert_bin="my-convert")
    with pytest.raises(ExternalToolNotFoundError):
        EbookConvertConverter(settings).convert(tmp_path / "b.epub", tmp_path / "b.azw3")
    assert seen["name"] == "my-convert"


@pytest.mark.skipif(
    shutil.which("ebook-convert") is None,
    reason="Calibre 'ebook-convert' не установлен",
)
def test_real_ebook_convert(tmp_path: Path) -> None:
    """Реальная конвертация при наличии Calibre (иначе тест пропускается)."""

    from biblioatom.models import (
        BookElement,
        ElementKind,
        StructuredChapter,
        StructuredDocument,
    )
    from biblioatom.services.epub_builder import EpubBuilder

    doc = StructuredDocument(
        title="T",
        book_id="b",
        chapters=[
            StructuredChapter(
                title="C",
                elements=[BookElement(kind=ElementKind.NOTE, text="hi", page=0)],
            )
        ],
    )
    epub_path = tmp_path / "b.epub"
    EpubBuilder().build(doc, epub_path)

    azw3 = tmp_path / "b.azw3"
    result = EbookConvertConverter().convert(epub_path, azw3)
    assert result.exists()
    assert result.stat().st_size > 0
