"""Programmstart."""

from __future__ import annotations

import atexit
import multiprocessing
import signal
import sys
import traceback
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from blackline2 import APP_NAME
from blackline2.settings import Settings


def _install_error_handler() -> None:
    """Unerwartete Fehler anzeigen und protokollieren (ohne Konsolenfenster sonst unsichtbar)."""
    from blackline2 import paths

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            paths.config_dir().mkdir(parents=True, exist_ok=True)
            with open(paths.config_dir() / "fehler.log", "a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} ---\n{text}")
        except OSError:
            pass
        try:
            from PySide6.QtWidgets import QMessageBox

            box = QMessageBox(QMessageBox.Icon.Critical, APP_NAME,
                              f"Unerwarteter Fehler: {exc}\n\nDetails stehen in {paths.config_dir() / 'fehler.log'}")
            box.setDetailedText(text)
            box.exec()
        except Exception:  # noqa: BLE001
            sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


def main() -> int:
    multiprocessing.freeze_support()
    _install_error_handler()
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
