"""Централизованные экземпляры Rich Console.

Все модули используют единые ``Console`` — это обеспечивает консистентное
поведение ``--quiet``, ``$NO_COLOR`` и разделение потоков: пользовательский вывод
идёт в stdout, ошибки и диагностика — в stderr (не попадают в пайплайн).
"""

from __future__ import annotations

from rich.console import Console

#: Пользовательский вывод (результаты, прогресс) → stdout.
console = Console()

#: Ошибки и диагностика → stderr.
err_console = Console(stderr=True)


__all__ = ["console", "err_console"]
