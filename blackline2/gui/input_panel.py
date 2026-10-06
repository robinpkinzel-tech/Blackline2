"""Eingabefelder: Mandant, Gegner, freie Suchbegriffe und Kategorien.

Die Eingaben werden bewusst NICHT gespeichert (Mandantengeheimnis).
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QFormLayout, QGridLayout, QGroupBox, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from blackline2.detect_inputs import UserInputs
from blackline2.settings import CATEGORIES, Settings

CUSTOM_ROWS = 5


class InputPanel(QScrollArea):
    analyze_requested = Signal()

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)

        hint = QLabel("Mehrere Angaben je Feld mit <b>;</b> trennen "
                      "(z. B. Namensvarianten oder Geburtsname).")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        box_m = QGroupBox("Mandant  →  „Mandant“ / „Adresse Mandant“")
        fm = QFormLayout(box_m)
        self.mandant_name = QLineEdit(placeholderText="z. B. Robin Kinzel")
        self.mandant_adresse = QLineEdit(placeholderText="z. B. Musterweg 15, 12345 Musterstadt")
        fm.addRow("Name:", self.mandant_name)
        fm.addRow("Adresse:", self.mandant_adresse)
        lay.addWidget(box_m)

        box_g = QGroupBox("Gegner  →  „Gegner“ / „Adresse Gegner“")
        fg = QFormLayout(box_g)
        self.gegner_name = QLineEdit(placeholderText="Name der Gegenseite")
        self.gegner_adresse = QLineEdit(placeholderText="Straße Nr., PLZ Ort")
        fg.addRow("Name:", self.gegner_name)
        fg.addRow("Adresse:", self.gegner_adresse)
        lay.addWidget(box_g)

        box_c = QGroupBox("Weitere Begriffe (Suchbegriff → wird ersetzt durch)")
        gc = QGridLayout(box_c)
        self.custom: list[tuple[QLineEdit, QLineEdit]] = []
        for i in range(CUSTOM_ROWS):
            term = QLineEdit(placeholderText=f"Suchbegriff {i + 1}")
            label = QLineEdit(placeholderText="Kürzel, z. B. Arbeitgeber")
            gc.addWidget(term, i, 0)
            gc.addWidget(QLabel("→"), i, 1)
            gc.addWidget(label, i, 2)
            self.custom.append((term, label))
        lay.addWidget(box_c)

        box_k = QGroupBox("Was soll geschwärzt werden?")
        vk = QVBoxLayout(box_k)
        self.cat_boxes: dict[str, QCheckBox] = {}
        for key, title in CATEGORIES.items():
            cb = QCheckBox(title)
            cb.setChecked(settings.category_on(key))
            cb.toggled.connect(self._categories_changed)
            vk.addWidget(cb)
            self.cat_boxes[key] = cb
        lay.addWidget(box_k)

        self.analyze_btn = QPushButton("▶  Analysieren")
        self.analyze_btn.setMinimumHeight(40)
        f = self.analyze_btn.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 2)
        self.analyze_btn.setFont(f)
        self.analyze_btn.clicked.connect(self.analyze_requested)
        lay.addWidget(self.analyze_btn)

        privacy = QLabel("🔒 Alles bleibt auf diesem Rechner. Die Angaben werden nicht gespeichert.")
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color: #2e7d32;")
        lay.addWidget(privacy)
        lay.addStretch(1)
        self.setWidget(inner)

    def _categories_changed(self) -> None:
        for key, cb in self.cat_boxes.items():
            self.settings.categories[key] = cb.isChecked()
        self.settings.save()

    def inputs(self) -> UserInputs:
        return UserInputs(
            mandant_name=self.mandant_name.text(),
            mandant_adresse=self.mandant_adresse.text(),
            gegner_name=self.gegner_name.text(),
            gegner_adresse=self.gegner_adresse.text(),
            custom=[(t.text(), l.text()) for t, l in self.custom if t.text().strip()],
        )

    def clear(self) -> None:
        for w in (self.mandant_name, self.mandant_adresse, self.gegner_name, self.gegner_adresse):
            w.clear()
        for t, l in self.custom:
            t.clear()
            l.clear()
