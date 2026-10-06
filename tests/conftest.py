import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from blackline2 import paths  # noqa: E402
from blackline2.model import PageData, Word  # noqa: E402

CHAR_W = 6.0
LINE_H = 14.0


def make_page(text: str, index: int = 0) -> PageData:
    """Seite aus Text bauen: jede Zeile eine Textzeile, Wörter mit erfundenen Positionen."""
    words = []
    for li, line in enumerate(text.split("\n")):
        x = 50.0
        for tok in line.split():
            w = len(tok) * CHAR_W
            words.append(Word(tok, (x, 50 + li * LINE_H, x + w, 50 + li * LINE_H + 10), li))
            x += w + CHAR_W
    return PageData(index=index, width=595, height=842, words=words, source="text")


@pytest.fixture
def page_factory():
    return make_page


@pytest.fixture(scope="session")
def tessdata():
    found = paths.find_tessdata()
    if not found:
        pytest.skip("Keine deutschen OCR-Sprachdaten installiert")
    return str(found)


@pytest.fixture
def fake_server(tmp_path):
    """Ausführbare Attrappe von llama-server + leere Modelldatei."""
    fake = Path(__file__).with_name("fake_llama_server.py")
    if sys.platform == "win32":
        exe = tmp_path / "llama-server.bat"
        exe.write_text(f'@"{sys.executable}" "{fake}" %*\n')
    else:
        exe = tmp_path / "llama-server"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    model = tmp_path / "test.gguf"
    model.write_bytes(b"GGUF")
    return exe, model


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Einstellungen/PID-Datei nicht im echten Benutzerordner ablegen."""
    cfg = tmp_path / "config"
    monkeypatch.setattr(paths, "config_dir", lambda: cfg)
    monkeypatch.setattr(paths, "settings_file", lambda: cfg / "einstellungen.json")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
