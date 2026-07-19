"""Rich-реализация :class:`ProgressReporterProtocol` для CLI.

Рендерит прогресс-бары на stderr (``err_console``), чтобы stdout оставался
чистым для результатов и pipe. Используется как контекст-менеджер:
``__exit__`` гарантированно останавливает рендер и возвращает терминал в
нормальное состояние даже при исключении внутри use case.

Бары transient: по завершении фазы строка исчезает — единственным следом
работы команды остаётся её финальное ``✓``-сообщение.
"""

from __future__ import annotations

from types import TracebackType

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
)

from biblioatom.ui import err_console

#: Подписи фаз для пользователя. Core оперирует только ключами.
_PHASE_LABELS = {
    "pages": "Страницы",
    "scans": "Сканы",
    "images": "Иллюстрации",
}


class RichProgressReporter:
    """Прогресс-бары Rich поверх :class:`ProgressReporterProtocol`.

    :param console: консоль для рендера; по умолчанию ``err_console`` (stderr).
    """

    def __init__(self, *, console: Console | None = None) -> None:
        self._progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
            console=console or err_console,
            transient=True,
        )
        self._tasks: dict[str, TaskID] = {}
        self._totals: dict[str, int] = {}
        self._skipped: dict[str, int] = {}

    def __enter__(self) -> RichProgressReporter:
        self._progress.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._progress.stop()

    def start(self, phase: str, total: int) -> None:
        """Начать фазу; повторный start той же фазы заменяет её задачу."""

        existing = self._tasks.pop(phase, None)
        if existing is not None:
            self._progress.remove_task(existing)
        label = _PHASE_LABELS.get(phase, phase)
        self._tasks[phase] = self._progress.add_task(label, total=total)
        self._totals[phase] = total
        self._skipped[phase] = 0

    def advance(self, phase: str, *, skipped: bool = False) -> None:
        """Шаг фазы; неначатая фаза — no-op. ``skipped`` — шаг взят из кэша."""

        task_id = self._tasks.get(phase)
        if task_id is not None:
            self._progress.advance(task_id)
            if skipped:
                self._skipped[phase] = self._skipped.get(phase, 0) + 1

    def finish(self, phase: str) -> None:
        """Убрать индикатор фазы; целиком пропущенная из кэша фаза оставляет строку."""

        task_id = self._tasks.pop(phase, None)
        if task_id is None:
            return
        total = self._totals.pop(phase, 0)
        skipped = self._skipped.pop(phase, 0)
        self._progress.remove_task(task_id)
        if total > 0 and skipped == total:
            label = _PHASE_LABELS.get(phase, phase)
            self._progress.console.print(f"{label}: {total} из кэша", style="dim")


__all__ = ["RichProgressReporter"]
