"""Тесты use case конвертации EPUB→AZW3 (``core/convert_to_azw3.py``).

Converter мокируется через ``ConverterProtocol`` (Dependency Inversion).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from biblioatom.core.convert_to_azw3 import convert_to_azw3
from biblioatom.errors import ExternalToolExecutionError
from biblioatom.services import ConverterProtocol


class _FakeConverter:
    """Мок-converter, реализующий ``ConverterProtocol``."""

    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc
        self.calls: list[tuple[Path, Path]] = []

    def convert(self, source: Path, target: Path) -> Path:
        self.calls.append((source, target))
        if self._exc is not None:
            raise self._exc
        return target


def test_convert_to_azw3_delegates(tmp_path: Path) -> None:
    converter = _FakeConverter()
    assert isinstance(converter, ConverterProtocol)

    source = tmp_path / "b.epub"
    target = tmp_path / "b.azw3"

    result = convert_to_azw3(converter, source, target, book_id="b")

    assert converter.calls == [(source, target)]
    assert result.outputs == [target]
    assert result.book_id == "b"


def test_convert_to_azw3_propagates_errors(tmp_path: Path) -> None:
    converter = _FakeConverter(exc=ExternalToolExecutionError("boom"))
    with pytest.raises(ExternalToolExecutionError):
        convert_to_azw3(converter, tmp_path / "b.epub", tmp_path / "b.azw3")
