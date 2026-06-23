"""Парсер и экспортёр библиографических записей в формате RIS.

Формат RIS (Research Information Systems) — стандартизированный текстовый формат
для обмена библиографическими ссылками. Используется EndNote, Zotero, Mendeley
и другими менеджерами ссылок.

Каждая запись состоит из строк ``TAG  - value`` и завершается строкой ``ER  - ``::

    TY  - JOUR
    AU  - Капица П. Л.
    TI  - Сверхпроводимость сверхтекучего гелия
    PY  - 1938
    JO  - Журнал экспериментальной и теоретической физики
    ER  -
"""

from __future__ import annotations

from pathlib import Path

from biblioatom.errors import InputValidationError
from biblioatom.models import RisEntry, TocEntry


def parse_ris(text: str) -> list[RisEntry]:
    """Разобрать текст в формате RIS и вернуть список записей.

    Мультистрочные теги (AU, KW и др.) объединяются в списки.
    Неизвестные теги игнорируются.
    """
    entries: list[RisEntry] = []
    current: dict[str, list[str]] = {}
    tag_map: dict[str, str] = {
        "ty": "ty",
        "au": "au",
        "ti": "ti",
        "py": "py",
        "jo": "jo",
        "vl": "vl",
        "is": "is_",
        "sp": "sp",
        "ab": "ab",
        "kw": "kw",
        "do": "do",
        "ur": "ur",
        "pb": "pb",
        "cy": "cy",
        "n1": "n1",
    }
    multi_tags = {"au", "kw"}

    for line in text.splitlines():
        line = line.rstrip("\n\r")
        if len(line) < 5:
            continue
        if line[2:6] != "  - " and line != "ER  -":
            continue
        tag = line[:2].strip().lower()
        value = line[6:].strip() if len(line) > 6 else ""

        if tag == "er" and not current:
            continue

        if tag == "er":
            mapped: dict[str, str | list[str]] = {}
            for t, vals in current.items():
                if t in multi_tags:
                    mapped[t] = [v for v in vals if v]
                else:
                    mapped[t] = " ".join(vals) if vals else ""
            try:
                entries.append(RisEntry.model_validate(mapped))
            except Exception as exc:
                raise InputValidationError(
                    "Failed to parse RIS entry.",
                    context={"tag": tag, "detail": str(exc)},
                ) from exc
            current = {}
            continue

        if tag in tag_map:
            current.setdefault(tag_map[tag], []).append(value)

    return entries


def parse_ris_file(path: Path) -> list[RisEntry]:
    """Прочитать и разобрать RIS-файл."""
    if not path.is_file():
        raise InputValidationError(
            "RIS file does not exist.",
            context={"path": str(path)},
        )
    text = path.read_text(encoding="utf-8")
    return parse_ris(text)


def _format_ris_line(tag: str, value: str) -> str:
    """Отформатировать одну строку RIS."""
    return f"{tag}  - {value}" if value else ""


def entry_to_ris(entry: RisEntry) -> str:
    """Конвертировать одну RisEntry в текст RIS."""
    lines: list[str] = []
    if entry.type:
        lines.append(_format_ris_line("TY", entry.type))
    for author in entry.authors:
        lines.append(_format_ris_line("AU", author))
    if entry.title:
        lines.append(_format_ris_line("TI", entry.title))
    if entry.year:
        lines.append(_format_ris_line("PY", entry.year))
    if entry.journal:
        lines.append(_format_ris_line("JO", entry.journal))
    if entry.volume:
        lines.append(_format_ris_line("VL", entry.volume))
    if entry.issue:
        lines.append(_format_ris_line("IS", entry.issue))
    if entry.pages:
        lines.append(_format_ris_line("SP", entry.pages))
    if entry.abstract:
        lines.append(_format_ris_line("AB", entry.abstract))
    for kw in entry.keywords:
        lines.append(_format_ris_line("KW", kw))
    if entry.doi:
        lines.append(_format_ris_line("DO", entry.doi))
    if entry.url:
        lines.append(_format_ris_line("UR", entry.url))
    if entry.publisher:
        lines.append(_format_ris_line("PB", entry.publisher))
    if entry.city:
        lines.append(_format_ris_line("CY", entry.city))
    if entry.notes:
        lines.append(_format_ris_line("N1", entry.notes))
    lines.append("ER  - ")
    return "\n".join(lines)


def entries_to_ris(entries: list[RisEntry]) -> str:
    """Конвертировать список RisEntry в текст RIS."""
    return "\n\n".join(entry_to_ris(e) for e in entries)


def entries_to_ris_file(entries: list[RisEntry], path: Path) -> None:
    """Записать список RisEntry в RIS-файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(entries_to_ris(entries), encoding="utf-8")


def toc_to_ris(toc: list[TocEntry], *, title: str = "", year: str = "") -> str:
    """Конвертировать список TocEntry (оглавление книги) в текст RIS.

    Создаёт отдельную запись типа ``CHAP`` на каждую главу оглавления.
    Название книги помещается в тег ``BT`` (Book Title), что соответствует
    стандарту RIS для глав из книг и корректно импортируется в Zotero/Mendeley.

    Ранее функция создавала одну запись ``BOOK`` с несколькими тегами ``TI`` —
    это нарушало стандарт RIS (один ``TI`` на запись).
    """
    records: list[str] = []
    for entry in toc:
        lines: list[str] = ["TY  - CHAP"]
        if entry.title:
            lines.append(_format_ris_line("TI", entry.title))
        if entry.author:
            lines.append(_format_ris_line("AU", entry.author))
        if title:
            lines.append(_format_ris_line("BT", title))
        if year:
            lines.append(_format_ris_line("PY", year))
        lines.append("ER  - ")
        records.append("\n".join(lines))
    return "\n\n".join(records)


__all__ = [
    "entries_to_ris",
    "entries_to_ris_file",
    "entry_to_ris",
    "parse_ris",
    "parse_ris_file",
    "toc_to_ris",
]
