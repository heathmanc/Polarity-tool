from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class TaskSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()


class ServiceTask(QRunnable):
    """Run a blocking hardware/vision call on Qt's global thread pool."""

    def __init__(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = TaskSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            result = self.function(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 - propagated to the HMI as a fault
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.completed.emit(result)
        finally:
            self.signals.finished.emit()
