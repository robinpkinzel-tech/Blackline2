"""Hintergrund-Threads, damit die Oberfläche bei OCR/KI/Export nicht einfriert."""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from blackline2.loader import Cancelled


class Worker(QThread):
    """Führt fn(progress, cancel, *args) in einem eigenen Thread aus."""

    progress = Signal(int, int, str)
    succeeded = Signal(object)
    failed = Signal(str, str)  # Meldung, Details
    cancelled = Signal()

    def __init__(self, fn: Callable[..., Any], *args: Any, parent=None) -> None:
        super().__init__(parent)
        self.fn = fn
        self.args = args
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _progress(self, done: int, total: int, msg: str) -> None:
        self.progress.emit(int(done), int(total), str(msg))

    def run(self) -> None:
        try:
            result = self.fn(self._progress, self.cancel_event, *self.args)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc), traceback.format_exc())
        else:
            self.succeeded.emit(result)
