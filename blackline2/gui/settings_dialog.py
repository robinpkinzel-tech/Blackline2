"""Einstellungsdialog."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

from blackline2 import paths
from blackline2.settings import Settings


def _path_row(edit: QLineEdit, directory: bool = False, filt: str = "") -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(edit, 1)
    b = QPushButton("…")
    b.setFixedWidth(32)

    def pick() -> None:
        if directory:
            p = QFileDialog.getExistingDirectory(w, "Ordner wählen", edit.text())
        else:
            p, _ = QFileDialog.getOpenFileName(w, "Datei wählen", edit.text(), filt)
        if p:
            edit.setText(p)

    b.clicked.connect(pick)
    h.addWidget(b)
    return w


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Einstellungen")
        self.setMinimumWidth(560)
        self.s = settings
        tabs = QTabWidget()

        # --- KI
        ki = QWidget()
        f = QFormLayout(ki)
        self.ki_mode = QComboBox()
        self.ki_mode.addItem("Lokal (empfohlen) – startet mit Blackline 2, endet beim Schließen", "lokal")
        self.ki_mode.addItem("Externer lokaler Dienst (z. B. Ollama) – eigener Lebenszyklus", "extern")
        self.ki_mode.addItem("Aus – nur Eingaben und Regeln", "aus")
        self.ki_mode.setCurrentIndex(max(0, self.ki_mode.findData(settings.ki_mode)))
        f.addRow("KI-Modus:", self.ki_mode)
        self.ki_autostart = QCheckBox("KI beim Programmstart sofort laden")
        self.ki_autostart.setChecked(settings.ki_autostart)
        f.addRow("", self.ki_autostart)
        self.server_path = QLineEdit(settings.llama_server_path)
        self.server_path.setPlaceholderText(str(paths.find_llama_server() or "automatisch (Ordner ki/llama.cpp)"))
        f.addRow("llama-server:", _path_row(self.server_path))
        self.model_path = QLineEdit(settings.model_path)
        self.model_path.setPlaceholderText(str(paths.find_model() or "automatisch (Ordner ki/modelle)"))
        f.addRow("KI-Modell (.gguf):", _path_row(self.model_path, filt="KI-Modell (*.gguf)"))
        self.ki_context = QSpinBox(minimum=2048, maximum=131072, singleStep=1024, value=settings.ki_context)
        f.addRow("Kontextlänge:", self.ki_context)
        self.ki_threads = QSpinBox(minimum=0, maximum=128, value=settings.ki_threads)
        self.ki_threads.setSpecialValueText("automatisch")
        f.addRow("CPU-Threads:", self.ki_threads)
        self.ki_gpu = QSpinBox(minimum=0, maximum=999, value=settings.ki_gpu_layers)
        f.addRow("GPU-Schichten (0 = nur CPU):", self.ki_gpu)
        self.ki_extra = QLineEdit(settings.ki_extra_args)
        f.addRow("Zusatzargumente:", self.ki_extra)
        self.ext_url = QLineEdit(settings.ki_extern_url)
        f.addRow("Externe URL:", self.ext_url)
        self.ext_model = QLineEdit(settings.ki_extern_model)
        f.addRow("Externes Modell:", self.ext_model)
        self.excl_prof = QCheckBox("Namen von Richtern, Anwälten und Behördenmitarbeitern NICHT schwärzen")
        self.excl_prof.setChecked(settings.ki_exclude_professionals)
        f.addRow("", self.excl_prof)
        tabs.addTab(ki, "KI")

        # --- OCR
        oc = QWidget()
        f = QFormLayout(oc)
        self.ocr_mode = QComboBox()
        self.ocr_mode.addItem("Automatisch (Scans erkennen, digitale PDFs direkt lesen)", "auto")
        self.ocr_mode.addItem("Immer Texterkennung", "immer")
        self.ocr_mode.addItem("Nie Texterkennung", "nie")
        self.ocr_mode.setCurrentIndex(max(0, self.ocr_mode.findData(settings.ocr_mode)))
        f.addRow("Texterkennung:", self.ocr_mode)
        self.ocr_dpi = QSpinBox(minimum=150, maximum=600, singleStep=50, value=settings.ocr_dpi)
        f.addRow("Auflösung (dpi):", self.ocr_dpi)
        self.ocr_lang = QLineEdit(settings.ocr_languages)
        f.addRow("Sprachen:", self.ocr_lang)
        self.ocr_deskew = QCheckBox("Schief eingescannte Seiten begradigen")
        self.ocr_deskew.setChecked(settings.ocr_deskew)
        f.addRow("", self.ocr_deskew)
        self.ocr_orient = QCheckBox("Gedrehte Seiten (90°/180°) erkennen und aufrichten")
        self.ocr_orient.setChecked(settings.ocr_orientation)
        f.addRow("", self.ocr_orient)
        self.tessdata = QLineEdit(settings.tessdata_path)
        self.tessdata.setPlaceholderText(str(paths.find_tessdata() or "automatisch (Ordner ocr/tessdata)"))
        f.addRow("tessdata-Ordner:", _path_row(self.tessdata, directory=True))
        self.fuzzy = QCheckBox("Tolerante Suche (OCR-Fehler, Silbentrennung)")
        self.fuzzy.setChecked(settings.fuzzy_matching)
        f.addRow("", self.fuzzy)
        tabs.addTab(oc, "Texterkennung")

        # --- Export
        ex = QWidget()
        f = QFormLayout(ex)
        self.exp_mode = QComboBox()
        self.exp_mode.addItem("Bild-PDF – maximal sicher (empfohlen)", "bild")
        self.exp_mode.addItem("Text-PDF – Text bleibt erhalten, Schwärzung per Redaction", "text")
        self.exp_mode.setCurrentIndex(max(0, self.exp_mode.findData(settings.export_mode)))
        f.addRow("Ausgabe:", self.exp_mode)
        self.exp_dpi = QSpinBox(minimum=150, maximum=600, singleStep=50, value=settings.export_dpi)
        f.addRow("Auflösung Bild-PDF:", self.exp_dpi)
        self.exp_search = QCheckBox("Bild-PDF durchsuchbar machen (Texterkennung auf dem geschwärzten Bild)")
        self.exp_search.setChecked(settings.export_searchable)
        f.addRow("", self.exp_search)
        self.exp_suffix = QLineEdit(settings.export_suffix)
        f.addRow("Dateiname-Zusatz:", self.exp_suffix)
        tabs.addTab(ex, "Speichern")

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        note = QLabel(f"Einstellungsdatei: {paths.settings_file()}")
        note.setStyleSheet("color: gray; font-size: 11px;")
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def ki_signature(self) -> tuple:
        s = self.s
        return (s.ki_mode, s.llama_server_path, s.model_path, s.ki_context, s.ki_threads, s.ki_gpu_layers,
                s.ki_extra_args, s.ki_extern_url, s.ki_extern_model)

    def apply(self) -> bool:
        """Übernimmt die Werte. Rückgabe: True, wenn die KI neu gestartet werden muss."""
        before = self.ki_signature()
        s = self.s
        s.ki_mode = self.ki_mode.currentData()
        s.ki_autostart = self.ki_autostart.isChecked()
        s.llama_server_path = self.server_path.text().strip()
        s.model_path = self.model_path.text().strip()
        s.ki_context = self.ki_context.value()
        s.ki_threads = self.ki_threads.value()
        s.ki_gpu_layers = self.ki_gpu.value()
        s.ki_extra_args = self.ki_extra.text().strip()
        s.ki_extern_url = self.ext_url.text().strip()
        s.ki_extern_model = self.ext_model.text().strip()
        s.ki_exclude_professionals = self.excl_prof.isChecked()
        s.ocr_mode = self.ocr_mode.currentData()
        s.ocr_dpi = self.ocr_dpi.value()
        s.ocr_languages = self.ocr_lang.text().strip() or "deu"
        s.ocr_deskew = self.ocr_deskew.isChecked()
        s.ocr_orientation = self.ocr_orient.isChecked()
        s.tessdata_path = self.tessdata.text().strip()
        s.fuzzy_matching = self.fuzzy.isChecked()
        s.export_mode = self.exp_mode.currentData()
        s.export_dpi = self.exp_dpi.value()
        s.export_searchable = self.exp_search.isChecked()
        s.export_suffix = self.exp_suffix.text() or "_geschwärzt"
        s.save()
        return before != self.ki_signature()
