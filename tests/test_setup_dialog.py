"""Einrichtung aus der Oberfläche: Fortschritt, Abbruch, Datenordner."""

import sys
import threading
import time
from pathlib import Path

import pytest

from blackline2 import paths, setup_ki


def test_run_setup_reports_progress(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(setup_ki, "setup_ocr", lambda: setup_ki._say("ocr ok"))
    monkeypatch.setattr(setup_ki, "setup_llama", lambda gpu, update=True: setup_ki._say(f"llama {gpu}"))
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

    def fake_run(modell, gpu, ocr, ki, reporter, cancel, update_llama=True):
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
    assert Path(s.model_path) == Path("/pfad/modell.gguf")  # Windows: Backslashes
    assert "Fertig" in dlg.status.text()
    assert "lade" in dlg.log.toPlainText()
    dlg.close()


def _fake_model(path: Path, size: int = paths.MIN_MODEL_SIZE + 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(size)  # Datei ohne echten Plattenplatz
    return path


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Eigene Ordner und fremde KI-Ablagen in tmp_path; Einstellungen nicht im echten Benutzerordner."""
    own, ext = tmp_path / "ki", tmp_path / "lmstudio"
    monkeypatch.setattr(paths, "_ki_roots", lambda: [own])
    monkeypatch.setattr(paths, "ki_dir", lambda: own)
    monkeypatch.setattr(paths, "external_model_dirs", lambda: [ext])
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(paths, "settings_file", lambda: tmp_path / "config" / "einstellungen.json")
    return own, ext


def test_model_kinds_match_download_names():
    assert paths.model_kind(Path("gemma-3-12b-it-Q4_K_M.gguf")) == "gruendlich"
    assert paths.model_kind(Path("Qwen3-8B-Q4_K_M.gguf")) == "ausgewogen"
    assert paths.model_kind(Path("Qwen3-4B-Instruct-2507-Q4_K_M.gguf")) == "schnell"
    assert paths.model_kind(Path("gemma-3-12b-it-Q4_K_M.gguf.part")) == "gruendlich"
    assert paths.model_kind(Path("Qwen3-8B-Q8_0.gguf")) is None


def test_setup_reuses_existing_model_without_download(monkeypatch, isolated):
    """Neue Programmversion: das schon geladene Modell wird gefunden und nicht erneut geladen."""
    own, _ext = isolated
    model = _fake_model(own / "modelle" / "gemma-3-12b-it-Q4_K_M.gguf")

    def no_network(*a, **k):
        raise AssertionError("Es darf nichts heruntergeladen werden")

    monkeypatch.setattr(setup_ki, "get_json", no_network)
    monkeypatch.setattr(setup_ki, "download", no_network)
    assert setup_ki.setup_model("gruendlich") == model
    from blackline2.settings import Settings
    assert Path(Settings.load().model_path) == model
    assert paths.find_model() == model


def test_model_from_other_program_is_used(monkeypatch, isolated):
    _own, ext = isolated
    model = _fake_model(ext / "lmstudio-community" / "gemma-3-12b-it-GGUF" / "gemma-3-12b-it-Q4_K_M.gguf")
    _fake_model(ext / "x" / "Qwen3-8B-Q4_K_M.gguf", 1000)  # unvollständig -> ignorieren
    assert paths.find_known_model("gruendlich") == model
    assert paths.find_known_model("ausgewogen") is None
    assert paths.find_model() == model
    monkeypatch.setattr(setup_ki, "get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    assert setup_ki.setup_model("gruendlich") == model


def test_setup_llama_keeps_existing_program(monkeypatch, isolated):
    own, _ext = isolated
    exe = own / "llama.cpp" / paths.server_exe_name()
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    monkeypatch.setattr(setup_ki, "find_llama_asset", lambda gpu: (_ for _ in ()).throw(AssertionError()))
    assert setup_ki.setup_llama("cpu", update=False) == exe


def test_setup_dialog_preselects_present_model(isolated):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    try:
        QApplication.instance() or QApplication(sys.argv)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Qt nicht startbar: {exc}")
    from blackline2.gui.setup_dialog import SetupDialog
    from blackline2.settings import Settings

    own, _ext = isolated
    _fake_model(own / "modelle" / "gemma-3-12b-it-Q4_K_M.gguf")
    dlg = SetupDialog(Settings())
    assert dlg.model.currentData() == "gruendlich"
    assert "vorhanden" in dlg.model.currentText() and "weiterverwendet" in dlg.model_info.text()
    assert dlg.update_llama.isChecked()  # KI-Programm fehlt noch -> wird geladen
    dlg.model.setCurrentIndex(dlg.model.findData("schnell"))
    assert "bleibt erhalten" in dlg.model_info.text()
    dlg.close()

    # angefangener Download wird erkannt und vorausgewählt
    (own / "modelle" / "gemma-3-12b-it-Q4_K_M.gguf").unlink()
    _fake_model(own / "modelle" / "Qwen3-8B-Q4_K_M.gguf.part", 2_000_000)
    dlg = SetupDialog(Settings())
    assert dlg.model.currentData() == "ausgewogen" and "fortgesetzt" in dlg.model_info.text()
    dlg.close()
