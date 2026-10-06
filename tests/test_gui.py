"""Rauchtest der Oberfläche (ohne Bildschirm): laden, analysieren, schließen -> KI beendet."""

import sys
import time

import psutil
import pytest

pytest.importorskip("PySide6.QtWidgets")


def _wait(app, cond, timeout=60):
    end = time.time() + timeout
    while time.time() < end and not cond():
        app.processEvents()
        time.sleep(0.05)
    return cond()


def test_gui_flow(fake_server, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    try:
        app = QApplication.instance() or QApplication(sys.argv)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Qt nicht startbar: {exc}")
    from blackline2.gui.main_window import MainWindow
    from blackline2.settings import Settings

    import pymupdf

    pdf = tmp_path / "brief.pdf"
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((60, 80), "Sehr geehrte Frau Erika Mustermann, Tel. 06433/123456", fontsize=11)
    p.insert_text((60, 100), "Ihr Mandant Robin Kinzel", fontsize=11)
    d.save(pdf)

    exe, model = fake_server
    s = Settings()
    s.llama_server_path, s.model_path = str(exe), str(model)
    w = MainWindow(s)
    w.show()
    w.start_ai()
    assert _wait(app, lambda: w.ai.state == "ready"), w.ai.message
    pid = w.ai.server.proc.pid

    w.load_files([pdf])
    assert _wait(app, lambda: w.worker is None and w.docs)
    w.inputs.mandant_name.setText("Robin Kinzel")
    w.analyze()
    assert _wait(app, lambda: w.worker is None and w.docs[0].analyzed)
    labels = {h.label for h in w.docs[0].hits}
    assert {"Mandant", "Person A", "Telefon"} <= labels

    # Klick auf Markierung schaltet um
    hit = w.docs[0].hits[0]
    w._toggle_hit(hit.id)
    assert hit.enabled is False

    w.close()
    _wait(app, lambda: False, 0.5)
    assert w.ai.server is None
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
