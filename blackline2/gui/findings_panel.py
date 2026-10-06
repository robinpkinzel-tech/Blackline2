"""Liste aller Funde des aktuellen Dokuments, gruppiert nach Kürzel."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMenu, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from blackline2.gui.page_view import color_for
from blackline2.model import PRIO_MANUAL, Hit

ROLE_HIT = Qt.ItemDataRole.UserRole + 1
ROLE_LABEL = Qt.ItemDataRole.UserRole + 2


def _swatch(color: QColor) -> QIcon:
    pm = QPixmap(12, 12)
    pm.fill(color)
    return QIcon(pm)


class FindingsPanel(QWidget):
    hit_selected = Signal(int)          # Treffer anzeigen
    hits_changed = Signal()             # an/aus oder Kürzel geändert
    label_renamed = Signal(str, str)    # alt, neu (für alle Dokumente)
    hit_removed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel("Noch keine Analyse.")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Kürzel / Fundstelle", "Seite", "Quelle"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.itemClicked.connect(self._double_clicked)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        b_all = QPushButton("Alle an")
        b_none = QPushButton("Alle aus")
        b_exp = QPushButton("Aufklappen")
        b_all.clicked.connect(lambda: self._set_all(True))
        b_none.clicked.connect(lambda: self._set_all(False))
        b_exp.clicked.connect(self._toggle_expand)
        for b in (b_all, b_none, b_exp):
            row.addWidget(b)
        lay.addLayout(row)
        tip = QLabel("Häkchen entfernen = Stelle wird NICHT geschwärzt. "
                     "Rechtsklick = Kürzel ändern. Klick auf eine Markierung in der Seite schaltet ebenfalls um.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray; font-size: 11px;")
        lay.addWidget(tip)
        self._hits: dict[int, Hit] = {}
        self._updating = False
        self._expanded = False

    # ------------------------------------------------------------ Aufbau
    def set_hits(self, hits: list[Hit], analyzed: bool) -> None:
        self._updating = True
        expanded = {self.tree.topLevelItem(i).data(0, ROLE_LABEL)
                    for i in range(self.tree.topLevelItemCount())
                    if self.tree.topLevelItem(i).isExpanded()}
        self.tree.clear()
        self._hits = {h.id: h for h in hits}
        groups: dict[str, list[Hit]] = {}
        for h in sorted(hits, key=lambda h: (h.page, h.rects[0][1] if h.rects else 0)):
            groups.setdefault(h.label, []).append(h)
        order = sorted(groups, key=lambda l: (0 if l.startswith("Mandant") else 1 if l.startswith("Gegner")
                                              else 2 if "Person" in l else 3, l))
        for label in order:
            items = groups[label]
            top = QTreeWidgetItem([f"{label}  ({len(items)})", "", ""])
            top.setData(0, ROLE_LABEL, label)
            top.setIcon(0, _swatch(color_for(items[0].category)))
            top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            self.tree.addTopLevelItem(top)
            for h in items:
                child = QTreeWidgetItem([h.text.replace("\n", " "), str(h.page + 1), h.source])
                child.setData(0, ROLE_HIT, h.id)
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked if h.enabled else Qt.CheckState.Unchecked)
                if not h.enabled:
                    child.setForeground(0, QBrush(QColor(140, 140, 140)))
                top.addChild(child)
            top.setExpanded(label in expanded or self._expanded)
        n_on = sum(1 for h in hits if h.enabled)
        if analyzed or hits:
            self.summary.setText(f"<b>{n_on}</b> Stellen werden geschwärzt"
                                 + (f", {len(hits) - n_on} ausgenommen." if len(hits) > n_on else "."))
        else:
            self.summary.setText("Noch keine Analyse.")
        self._updating = False

    # ------------------------------------------------------------ Interaktion
    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        hid = item.data(0, ROLE_HIT)
        if hid is None:
            return
        hit = self._hits.get(hid)
        if hit is None:
            return
        on = item.checkState(0) == Qt.CheckState.Checked
        if hit.enabled != on:
            hit.enabled = on
            self._updating = True
            item.setForeground(0, QBrush(QColor(0, 0, 0) if on else QColor(140, 140, 140)))
            self._updating = False
            self.hits_changed.emit()

    def _double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        hid = item.data(0, ROLE_HIT)
        if hid is not None:
            self.hit_selected.emit(hid)

    def _set_all(self, on: bool) -> None:
        for h in self._hits.values():
            h.enabled = on
        self.set_hits(list(self._hits.values()), True)
        self.hits_changed.emit()

    def _toggle_expand(self) -> None:
        self._expanded = not self._expanded
        if self._expanded:
            self.tree.expandAll()
        else:
            self.tree.collapseAll()

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        label = item.data(0, ROLE_LABEL)
        hid = item.data(0, ROLE_HIT)
        if label is not None:
            act = menu.addAction(f"Kürzel „{label}“ überall ändern …")
            act.triggered.connect(lambda: self._rename(label))
        if hid is not None:
            hit = self._hits[hid]
            act = menu.addAction("Kürzel nur für diese Stelle ändern …")
            act.triggered.connect(lambda: self._rename_one(hit))
            if hit.priority == PRIO_MANUAL:
                act2 = menu.addAction("Manuelle Schwärzung löschen")
                act2.triggered.connect(lambda: self.hit_removed.emit(hit.id))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _rename(self, label: str) -> None:
        new, ok = QInputDialog.getText(self, "Kürzel ändern", f"Neues Kürzel für „{label}“:", text=label)
        new = new.strip()
        if ok and new and new != label:
            self.label_renamed.emit(label, new)

    def _rename_one(self, hit: Hit) -> None:
        labels = sorted({h.label for h in self._hits.values()})
        new, ok = QInputDialog.getItem(self, "Kürzel ändern", f"Kürzel für „{hit.text}“:", labels,
                                       labels.index(hit.label) if hit.label in labels else 0, True)
        new = new.strip()
        if ok and new:
            hit.label = new
            self.set_hits(list(self._hits.values()), True)
            self.hits_changed.emit()
