"""Programmstart."""

from __future__ import annotations

import atexit
import multiprocessing
import os
import signal
import sys
import threading
import traceback
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from blackline2 import APP_NAME
from blackline2.settings import Settings


class _ErrorBridge(QObject):
    """Zeigt Fehlermeldungen immer im Haupt-Thread an (macOS erlaubt Fenster nur dort)."""

    show = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.show.connect(self._show)  # Empfänger lebt im Haupt-Thread -> Aufrufe aus anderen Threads werden eingereiht
        self._open = False

    def _show(self, message: str, details: str) -> None:
        if self._open:  # keine Dialoglawine bei Folgefehlern
            return
        self._open = True
        try:
            from PySide6.QtWidgets import QMessageBox

            from blackline2 import paths

            box = QMessageBox(QMessageBox.Icon.Critical, APP_NAME,
                              f"Unerwarteter Fehler: {message}\n\nDetails stehen in "
                              f"{paths.config_dir() / 'fehler.log'}")
            box.setDetailedText(details)
            box.exec()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._open = False


_bridge: _ErrorBridge | None = None


def _log_error(text: str) -> None:
    from blackline2 import paths

    try:
        paths.config_dir().mkdir(parents=True, exist_ok=True)
        with open(paths.config_dir() / "fehler.log", "a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} ---\n{text}")
    except OSError:
        pass


def report_error(exc_type, exc, tb) -> None:
    """Fehler protokollieren und (aus jedem Thread) im Haupt-Thread anzeigen."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    _log_error(text)
    if _bridge is not None:
        _bridge.show.emit(str(exc), text)  # aus Nebenthreads automatisch eingereiht
    else:
        sys.__excepthook__(exc_type, exc, tb)


def _install_error_handler() -> None:
    """Unerwartete Fehler anzeigen und protokollieren (ohne Konsolenfenster sonst unsichtbar)."""
    sys.excepthook = report_error
    threading.excepthook = lambda a: report_error(a.exc_type, a.exc_value, a.exc_traceback)


def main() -> int:
    global _bridge
    multiprocessing.freeze_support()
    _install_error_handler()
    app = QApplication(sys.argv)
    _bridge = _ErrorBridge()
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

    if os.environ.get("BLACKLINE2_SELFTEST"):
        # Start-Prüfung für gepackte Programme: Fenster aufbauen, kurz laufen, sauber beenden
        win.auto_offer_setup = False
        QTimer.singleShot(2500, lambda: (print("SELFTEST OK", flush=True), app.quit()))
    win.show()
    QTimer.singleShot(0, win.start_ai)
    if len(sys.argv) > 1:
        from pathlib import Path

        QTimer.singleShot(100, lambda: win.load_files([Path(a) for a in sys.argv[1:]]))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
