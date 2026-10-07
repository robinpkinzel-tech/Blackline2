"""Einrichtung der lokalen KI aus der Oberfläche heraus (Download mit Fortschrittsanzeige)."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit,
                               QProgressBar, QPushButton, QVBoxLayout)

from blackline2 import paths, setup_ki
from blackline2.gui.workers import Worker
from blackline2.settings import Settings


class SetupDialog(QDialog):
    finished_ok = Signal()

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("KI einrichten")
        self.setMinimumWidth(620)
        self.worker: Worker | None = None
        lay = QVBoxLayout(self)

        intro = QLabel(
            "Blackline 2 lädt das KI-Programm (llama.cpp), ein Sprachmodell und die deutschen "
            "Texterkennungsdaten herunter. Danach läuft alles ohne Internet, ausschließlich auf diesem Rechner.<br>"
            f"Ablageordner: <code>{paths.data_root()}</code><br>"
            "Ein abgebrochener Download wird beim nächsten Versuch fortgesetzt.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        form = QFormLayout()
        # schon vorhandene Modelle (auch aus älteren Versionen oder anderen KI-Programmen) weiterverwenden
        self.present = paths.known_models()
        self.partial = {k: p for p in paths.partial_downloads() if (k := paths.model_kind(p))}
        self.model = QComboBox()
        for key in setup_ki.MODELS:
            self.model.addItem(self._item_text(key), key)
        self.model.setCurrentIndex(self.model.findData(self._preselect()))
        self.model.currentIndexChanged.connect(self._update_model_info)
        form.addRow("KI-Modell:", self.model)
        self.model_info = QLabel()
        self.model_info.setWordWrap(True)
        form.addRow("", self.model_info)
        self.gpu = QComboBox()
        if sys.platform == "darwin":
            self.gpu.addItem("Standard (nutzt die Grafikeinheit des Mac automatisch)", "cpu")
        elif sys.platform == "win32":
            self.gpu.addItem("Nur Prozessor (funktioniert überall)", "cpu")
            self.gpu.addItem("Grafikkarte – Vulkan (AMD, Intel, NVIDIA)", "vulkan")
            self.gpu.addItem("Grafikkarte – CUDA (NVIDIA)", "cuda")
        else:
            self.gpu.addItem("Nur Prozessor", "cpu")
            self.gpu.addItem("Grafikkarte – Vulkan", "vulkan")
        form.addRow("Rechenwerk:", self.gpu)
        self.ocr = QCheckBox("Texterkennungsdaten (deutsch/englisch) laden")
        self.ocr.setChecked(paths.find_tessdata() is None)
        form.addRow("", self.ocr)
        self.ki = QCheckBox("KI-Programm und Modell einrichten")
        self.ki.setChecked(True)
        form.addRow("", self.ki)
        self.server_present = paths.find_llama_server() is not None
        self.update_llama = QCheckBox("KI-Programm (llama.cpp) neu herunterladen / aktualisieren")
        self.update_llama.setChecked(not self.server_present)
        self.update_llama.setEnabled(self.server_present)
        self.update_llama.setToolTip("Nur nötig, wenn die KI nicht startet oder eine neuere Version gewünscht ist.")
        form.addRow("", self.update_llama)
        lay.addLayout(form)
        self._update_model_info()

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        lay.addWidget(self.progress)
        self.status = QLabel("Bereit.")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(160)
        lay.addWidget(self.log)

        row = QHBoxLayout()
        self.b_start = QPushButton("▶ Einrichtung starten")
        self.b_start.clicked.connect(self.start)
        self.b_cancel = QPushButton("Abbrechen")
        self.b_cancel.clicked.connect(self.cancel)
        self.b_cancel.setEnabled(False)
        self.b_close = QPushButton("Schließen")
        self.b_close.clicked.connect(self.reject)
        row.addWidget(self.b_start)
        row.addWidget(self.b_cancel)
        row.addStretch(1)
        row.addWidget(self.b_close)
        lay.addLayout(row)

    # ------------------------------------------------------------
    def _preselect(self) -> str:
        """Modell, das schon da ist (bzw. angefangen wurde), vorauswählen."""
        current = Path(self.settings.model_path) if self.settings.model_path else None
        if current and current.is_file() and paths.model_kind(current):
            return paths.model_kind(current)
        if self.present:
            return next(iter(self.present))
        if self.partial:
            return next(iter(self.partial))
        return "ausgewogen"

    def _item_text(self, key: str) -> str:
        mark = "  ✓ vorhanden" if key in self.present else "  ⏸ angefangen" if key in self.partial else ""
        return f"{key} – {setup_ki.MODELS[key]['info']}{mark}"

    def _update_model_info(self) -> None:
        key = self.model.currentData()
        if key in self.present:
            text = (f"✓ Bereits vorhanden – wird weiterverwendet, kein erneuter Download:<br>"
                    f"<code>{self.present[key]}</code>")
        elif key in self.partial:
            gb = self.partial[key].stat().st_size / 1e9
            text = f"⏸ Download angefangen ({gb:.1f} GB geladen) – wird fortgesetzt."
        else:
            text = "Wird heruntergeladen."
            others = [p.name for k, p in self.present.items() if k != key]
            if others:
                text += (f" Hinweis: {', '.join(others)} ist schon vorhanden und bleibt erhalten "
                         f"(zum Platzsparen ggf. in <code>{paths.ki_models_dir()}</code> löschen).")
        self.model_info.setText(text)

    def start(self) -> None:
        modell = self.model.currentData()
        gpu = self.gpu.currentData()
        ocr, ki = self.ocr.isChecked(), self.ki.isChecked()
        update_llama = self.update_llama.isChecked() or not self.server_present
        if not (ocr or ki):
            self.status.setText("Nichts ausgewählt.")
            return

        def job(progress, cancel):
            def reporter(msg: str, done: int, total: int) -> None:
                progress(done, total, msg)
            return setup_ki.run_setup(modell, gpu, ocr, ki, reporter, cancel, update_llama=update_llama)

        self.worker = Worker(job, parent=self)
        self.worker.progress.connect(self._progress)
        self.worker.succeeded.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(self._cancelled)
        self.worker.finished.connect(self._finished)
        for w in (self.b_start, self.model, self.gpu, self.ocr, self.ki, self.update_llama, self.b_close):
            w.setEnabled(False)
        self.b_cancel.setEnabled(True)
        self.progress.setRange(0, 0)
        self.log.clear()
        self.status.setText("Einrichtung läuft …")
        self.worker.start()

    def cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.status.setText("Wird abgebrochen …")

    def _progress(self, done: int, total: int, msg: str) -> None:
        if total > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(1000 * done / total))
        else:
            self.progress.setRange(0, 0)
            self.log.appendPlainText(msg.strip())
        self.status.setText(msg)

    def _done(self, model_path) -> None:
        if model_path:
            self.settings.model_path = str(Path(model_path))
            self.settings.save()
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.status.setText("Fertig. Die KI wird jetzt gestartet.")
        self.finished_ok.emit()

    def _failed(self, msg: str, details: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status.setText(f"Fehler: {msg}")
        self.log.appendPlainText(details)

    def _cancelled(self) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status.setText("Abgebrochen. Ein erneuter Start setzt den Download fort.")

    def _finished(self) -> None:
        for w in (self.b_start, self.model, self.gpu, self.ocr, self.ki, self.b_close):
            w.setEnabled(True)
        self.server_present = paths.find_llama_server() is not None
        self.update_llama.setEnabled(self.server_present)
        self.present = paths.known_models()
        self.partial = {k: p for p in paths.partial_downloads() if (k := paths.model_kind(p))}
        for i in range(self.model.count()):
            self.model.setItemText(i, self._item_text(self.model.itemData(i)))
        self._update_model_info()
        self.b_cancel.setEnabled(False)
        self.worker = None

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.cancel()
            self.worker.wait(5000)
        super().closeEvent(event)
