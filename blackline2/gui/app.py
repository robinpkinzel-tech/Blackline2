"""Programmstart."""

from __future__ import annotations

import atexit
import multiprocessing
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from blackline2 import APP_NAME
from blackline2.settings import Settings


def main() -> int:
    multiprocessing.freeze_support()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Blackline")
    app.setStyle("Fusion")

    from blackline2.gui.main_window import MainWindow

    settings = Settings.load()
    win = MainWindow(settings)

    # KI in jedem Fall beim Programmende stoppen (auch bei Strg+C im Terminal)
    atexit.register(win.ai.stop)
    app.aboutToQuit.connect(win.ai.stop)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    timer = QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)  # Python-Signale verarbeiten lassen

    win.show()
    QTimer.singleShot(0, win.start_ai)
    if len(sys.argv) > 1:
        from pathlib import Path

        QTimer.singleShot(100, lambda: win.load_files([Path(a) for a in sys.argv[1:]]))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
