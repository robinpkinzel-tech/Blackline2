"""Zentrale Pfade: Programmordner, KI-/OCR-Daten und Einstellungsdatei."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Dateinamen der Modelle, die Blackline 2 einrichtet (auch von anderen Programmen geladen erkennbar)
KNOWN_MODEL_FILES: dict[str, str] = {
    "schnell": r"^qwen3-4b-instruct-2507.*q4_k_m\.gguf$",
    "ausgewogen": r"^qwen3-8b-q4_k_m\.gguf$",
    "gruendlich": r"^gemma-3-12b-it.*q4_k_m\.gguf$",
}
MIN_MODEL_SIZE = 1_000_000_000  # kleinere .gguf-Dateien sind unvollständig oder keine Sprachmodelle


def app_root() -> Path:
    """Ordner, in dem Blackline 2 liegt (bei PyInstaller: neben der .exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def data_root() -> Path:
    """Ablage für KI und OCR-Daten.

    Beim Start aus dem Quellcode neben dem Programm; als gepackte App (Mac .app,
    Windows .exe) im Benutzerordner, weil der App-Ordner bei Updates ersetzt wird.
    """
    return config_dir() if is_frozen() else app_root()


def ki_dir() -> Path:
    return data_root() / "ki"


def ki_models_dir() -> Path:
    return ki_dir() / "modelle"


def ki_server_dir() -> Path:
    return ki_dir() / "llama.cpp"


def tessdata_dir() -> Path:
    return data_root() / "ocr" / "tessdata"


def _ki_roots() -> list[Path]:
    roots = [ki_dir(), app_root() / "ki", config_dir() / "ki"]
    out: list[Path] = []
    for r in roots:
        if r not in out:
            out.append(r)
    return out


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
    """Sucht llama-server in den KI-Ordnern (auch in Unterordnern aus dem ZIP)."""
    for root in _ki_roots():
        base = root / "llama.cpp"
        if not base.exists():
            continue
        direct = base / server_exe_name()
        if direct.is_file():
            return direct
        for candidate in sorted(base.rglob(server_exe_name())):
            if candidate.is_file():
                return candidate
    return None


def own_models() -> list[Path]:
    """Modelle in den Blackline-Ordnern (ohne Bildmodelle und unvollständige Downloads)."""
    models: list[Path] = []
    for root in _ki_roots():
        base = root / "modelle"
        if base.exists():
            models += [p for p in base.glob("*.gguf")
                       if p.is_file() and not p.name.startswith("mmproj") and p not in models]
    return models


def external_model_dirs() -> list[Path]:
    """Übliche Ablagen anderer KI-Programme (LM Studio, llama.cpp, Hugging Face, Downloads)."""
    home = Path.home()
    dirs = [home / ".lmstudio" / "models", home / ".cache" / "lm-studio" / "models",
            home / ".cache" / "llama.cpp", home / ".cache" / "huggingface" / "hub"]
    if sys.platform == "darwin":
        dirs.insert(0, home / "Library" / "Caches" / "llama.cpp")
    elif sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        dirs.insert(0, Path(os.environ["LOCALAPPDATA"]) / "llama.cpp")
    hf = os.environ.get("HF_HOME")
    if hf:
        dirs.append(Path(hf) / "hub")
    dirs.append(home / "Downloads")
    return dirs


def model_kind(path: Path) -> str | None:
    """Welches der Blackline-Modelle ist diese Datei? ("schnell", "ausgewogen", "gruendlich")"""
    name = path.name.lower().removesuffix(".part")
    for kind, pattern in KNOWN_MODEL_FILES.items():
        if re.search(pattern, name):
            return kind
    return None


def _usable(p: Path) -> bool:
    try:
        return p.is_file() and p.stat().st_size >= MIN_MODEL_SIZE
    except OSError:
        return False


def _iter_known_models():
    """(Art, Pfad) aller vollständigen Blackline-Modelle – eigene Ordner zuerst."""
    for p in sorted(own_models(), key=lambda x: -x.stat().st_size):
        if model_kind(p) and _usable(p):
            yield model_kind(p), p
    for base in external_model_dirs():
        if not base.is_dir():
            continue
        try:
            found = base.glob("*.gguf") if base.name == "Downloads" else base.rglob("*.gguf")
            for p in found:
                k = model_kind(p)
                if k and _usable(p):
                    yield k, p
        except OSError:
            continue


def find_known_model(kind: str | None = None) -> Path | None:
    """Bereits vorhandenes Blackline-Modell suchen – erst eigene Ordner, dann andere KI-Programme.

    So wird ein schon geladenes Modell weiterverwendet statt ein zweites Mal heruntergeladen.
    """
    return next((p for k, p in _iter_known_models() if kind is None or k == kind), None)


def known_models() -> dict[str, Path]:
    """Je Modellart die erste gefundene Datei."""
    out: dict[str, Path] = {}
    for k, p in _iter_known_models():
        out.setdefault(k, p)
    return out


def find_model() -> Path | None:
    """Nimmt das größte .gguf-Modell aus den Modellordnern, sonst ein anderswo vorhandenes Blackline-Modell."""
    models = [p for p in own_models() if _usable(p)] or own_models()
    if models:
        return max(models, key=lambda p: p.stat().st_size)
    return find_known_model()


def partial_downloads() -> list[Path]:
    """Angefangene Modell-Downloads (werden bei der nächsten Einrichtung fortgesetzt)."""
    out: list[Path] = []
    for root in _ki_roots():
        base = root / "modelle"
        if base.exists():
            out += [p for p in base.glob("*.gguf.part") if p.is_file() and p not in out]
    return out


def find_tessdata() -> Path | None:
    """Tessdata-Ordner mit deu.traineddata finden (eigener Ordner bevorzugt)."""
    candidates = [tessdata_dir(), app_root() / "ocr" / "tessdata", config_dir() / "ocr" / "tessdata"]
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
