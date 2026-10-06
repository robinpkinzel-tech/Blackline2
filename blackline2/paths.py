"""Zentrale Pfade: Programmordner, KI-/OCR-Daten und Einstellungsdatei."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_root() -> Path:
    """Ordner, in dem Blackline 2 liegt (bei PyInstaller: neben der .exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def ki_dir() -> Path:
    return app_root() / "ki"


def ki_models_dir() -> Path:
    return ki_dir() / "modelle"


def ki_server_dir() -> Path:
    return ki_dir() / "llama.cpp"


def tessdata_dir() -> Path:
    return app_root() / "ocr" / "tessdata"


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "Blackline2"


def settings_file() -> Path:
    return config_dir() / "einstellungen.json"


def server_exe_name() -> str:
    return "llama-server.exe" if sys.platform == "win32" else "llama-server"


def find_llama_server() -> Path | None:
    """Sucht llama-server im KI-Ordner (auch in Unterordnern aus dem ZIP)."""
    base = ki_server_dir()
    if not base.exists():
        return None
    direct = base / server_exe_name()
    if direct.is_file():
        return direct
    for candidate in sorted(base.rglob(server_exe_name())):
        if candidate.is_file():
            return candidate
    return None


def find_model() -> Path | None:
    """Nimmt das erste .gguf-Modell im Modellordner (größte Datei zuerst)."""
    base = ki_models_dir()
    if not base.exists():
        return None
    models = [p for p in base.glob("*.gguf") if p.is_file() and not p.name.startswith("mmproj")]
    if not models:
        return None
    return max(models, key=lambda p: p.stat().st_size)


def find_tessdata() -> Path | None:
    """Tessdata-Ordner mit deu.traineddata finden (eigener Ordner bevorzugt)."""
    candidates = [tessdata_dir()]
    env = os.environ.get("TESSDATA_PREFIX")
    if env:
        candidates += [Path(env), Path(env) / "tessdata"]
    if sys.platform == "win32":
        for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                   os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            candidates.append(Path(pf) / "Tesseract-OCR" / "tessdata")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Programs" / "Tesseract-OCR" / "tessdata")
    else:
        candidates += [
            Path("/opt/homebrew/share/tessdata"),
            Path("/usr/local/share/tessdata"),
            Path("/usr/share/tesseract-ocr/5/tessdata"),
            Path("/usr/share/tesseract-ocr/4.00/tessdata"),
            Path("/usr/share/tessdata"),
        ]
    for c in candidates:
        if (c / "deu.traineddata").is_file():
            return c
    return None
