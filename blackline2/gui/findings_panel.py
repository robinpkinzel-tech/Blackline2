"""Rückfragen der KI, ungelesene Bereiche und die Liste aller Funde des aktuellen Dokuments.

Entscheidungen gelten für alle gleichen Stellen in allen geladenen Dokumenten
(Häkchen am Kürzel, Rückfrage-Antwort). Nur das Häkchen an einer einzelnen
Fundstelle wirkt allein auf diese Stelle.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMenu, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from blackline2.analysis import group_members
from blackline2.gui.page_view import color_for
from blackline2.model import PRIO_MANUAL, Document, Hit

ROLE_HIT = Qt.ItemDataRole.UserRole + 1
ROLE_LABEL = Qt.ItemDataRole.UserRole + 2
ROLE_GROUP = Qt.ItemDataRole.UserRole + 3


def _swatch(color: QColor) -> QIcon:
    pm = QPixmap(12, 12)
    pm.fill(color)
    return QIcon(pm)


class FindingsPanel(QWidget):
    hit_selected = Signal(int)               # Treffer anzeigen
    hits_changed = Signal()                  # an/aus oder Kürzel geändert
    label_renamed = Signal(str, str)         # alt, neu (für alle Dokumente)
    hit_removed = Signal(int)
    group_toggled = Signal(str, bool)        # Gruppe überall an/aus
    question_decided = Signal(str, bool)     # Gruppe, schwärzen?
    next_unread = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel("Noch keine Analyse.")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        # --- Rückfragen der KI
        self.q_box = QGroupBox("❓ Rückfragen der KI – Antwort gilt für alle gleichen Stellen")
        qv = QVBoxLayout(self.q_box)
        self.q_tree = QTreeWidget()
        self.q_tree.setHeaderHidden(True)
        self.q_tree.setRootIsDecorated(False)
        self.q_tree.setMaximumHeight(170)
        self.q_tree.itemClicked.connect(self._question_clicked)
        qv.addWidget(self.q_tree)
        qrow = QHBoxLayout()
        self.q_yes = QPushButton("✔ Schwärzen")
        self.q_no = QPushButton("✘ Nicht schwärzen")
        self.q_yes.clicked.connect(lambda: self._decide(True))
        self.q_no.clicked.connect(lambda: self._decide(False))
        qrow.addWidget(self.q_yes)
        qrow.addWidget(self.q_no)
        qv.addLayout(qrow)
        lay.addWidget(self.q_box)
        self.q_box.setVisible(False)

        # --- Ungelesene Bereiche
        self.unread_row = QWidget()
        ur = QHBoxLayout(self.unread_row)
        ur.setContentsMargins(0, 0, 0, 0)
        self.unread_label = QLabel("")
        self.unread_label.setWordWrap(True)
        self.unread_label.setStyleSheet("color: #b36b00;")
        b_next = QPushButton("Anzeigen ▶")
        b_next.clicked.connect(self.next_unread)
        ur.addWidget(self.unread_label, 1)
        ur.addWidget(b_next)
        lay.addWidget(self.unread_row)
        self.unread_row.setVisible(False)

        # --- Fundliste
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Kürzel / Fundstelle", "Seite", "Quelle"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemClicked.connect(self._clicked)
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
        tip = QLabel("Häkchen am Kürzel = alle gleichen Stellen in allen Dokumenten. Häkchen an einer Fundstelle = "
                     "nur diese. Rechtsklick = Kürzel ändern. In der Seite: Klick auf eine Markierung = überall, "
                     "Strg+Klick = nur dort, Rechtsklick auf ein Wort = nachträglich schwärzen.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray; font-size: 11px;")
        lay.addWidget(tip)
        self._hits: dict[int, Hit] = {}
        self._docs: list[Document] = []
        self._updating = False
        self._expanded = False

    # ------------------------------------------------------------ Aufbau
    def set_hits(self, hits: list[Hit], analyzed: bool, docs: list[Document] | None = None) -> None:
        self._docs = docs or []
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
                                              else 2 if groups[l][0].category == "name" else 3, l))
        total_by_label: dict[str, int] = {}
        for d in self._docs:
            for h in d.hits:
                total_by_label[h.label] = total_by_label.get(h.label, 0) + 1
        for label in order:
            items = groups[label]
            total = total_by_label.get(label, len(items))
            extra = f" · gesamt {total}" if total > len(items) else ""
            top = QTreeWidgetItem([f"{label}  ({len(items)}{extra})", "", ""])
            top.setData(0, ROLE_LABEL, label)
            top.setIcon(0, _swatch(color_for(items[0].category)))
            top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            on = sum(1 for h in items if h.enabled)
            top.setCheckState(0, Qt.CheckState.Checked if on == len(items)
                              else Qt.CheckState.Unchecked if on == 0 else Qt.CheckState.PartiallyChecked)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            self.tree.addTopLevelItem(top)
            for h in items:
                text = h.text.replace("\n", " ")
                if h.question:
                    text = "❓ " + text
                child = QTreeWidgetItem([text, str(h.page + 1), h.source])
                child.setData(0, ROLE_HIT, h.id)
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked if h.enabled else Qt.CheckState.Unchecked)
                if not h.enabled:
                    child.setForeground(0, QBrush(QColor(140, 140, 140)))
                if h.question:
                    child.setToolTip(0, f"Rückfrage der KI: {h.question}")
                top.addChild(child)
            top.setExpanded(label in expanded or self._expanded)
        n_on = sum(1 for h in hits if h.enabled)
        if analyzed or hits:
            self.summary.setText(f"<b>{n_on}</b> Stellen werden geschwärzt"
                                 + (f", {len(hits) - n_on} ausgenommen." if len(hits) > n_on else "."))
        else:
            self.summary.setText("Noch keine Analyse.")
        self._fill_questions()
        self._updating = False

    def _fill_questions(self) -> None:
        self.q_tree.clear()
        seen: dict[str, tuple[Hit, int, int]] = {}
        for d in self._docs:
            doc_groups: set[str] = set()
            for h in d.hits:
                if h.question and h.group:
                    first, n, m = seen.get(h.group, (h, 0, 0))
                    seen[h.group] = (first, n + 1, m + (0 if h.group in doc_groups else 1))
                    doc_groups.add(h.group)
        for group, (h, n, m) in seen.items():
            where = f"{n} Stelle{'n' if n != 1 else ''}" + (f" in {m} Dokumenten" if m > 1 else "")
            state = "wird geschwärzt" if h.enabled else "wird NICHT geschwärzt"
            item = QTreeWidgetItem([f"{h.text}  →  {h.label}\n{h.question}  ·  {where}  ·  zurzeit: {state}"])
            item.setData(0, ROLE_GROUP, group)
            item.setToolTip(0, "Anklicken, dann unten entscheiden. Die Antwort gilt für alle Stellen.")
            self.q_tree.addTopLevelItem(item)
        has = self.q_tree.topLevelItemCount() > 0
        self.q_box.setVisible(has)
        if has:
            self.q_tree.setCurrentItem(self.q_tree.topLevelItem(0))

    def set_unread_info(self, pages_with_unread: list[int], total: int) -> None:
        if not total:
            self.unread_row.setVisible(False)
            return
        pages = ", ".join(str(p + 1) for p in pages_with_unread[:8]) + (" …" if len(pages_with_unread) > 8 else "")
        self.unread_label.setText(f"⚠ {total} ungelesene Bereiche (Handschrift, Unterschrift, Stempel?) "
                                  f"auf Seite {pages} – bitte ansehen und ggf. schwärzen.")
        self.unread_row.setVisible(True)

    # ------------------------------------------------------------ Rückfragen
    def _question_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        group = item.data(0, ROLE_GROUP)
        for d in self._docs:
            for h in d.hits:
                if h.group == group:
                    self.hit_selected.emit(h.id)
                    return

    def _decide(self, redact: bool) -> None:
        item = self.q_tree.currentItem()
        if item is None:
            return
        self.question_decided.emit(item.data(0, ROLE_GROUP), redact)

    # ------------------------------------------------------------ Interaktion
    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        label = item.data(0, ROLE_LABEL)
        if label is not None:
            on = item.checkState(0) != Qt.CheckState.Unchecked
            groups = {h.group for h in self._hits.values() if h.label == label and h.group}
            changed = False
            for g in groups:
                for _d, h in group_members(self._docs, g):
                    if h.enabled != on:
                        h.enabled = on
                        changed = True
            for h in self._hits.values():  # auch Stellen ohne Gruppe (manuelle Bereiche)
                if h.label == label and h.enabled != on:
                    h.enabled = on
                    changed = True
            if changed:
                self.hits_changed.emit()
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
            self.hits_changed.emit()

    def _clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        hid = item.data(0, ROLE_HIT)
        if hid is not None:
            self.hit_selected.emit(hid)

    def _set_all(self, on: bool) -> None:
        for h in self._hits.values():
            h.enabled = on
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
            if hit.group:
                a_all = menu.addAction(f"„{hit.text}“ überall {'nicht ' if hit.enabled else ''}schwärzen")
                a_all.triggered.connect(lambda: self.group_toggled.emit(hit.group, not hit.enabled))
            a_one = menu.addAction(f"Nur diese Stelle {'nicht ' if hit.enabled else ''}schwärzen")
            a_one.triggered.connect(lambda: self._toggle_one(hit))
            act = menu.addAction("Kürzel nur für diese Stelle ändern …")
            act.triggered.connect(lambda: self._rename_one(hit))
            if hit.priority == PRIO_MANUAL:
                act2 = menu.addAction("Manuelle Schwärzung löschen")
                act2.triggered.connect(lambda: self.hit_removed.emit(hit.id))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _toggle_one(self, hit: Hit) -> None:
        hit.enabled = not hit.enabled
        self.hits_changed.emit()

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
            self.hits_changed.emit()
