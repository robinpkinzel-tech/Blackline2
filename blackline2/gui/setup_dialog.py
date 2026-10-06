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
        self.model = QComboBox()
        for key, info in setup_ki.MODELS.items():
            self.model.addItem(f"{key} – {info['info']}", key)
        self.model.setCurrentIndex(1)
        form.addRow("KI-Modell:", self.model)
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
        self.ki = QCheckBox("KI-Programm und Modell laden")
        self.ki.setChecked(True)
        form.addRow("", self.ki)
        lay.addLayout(form)

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
    def start(self) -> None:
        modell = self.model.currentData()
        gpu = self.gpu.currentData()
        ocr, ki = self.ocr.isChecked(), self.ki.isChecked()
        if not (ocr or ki):
            self.status.setText("Nichts ausgewählt.")
            return

        def job(progress, cancel):
            def reporter(msg: str, done: int, total: int) -> None:
                progress(done, total, msg)
            return setup_ki.run_setup(modell, gpu, ocr, ki, reporter, cancel)

        self.worker = Worker(job, parent=self)
        self.worker.progress.connect(self._progress)
        self.worker.succeeded.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(self._cancelled)
        self.worker.finished.connect(self._finished)
        for w in (self.b_start, self.model, self.gpu, self.ocr, self.ki, self.b_close):
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
        self.b_cancel.setEnabled(False)
        self.worker = None

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.cancel()
            self.worker.wait(5000)
        super().closeEvent(event)
