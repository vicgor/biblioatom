"""Use case: очистка рабочего каталога книги.

Удаляет кэшированное сырьё по выбранному объёму (:class:`CleanScope`), никогда
не трогая итоговый ``.epub``. Логика чистая: пути берутся из
:class:`BookWorkspace`, удаление — стандартными средствами; сбой I/O
оборачивается в :class:`WorkspaceError`.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from biblioatom.errors import WorkspaceError
from biblioatom.logging_config import get_logger
from biblioatom.services.workspace import BookWorkspace

_logger = get_logger(__name__)


class CleanScope(StrEnum):
    """Объём очистки рабочего каталога."""

    SCANS = "scans"
    RAW = "raw"
    ALL = "all"


@dataclass(slots=True)
class CleanResult:
    """Итог очистки: удалённые объекты и освобождённый объём."""

    removed: list[Path] = field(default_factory=list)
    freed_bytes: int = 0


def _tree_size(path: Path) -> int:
    """Суммарный размер файла или дерева каталога в байтах."""

    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _remove(path: Path) -> None:
    """Удалить файл или каталог; сбой I/O → :class:`WorkspaceError`."""

    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        raise WorkspaceError(
            "Failed to remove workspace entry.", context={"path": str(path)}
        ) from exc


def clean_workspace(
    workspace: BookWorkspace, scope: CleanScope = CleanScope.SCANS
) -> CleanResult:
    """Очистить рабочий каталог книги по выбранному объёму.

    * ``SCANS`` — только ``raw/scans/`` (тяжёлые сырые JPEG);
    * ``RAW`` — весь ``raw/`` (сырьё целиком);
    * ``ALL`` — всё содержимое каталога книги, кроме ``*.epub``.

    :raises WorkspaceError: каталог книги не существует или сбой удаления.
    """

    if not workspace.root.is_dir():
        raise WorkspaceError(
            "Book workspace does not exist.", context={"path": str(workspace.root)}
        )

    if scope is CleanScope.SCANS:
        targets = [workspace.scans_dir]
    elif scope is CleanScope.RAW:
        targets = [workspace.raw_dir]
    else:
        targets = [p for p in sorted(workspace.root.iterdir()) if p.suffix != ".epub"]

    result = CleanResult()
    for target in targets:
        if not target.exists():
            continue
        result.freed_bytes += _tree_size(target)
        _remove(target)
        result.removed.append(target)

    _logger.info(
        "clean_workspace.done",
        book_id=workspace.book_id,
        scope=str(scope),
        removed=[str(p) for p in result.removed],
        freed_bytes=result.freed_bytes,
    )
    return result


__all__ = ["CleanResult", "CleanScope", "clean_workspace"]
