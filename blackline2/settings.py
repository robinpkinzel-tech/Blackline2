"""Programmeinstellungen.

Gespeichert werden NUR technische Einstellungen. Mandanten-, Gegner- und
Suchbegriff-Angaben werden bewusst nie auf die Festplatte geschrieben.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields

from blackline2 import paths

# Kategorien, die erkannt und geschwärzt werden können (Schlüssel -> Anzeige)
CATEGORIES: dict[str, str] = {
    "name": "Namen",
    "adresse": "Adressen",
    "email": "E-Mail-Adressen",
    "telefon": "Telefon-/Handy-/Faxnummern",
    "iban": "IBAN / Kontonummern",
    "versicherung": "Versicherten-/Rentennummern",
    "geburtsdatum": "Geburtsdaten",
    "sonstiges": "Sonstige persönliche Daten (KI)",
}


@dataclass
class Settings:
    # --- Texterkennung ---
    ocr_mode: str = "auto"          # auto | immer | nie
    ocr_dpi: int = 300
    ocr_languages: str = "deu+eng"
    ocr_deskew: bool = True
    ocr_orientation: bool = True
    tessdata_path: str = ""         # leer = automatisch suchen

    # --- KI ---
    ki_mode: str = "lokal"          # lokal | extern | aus
    ki_autostart: bool = True
    llama_server_path: str = ""     # leer = automatisch im Ordner ki/llama.cpp
    model_path: str = ""            # leer = größtes .gguf in ki/modelle
    ki_context: int = 8192
    ki_threads: int = 0             # 0 = automatisch
    ki_gpu_layers: int = 99         # ohne GPU-Build wirkungslos
    ki_extra_args: str = "--jinja"
    ki_start_timeout: int = 300
    ki_request_timeout: int = 900
    ki_extern_url: str = "http://127.0.0.1:11434/v1"
    ki_extern_model: str = "qwen3:8b"
    ki_exclude_professionals: bool = False

    # --- Erkennung ---
    categories: dict[str, bool] = field(default_factory=lambda: {k: True for k in CATEGORIES})
    fuzzy_matching: bool = True

    # --- Export ---
    export_mode: str = "bild"       # bild (maximal sicher) | text
    export_dpi: int = 300
    export_searchable: bool = False  # OCR-Textebene über das geschwärzte Bild legen
    export_suffix: str = "_geschwärzt"

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            data = json.loads(paths.settings_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return s
        known = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key in known:
                setattr(s, key, value)
        # neue Kategorien ergänzen
        for k in CATEGORIES:
            s.categories.setdefault(k, True)
        return s

    def save(self) -> None:
        try:
            paths.config_dir().mkdir(parents=True, exist_ok=True)
            paths.settings_file().write_text(
                json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def category_on(self, key: str) -> bool:
        return bool(self.categories.get(key, True))
