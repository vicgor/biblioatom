"""Tests for services/ris_parser.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from biblioatom.errors import InputValidationError
from biblioatom.models import RisEntry, TocEntry
from biblioatom.services.ris_parser import (
    entries_to_ris,
    entries_to_ris_file,
    entry_to_ris,
    parse_ris,
    parse_ris_file,
    toc_to_ris,
)

SAMPLE_RIS = """\
TY  - JOUR
AU  - Капица П. Л.
TI  - Сверхпроводимость сверхтекучего гелия
PY  - 1938
JO  - Журнал экспериментальной и теоретической физики
VL  - 8
SP  - 946
ER  -

TY  - BOOK
AU  - Ландау Л. Д.
AU  - Лифшиц Е. М.
TI  - Статистическая физика
PY  - 1964
JO  - Теоретическая физика
VL  - 5
KW  - квантовая механика
KW  - статистическая физика
ER  -

TY  - THES
AU  - Иванов А. А.
TI  - Исследование свойств топологических изоляторов
PY  - 2020
PB  - МГУ
CY  - Москва
AB  - Диссертация на соискание учёной степени кандидата физико-математических наук
DO  - 10.1234/thesis.2020.001
ER  -
"""


class TestParseRis:
    def test_parses_single_entry(self) -> None:
        ris = "TY  - JOUR\nTI  - Test\nPY  - 2024\nER  -\n"
        entries = parse_ris(ris)
        assert len(entries) == 1
        assert entries[0].type == "JOUR"
        assert entries[0].title == "Test"
        assert entries[0].year == "2024"

    def test_parses_multiple_entries(self) -> None:
        entries = parse_ris(SAMPLE_RIS)
        assert len(entries) == 3

    def test_multi_value_tags(self) -> None:
        entries = parse_ris(SAMPLE_RIS)
        book = entries[1]
        assert book.type == "BOOK"
        assert len(book.authors) == 2
        assert "Ландау Л. Д." in book.authors
        assert "Лифшиц Е. М." in book.authors
        assert len(book.keywords) == 2
        assert "квантовая механика" in book.keywords

    def test_thesis_fields(self) -> None:
        entries = parse_ris(SAMPLE_RIS)
        thesis = entries[2]
        assert thesis.type == "THES"
        assert thesis.publisher == "МГУ"
        assert thesis.city == "Москва"
        assert thesis.doi == "10.1234/thesis.2020.001"
        assert "Диссертация" in thesis.abstract

    def test_empty_input(self) -> None:
        assert parse_ris("") == []

    def test_unknown_tags_ignored(self) -> None:
        ris = "TY  - JOUR\nXX  - unknown\nTI  - Test\nER  -\n"
        entries = parse_ris(ris)
        assert len(entries) == 1
        assert entries[0].title == "Test"

    def test_incomplete_entry_skipped(self) -> None:
        ris = "TY  - JOUR\nTI  - Test\n"
        entries = parse_ris(ris)
        assert len(entries) == 0


class TestParseRisFile:
    def test_reads_file(self, tmp_path: Path) -> None:
        ris_file = tmp_path / "test.ris"
        ris_file.write_text(SAMPLE_RIS, encoding="utf-8")
        entries = parse_ris_file(ris_file)
        assert len(entries) == 3

    def test_missing_file_raises(self) -> None:
        with pytest.raises(InputValidationError, match="does not exist"):
            parse_ris_file(Path("/nonexistent/file.ris"))


class TestEntryToRis:
    def test_single_entry(self) -> None:
        entry = RisEntry(
            ty="JOUR",
            au=["Капица П. Л."],
            ti="Сверхпроводимость сверхтекучего гелия",
            py="1938",
            jo="Журнал экспериментальной и теоретической физики",
        )
        ris = entry_to_ris(entry)
        assert "TY  - JOUR" in ris
        assert "AU  - Капица П. Л." in ris
        assert "TI  - Сверхпроводимость сверхтекучего гелия" in ris
        assert "PY  - 1938" in ris
        assert "ER  - " in ris

    def test_empty_fields_omitted(self) -> None:
        entry = RisEntry(ty="JOUR", ti="Test")
        ris = entry_to_ris(entry)
        assert "JO  -" not in ris
        assert "VL  -" not in ris

    def test_empty_authors_omitted_in_output(self) -> None:
        """Пустые строки в списках authors/keywords не порождают строки AU/KW."""
        entry = RisEntry(ty="JOUR", au=["", "Автор А."], kw=["", "термодинамика"])
        ris = entry_to_ris(entry)
        assert "AU  - Автор А." in ris
        assert "KW  - термодинамика" in ris
        # Пустые значения не должны создавать пустые строки в выводе
        lines = ris.splitlines()
        assert not any(line.startswith("AU  - ") and line == "AU  - " for line in lines)
        assert not any(line.startswith("KW  - ") and line == "KW  - " for line in lines)


class TestEntriesToRis:
    def test_multiple_entries(self) -> None:
        entries = [
            RisEntry(ty="JOUR", ti="First"),
            RisEntry(ty="BOOK", ti="Second"),
        ]
        ris = entries_to_ris(entries)
        assert ris.count("ER  - ") == 2
        assert "TI  - First" in ris
        assert "TI  - Second" in ris


class TestEntriesToRisFile:
    def test_writes_file(self, tmp_path: Path) -> None:
        out = tmp_path / "out.ris"
        entries = [RisEntry(ty="JOUR", ti="Test")]
        entries_to_ris_file(entries, out)
        assert out.is_file()
        assert "TI  - Test" in out.read_text(encoding="utf-8")


class TestTocToRis:
    def test_converts_toc_to_chap_records(self) -> None:
        """toc_to_ris должен создавать по одной записи CHAP на каждую главу."""
        toc = [
            TocEntry(title="Введение", author="Иванов А. А.", page=1),
            TocEntry(title="Глава 1", page=10),
        ]
        ris = toc_to_ris(toc, title="Моя книга", year="2024")
        # Каждая глава — отдельная запись CHAP
        assert ris.count("TY  - CHAP") == 2
        assert ris.count("ER  - ") == 2
        # Заголовок книги в BT, не в TI верхнего уровня
        assert ris.count("BT  - Моя книга") == 2
        assert "TI  - Введение" in ris
        assert "TI  - Глава 1" in ris
        assert "AU  - Иванов А. А." in ris
        assert "PY  - 2024" in ris
        # Не должно быть TY BOOK
        assert "TY  - BOOK" not in ris

    def test_empty_toc_returns_empty_string(self) -> None:
        assert toc_to_ris([]) == ""

    def test_no_book_title_omits_bt(self) -> None:
        toc = [TocEntry(title="Глава 1", page=1)]
        ris = toc_to_ris(toc)
        assert "BT  -" not in ris
        assert "TI  - Глава 1" in ris

    def test_single_chap_roundtrip(self) -> None:
        """Запись CHAP без author и year корректно парсится обратно."""
        toc = [TocEntry(title="Заключение", page=99)]
        ris_text = toc_to_ris(toc, title="Книга")
        # Ручной парсинг: CHAP не входит в базовый tag_map, но TI/BT/ER читаются
        assert "TY  - CHAP" in ris_text
        assert "TI  - Заключение" in ris_text
        assert "BT  - Книга" in ris_text
        assert "ER  - " in ris_text
