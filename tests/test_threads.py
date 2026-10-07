"""Fehler aus Hintergrund-Threads: kein Fenster außerhalb des Haupt-Threads (sonst Absturz unter macOS)."""

import sys
import threading
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")


def _app():
    from PySide6.QtWidgets import QApplication

    try:
        return QApplication.instance() or QApplication(sys.argv)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Qt nicht startbar: {exc}")


def _pump(app, cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end and not cond():
        app.processEvents()
        time.sleep(0.02)
    return cond()


def test_progress_accepts_values_above_2gb():
    """Download-Fortschritt in Bytes (> 2^31) darf das Signal nicht zum Überlaufen bringen."""
    app = _app()
    from blackline2.gui.workers import Worker

    got = []

    def job(progress, cancel):
        progress(7_000_000_000, 8_000_000_000, "Download")
        return "fertig"

    w = Worker(job)
    w.progress.connect(lambda d, t, m: got.append((d, t, m)))
    done = []
    w.succeeded.connect(lambda r: done.append(r))
    w.start()
    assert _pump(app, lambda: bool(done)), "Worker ist abgestürzt/hängt"
    _pump(app, lambda: bool(got), 2)
    assert got == [(7_000_000_000, 8_000_000_000, "Download")]
    assert done == ["fertig"]
    w.wait(2000)


def test_error_dialog_only_in_main_thread(monkeypatch):
    app = _app()
    from PySide6.QtCore import QThread

    from blackline2.gui import app as gui_app

    shown = []

    def fake_show(self, message, details):
        shown.append((QThread.currentThread() is app.thread(), message))

    monkeypatch.setattr(gui_app._ErrorBridge, "_show", fake_show)
    bridge = gui_app._ErrorBridge()
    monkeypatch.setattr(gui_app, "_bridge", bridge)
    monkeypatch.setattr(gui_app, "_log_error", lambda text: None)

    def boom():
        try:
            raise RuntimeError("Fehler im Hintergrund")
        except RuntimeError:
            gui_app.report_error(*sys.exc_info())

    t = threading.Thread(target=boom)
    t.start()
    t.join()
    assert _pump(app, lambda: bool(shown))
    assert shown == [(True, "Fehler im Hintergrund")], "Meldung wurde nicht im Haupt-Thread angezeigt"


def test_failed_job_reported_in_main_thread(monkeypatch, tmp_path):
    """Schlägt ein Hintergrundauftrag fehl, erscheint die Meldung im Haupt-Thread."""
    app = _app()
    from PySide6.QtCore import QThread

    from blackline2.gui.main_window import MainWindow
    from blackline2.settings import Settings

    s = Settings()
    s.ki_mode = "aus"
    w = MainWindow(s)
    w.auto_offer_setup = False
    seen = []
    monkeypatch.setattr(MainWindow, "_failed",
                        lambda self, label, msg, det: seen.append((QThread.currentThread() is app.thread(), msg)))

    def job(progress, cancel):
        raise RuntimeError("kaputt")

    w._run(job, on_done=lambda r: None, label="Test")
    assert _pump(app, lambda: bool(seen))
    assert seen == [(True, "kaputt")]
    w.close()
