"""Einrichtung aus der Oberfläche: Fortschritt, Abbruch, Datenordner."""

import sys
import threading
import time

import pytest

from blackline2 import paths, setup_ki


def test_run_setup_reports_progress(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(setup_ki, "setup_ocr", lambda: setup_ki._say("ocr ok"))
    monkeypatch.setattr(setup_ki, "setup_llama", lambda gpu: setup_ki._say(f"llama {gpu}"))
    monkeypatch.setattr(setup_ki, "setup_model", lambda m: (setup_ki._say(f"modell {m}", 5, 10), tmp_path / "m.gguf")[1])
    result = setup_ki.run_setup("schnell", "vulkan", True, True, lambda m, d, t: calls.append((m, d, t)), None)
    assert result == tmp_path / "m.gguf"
    assert ("ocr ok", 0, 0) in calls and ("llama vulkan", 0, 0) in calls and ("modell schnell", 5, 10) in calls
    assert setup_ki.REPORTER is None and setup_ki.CANCEL is None


def test_download_can_be_cancelled(monkeypatch, tmp_path):
    cancel = threading.Event()
    cancel.set()

    class FakeResp:
        status = 200
        headers = {"Content-Length": "100"}

        def read(self, n):
            return b"x" * 10

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(setup_ki, "_open", lambda *a, **k: FakeResp())
    monkeypatch.setattr(setup_ki, "CANCEL", cancel)
    with pytest.raises(setup_ki.SetupCancelled):
        setup_ki.download("http://x/y", tmp_path / "y", "y")


def test_data_root_frozen_uses_user_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    assert paths.ki_dir() == paths.config_dir() / "ki"
    assert paths.tessdata_dir() == paths.config_dir() / "ocr" / "tessdata"
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    assert paths.ki_dir() == paths.app_root() / "ki"


def test_find_model_searches_all_roots(monkeypatch, tmp_path):
    (tmp_path / "modelle").mkdir()
    (tmp_path / "modelle" / "klein.gguf").write_bytes(b"1")
    (tmp_path / "modelle" / "gross.gguf").write_bytes(b"12345")
    monkeypatch.setattr(paths, "_ki_roots", lambda: [tmp_path / "leer", tmp_path])
    assert paths.find_model().name == "gross.gguf"
    assert paths.find_llama_server() is None


def test_setup_dialog_runs_and_reports(monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    try:
        app = QApplication.instance() or QApplication(sys.argv)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Qt nicht startbar: {exc}")
    from blackline2.gui.setup_dialog import SetupDialog
    from blackline2.settings import Settings

    def fake_run(modell, gpu, ocr, ki, reporter, cancel):
        reporter("lade", 0, 0)
        for i in range(1, 4):
            reporter(f"modell {i}/3", i, 3)
            time.sleep(0.05)
        return "/pfad/modell.gguf"

    monkeypatch.setattr(setup_ki, "run_setup", fake_run)
    s = Settings()
    dlg = SetupDialog(s)
    done = []
    dlg.finished_ok.connect(lambda: done.append(True))
    dlg.show()
    dlg.start()
    end = time.time() + 20
    while time.time() < end and dlg.worker is not None:
        app.processEvents()
        time.sleep(0.02)
    assert done == [True]
    assert s.model_path == "/pfad/modell.gguf"
    assert "Fertig" in dlg.status.text()
    assert "lade" in dlg.log.toPlainText()
    dlg.close()
