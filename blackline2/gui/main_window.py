"""Hauptfenster von Blackline 2."""

from __future__ import annotations

import time
from pathlib import Path

import pymupdf
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QMenu,
                               QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter, QTabWidget,
                               QToolBar, QVBoxLayout, QWidget)

from blackline2 import APP_NAME, __version__, paths, session
from blackline2.ai.detector import AIDetector
from blackline2.analysis import (Analyzer, AnalysisReport, apply_user_term, assign_groups, group_members,
                                 merge_page_hits, set_group_enabled)
from blackline2.export import ExportResult, export_document, output_path_for
from blackline2.gui import ai_manager as aim
from blackline2.gui.findings_panel import FindingsPanel
from blackline2.gui.input_panel import InputPanel
from blackline2.gui.page_view import RENDER_DPI, PageView
from blackline2.gui.settings_dialog import SettingsDialog
from blackline2.gui.workers import Worker
from blackline2.labels import PersonRegistry
from blackline2.loader import SUPPORTED_EXT, LoadError, load_document
from blackline2.model import PRIO_MANUAL, Document, Hit, Segment
from blackline2.mupdf_lock import LOCK
from blackline2.settings import Settings

STATE_COLORS = {aim.READY: "#2e7d32", aim.STARTING: "#ef6c00", aim.ERROR: "#c62828",
                aim.MISSING: "#757575", aim.OFF: "#757575"}


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.setWindowTitle(f"{APP_NAME} – KI-Schwärzung")
        self.resize(1500, 950)
        self.setAcceptDrops(True)

        self.docs: list[Document] = []
        self.current: Document | None = None
        self.page_no = 0
        self.highlight_id: int | None = None
        self.registry = PersonRegistry()
        self._render_docs: dict[int, pymupdf.Document] = {}
        self._pix_cache: dict[tuple[int, int], QPixmap] = {}
        self.worker: Worker | None = None
        self._work_started = 0.0
        self._pending_session: dict | None = None
        self._last_manual_label = "geschwärzt"
        self._setup_offered = False
        self.auto_offer_setup = True
        self._refresh_timer = QTimer(self, singleShot=True, interval=60)
        self._refresh_timer.timeout.connect(self._refresh_all)

        self.ai = aim.AIManager(settings, self)
        self.ai.state_changed.connect(self._ai_state)

        self._build_ui()
        self._update_actions()
        self.view.clear_page("PDFs oder Bilder hierher ziehen oder über „Dateien öffnen“ laden.")

    # ================================================================ UI-Aufbau
    def _build_ui(self) -> None:
        tb = QToolBar("Werkzeuge")
        tb.setIconSize(QSize(18, 18))
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(tb)

        def action(text: str, slot, shortcut: str | None = None, checkable: bool = False) -> QAction:
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setCheckable(checkable)
            tb.addAction(a)
            return a

        self.act_open = action("📂 Dateien öffnen", self.open_files, "Ctrl+O")
        self.act_new = action("🗑 Neuer Vorgang", self.new_case)
        tb.addSeparator()
        menu = self.menuBar().addMenu("&Datei")
        menu.addAction(self.act_open)
        a_save = QAction("Vorgang sichern …", self, shortcut=QKeySequence("Ctrl+Shift+S"))
        a_save.triggered.connect(self.save_session)
        a_load = QAction("Vorgang laden …", self, shortcut=QKeySequence("Ctrl+L"))
        a_load.triggered.connect(self.load_session)
        menu.addAction(a_save)
        menu.addAction(a_load)
        menu.addSeparator()
        a_setup = QAction("KI einrichten / aktualisieren …", self)
        a_setup.triggered.connect(self.open_setup)
        menu.addAction(a_setup)
        menu.addSeparator()
        menu.addAction(self.act_new)
        a_quit = QAction("Beenden", self, shortcut=QKeySequence("Ctrl+Q"))
        a_quit.triggered.connect(self.close)
        menu.addAction(a_quit)
        self.act_save_session = a_save
        self.act_analyze = action("▶ Analysieren", self.analyze, "F5")
        self.act_cancel = action("⏹ Abbrechen", self.cancel_work)
        tb.addSeparator()
        self.act_preview = action("👁 Vorschau Schwärzung", self._refresh_view, "Ctrl+P", checkable=True)
        self.act_preview.setToolTip("Zeigt das Ergebnis. In der Vorschau: Bereich mit der Maus aufziehen, "
                                    "Kürzel tippen, Enter – fertig.")
        self.act_manual = action("▭ Bereich manuell schwärzen", self._toggle_manual, "Ctrl+M", checkable=True)
        self.act_unread = action("⚠ Ungelesene Bereiche", self._refresh_view, "Ctrl+U", checkable=True)
        self.act_unread.setChecked(True)
        self.act_unread.setToolTip("Gelb gestrichelt: Handschrift, Unterschriften, Stempel – von Texterkennung "
                                   "und KI nicht lesbar. Bitte selbst prüfen.")
        tb.addSeparator()
        self.act_export = action("💾 Geschwärzt speichern", self.export, "Ctrl+S")
        tb.addSeparator()
        action("⚙ Einstellungen", self.open_settings)
        action("? Hilfe", self.show_help, "F1")

        # Links: Dokumente
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(4, 4, 4, 4)
        lv.addWidget(QLabel("<b>Dokumente</b>"))
        self.doc_list = QListWidget()
        self.doc_list.currentRowChanged.connect(self._select_doc)
        lv.addWidget(self.doc_list, 1)
        b_rm = QPushButton("Dokument entfernen")
        b_rm.clicked.connect(self.remove_current_doc)
        lv.addWidget(b_rm)

        # Mitte: Seite
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        nav = QHBoxLayout()
        self.btn_prev = QPushButton("◀")
        self.btn_next = QPushButton("▶")
        self.page_spin = QSpinBox(minimum=1, maximum=1)
        self.page_label = QLabel("/ 0")
        self.btn_prev.clicked.connect(lambda: self.goto_page(self.page_no - 1))
        self.btn_next.clicked.connect(lambda: self.goto_page(self.page_no + 1))
        self.page_spin.valueChanged.connect(lambda v: self.goto_page(v - 1))
        b_zi, b_zo, b_fit = QPushButton("＋"), QPushButton("－"), QPushButton("Breite")
        b_zi.clicked.connect(lambda: self.view.zoom(1.2))
        b_zo.clicked.connect(lambda: self.view.zoom(1 / 1.2))
        b_fit.clicked.connect(lambda: self.view.fit_width())
        self.page_info = QLabel("")
        self.page_info.setStyleSheet("color: gray;")
        for w in (self.btn_prev, QLabel("Seite"), self.page_spin, self.page_label, self.btn_next):
            nav.addWidget(w)
        nav.addSpacing(20)
        for w in (b_zo, b_zi, b_fit):
            nav.addWidget(w)
        nav.addSpacing(20)
        nav.addWidget(self.page_info, 1)
        cv.addLayout(nav)
        self.view = PageView()
        self.view.hit_toggled.connect(self._toggle_hit)
        self.view.rect_drawn.connect(self._manual_rect)
        self.view.rect_labeled.connect(self._rect_labeled)
        self.view.unread_clicked.connect(self._unread_clicked)
        self.view.context_requested.connect(self._context_menu)
        cv.addWidget(self.view, 1)

        # Rechts: Angaben + Funde
        self.tabs = QTabWidget()
        self.inputs = InputPanel(self.settings)
        self.inputs.analyze_requested.connect(self.analyze)
        self.findings = FindingsPanel()
        self.findings.hit_selected.connect(self._show_hit)
        self.findings.hits_changed.connect(self._schedule_refresh)
        self.findings.label_renamed.connect(self._rename_label)
        self.findings.hit_removed.connect(self._remove_hit)
        self.findings.group_toggled.connect(self._group_toggled)
        self.findings.question_decided.connect(self._question_decided)
        self.findings.next_unread.connect(self._next_unread)
        self.tabs.addTab(self.inputs, "1. Angaben")
        self.tabs.addTab(self.findings, "2. Funde prüfen")

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(center)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([220, 850, 430])
        self.setCentralWidget(split)

        sb = self.statusBar()
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setVisible(False)
        self.status_msg = QLabel("Bereit.")
        self.ai_label = QPushButton("● KI: –")
        self.ai_label.setFlat(True)
        self.ai_label.clicked.connect(self.show_ai_details)
        sb.addWidget(self.status_msg, 1)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.ai_label)

    # ================================================================ KI-Status
    def start_ai(self) -> None:
        if self.settings.ki_mode == "aus" or self.settings.ki_autostart or self.settings.ki_mode == "extern":
            self.ai.start()
        else:
            self._ai_state(aim.OFF, "KI wird bei der ersten Analyse gestartet")

    def _ai_state(self, state: str, message: str) -> None:
        color = STATE_COLORS.get(state, "#757575")
        if state == aim.MISSING and self.auto_offer_setup and not self._setup_offered:
            self._setup_offered = True
            QTimer.singleShot(600, self._offer_setup)
        self.ai_label.setText(f"● {message}")
        self.ai_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        self.ai_label.setToolTip("Die lokale KI läuft nur, solange Blackline 2 geöffnet ist.\n"
                                 "Klicken für Details.")

    def _offer_setup(self) -> None:
        if QMessageBox.question(
                self, "KI einrichten",
                "Die lokale KI ist auf diesem Rechner noch nicht eingerichtet.\n\n"
                "Blackline 2 kann das KI-Programm, ein Sprachmodell (2,5–5 GB) und die deutschen "
                "Texterkennungsdaten jetzt herunterladen. Alles bleibt auf diesem Rechner.\n\n"
                "Jetzt einrichten?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.open_setup()

    def open_setup(self) -> None:
        from blackline2.gui.setup_dialog import SetupDialog

        if not self._can_start():
            return
        dlg = SetupDialog(self.settings, self)
        dlg.finished_ok.connect(self.ai.start)
        dlg.exec()

    def show_ai_details(self) -> None:
        d = QDialog(self)
        d.setWindowTitle("KI-Status")
        d.resize(760, 460)
        lay = QVBoxLayout(d)
        info = [f"<b>Status:</b> {self.ai.message}",
                f"<b>Modus:</b> {self.settings.ki_mode}",
                f"<b>llama-server:</b> {self.ai.server_path() or '—'}",
                f"<b>Modell:</b> {self.ai.model_path() or '—'}",
                "<br>Die KI läuft ausschließlich auf diesem Rechner (127.0.0.1) und wird beim "
                "Schließen von Blackline 2 automatisch beendet."]
        if self.ai.state == aim.MISSING:
            info.append("<br><b>Einrichtung:</b> Menü „Datei → KI einrichten“ (oder im Programmordner "
                        "<code>python -m blackline2.setup_ki</code>).")
        lbl = QLabel("<br>".join(info))
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(lbl)
        log = QPlainTextEdit(self.ai.log_text() or "(kein Protokoll)")
        log.setReadOnly(True)
        lay.addWidget(log, 1)
        row = QHBoxLayout()
        b_setup = QPushButton("KI einrichten …")
        b_setup.clicked.connect(lambda: (d.accept(), self.open_setup()))
        row.addWidget(b_setup)
        b_restart = QPushButton("KI neu starten")
        b_restart.clicked.connect(lambda: (self.ai.start(), d.accept()))
        b_stop = QPushButton("KI beenden")
        b_stop.clicked.connect(lambda: (self.ai.stop(), d.accept()))
        row.addWidget(b_restart)
        row.addWidget(b_stop)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(d.reject)
        row.addWidget(bb)
        lay.addLayout(row)
        d.exec()

    # ================================================================ Dokumente
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        files = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        expanded: list[Path] = []
        for f in files:
            if f.is_dir():
                expanded += sorted(p for p in f.iterdir() if p.suffix.lower() in SUPPORTED_EXT)
            else:
                expanded.append(f)
        self.load_files(expanded)

    def open_files(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_EXT))
        files, _ = QFileDialog.getOpenFileNames(self, "Dokumente öffnen", "",
                                                f"PDFs und Bilder ({exts});;Alle Dateien (*)")
        if files:
            self.load_files([Path(f) for f in files])

    def load_files(self, files: list[Path]) -> None:
        files = [f for f in files if f.suffix.lower() in SUPPORTED_EXT | {".doc", ".docx"}]
        known = {d.path.resolve() for d in self.docs}
        files = [f for f in files if f.resolve() not in known]
        if not files or not self._can_start():
            return

        settings = self.settings

        def job(progress, cancel, paths_):
            loaded, errors = [], []
            for i, p in enumerate(paths_):
                progress(i, len(paths_), f"Lade {p.name} …")
                try:
                    def sub(done, total, msg, i=i):
                        progress(i, len(paths_), f"[{i + 1}/{len(paths_)}] {msg} ({done}/{total})")
                    loaded.append(load_document(p, settings, sub, cancel))
                except LoadError as exc:
                    errors.append(str(exc))
            return loaded, errors

        self._run(job, files, on_done=self._loaded, label="Texterkennung")

    def _loaded(self, result) -> None:
        loaded, errors = result
        restored = dropped = 0
        for d in loaded:
            if self._pending_session is not None:
                ok, bad = session.apply_session_hits(self._pending_session, d)
                restored += ok
                dropped += bad
            self.docs.append(d)
            item = QListWidgetItem()
            self.doc_list.addItem(item)
        self._pending_session = None
        self._update_doc_list()
        if loaded:
            self.doc_list.setCurrentRow(len(self.docs) - len(loaded))
            ocr_pages = sum(1 for d in loaded for p in d.pages if p.source == "ocr")
            unread = sum(len(p.unread) for d in loaded for p in d.pages)
            total = sum(len(d.pages) for d in loaded)
            msg = (f"{len(loaded)} Dokument(e) geladen, {total} Seiten "
                   f"({ocr_pages} per Texterkennung gelesen).")
            if unread:
                msg += f" ⚠ {unread} ungelesene Bereiche (Handschrift/Stempel?) – bitte ansehen."
            notes = [f"{d.name}: {d.note}" for d in loaded if d.note]
            if notes:
                msg += "  " + "; ".join(notes)
            if restored or dropped:
                msg += f"  Vorgang wiederhergestellt: {restored} Funde" + (
                    f", {dropped} nicht mehr zuzuordnen" if dropped else "") + "."
            self.status_msg.setText(msg)
        if errors:
            QMessageBox.warning(self, "Nicht geladen", "\n\n".join(errors))
        self._update_actions()

    def _update_doc_list(self) -> None:
        for i, d in enumerate(self.docs):
            item = self.doc_list.item(i)
            state = ("✔ gespeichert" if d.exported_to else
                     f"{sum(h.enabled for h in d.hits)} Funde" if d.analyzed else "nicht analysiert")
            item.setText(f"{d.name}\n   {len(d.pages)} S. · {state}")
            item.setToolTip(str(d.path) + (f"\n{d.note}" if d.note else ""))

    def _select_doc(self, row: int) -> None:
        self.current = self.docs[row] if 0 <= row < len(self.docs) else None
        self.page_no = 0
        self.highlight_id = None
        self._refresh_all()

    def remove_current_doc(self) -> None:
        row = self.doc_list.currentRow()
        if row < 0 or not self._can_start():
            return
        d = self.docs.pop(row)
        rd = self._render_docs.pop(id(d), None)
        if rd is not None:
            rd.close()
        self._pix_cache = {k: v for k, v in self._pix_cache.items() if k[0] != id(d)}
        self.doc_list.takeItem(row)
        if not self.docs:
            self.current = None
            self._refresh_all()
        self._update_actions()

    def new_case(self) -> None:
        if not self._can_start():
            return
        if self.docs and QMessageBox.question(
                self, "Neuer Vorgang", "Alle Dokumente, Funde und Eingaben verwerfen?") != QMessageBox.StandardButton.Yes:
            return
        for rd in self._render_docs.values():
            rd.close()
        self._render_docs.clear()
        self._pix_cache.clear()
        self.docs.clear()
        self.doc_list.clear()
        self.current = None
        self.registry = PersonRegistry()
        self.inputs.clear()
        self.tabs.setCurrentIndex(0)
        self._refresh_all()
        self._update_actions()
        self.view.clear_page("PDFs oder Bilder hierher ziehen oder über „Dateien öffnen“ laden.")

    # ================================================================ Seitenanzeige
    def _render(self, doc: Document, page: int) -> QPixmap | None:
        key = (id(doc), page)
        if key in self._pix_cache:
            return self._pix_cache[key]
        if not LOCK.acquire(timeout=0.3):
            QTimer.singleShot(400, self._refresh_view)
            return None
        try:
            rd = self._render_docs.get(id(doc))
            if rd is None:
                rd = pymupdf.open("pdf", doc.pdf_bytes)
                self._render_docs[id(doc)] = rd
            pix = rd[page].get_pixmap(dpi=RENDER_DPI, alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        finally:
            LOCK.release()
        qpix = QPixmap.fromImage(img)
        if len(self._pix_cache) > 12:
            self._pix_cache.pop(next(iter(self._pix_cache)))
        self._pix_cache[key] = qpix
        return qpix

    def goto_page(self, n: int) -> None:
        if not self.current:
            return
        n = max(0, min(n, len(self.current.pages) - 1))
        if n != self.page_no:
            self.page_no = n
            self.highlight_id = None
        self._refresh_view()

    def _refresh_view(self) -> None:
        d = self.current
        if d is None:
            self.view.clear_page("Kein Dokument ausgewählt.")
            self.page_label.setText("/ 0")
            self.page_info.setText("")
            return
        p = d.pages[self.page_no]
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(len(d.pages))
        self.page_spin.setValue(self.page_no + 1)
        self.page_spin.blockSignals(False)
        self.page_label.setText(f"/ {len(d.pages)}")
        hits = d.hits_on(self.page_no)
        self.page_info.setText(
            f"{'Texterkennung' if p.source == 'ocr' else 'digitaler Text'} · {len(p.words)} Wörter · "
            f"{sum(h.enabled for h in hits)} Schwärzungen auf dieser Seite")
        pix = self._render(d, self.page_no)
        self.view.show_page(pix, p.width, p.height, hits, self.act_preview.isChecked(), self.highlight_id,
                            p.unread if self.act_unread.isChecked() else None)

    def _schedule_refresh(self) -> None:
        self._refresh_timer.start()

    def _refresh_all(self) -> None:
        self._refresh_view()
        if self.current:
            self.findings.set_hits(self.current.hits, self.current.analyzed, self.docs)
            pages = [p.index for p in self.current.pages if p.unread]
            self.findings.set_unread_info(pages, sum(len(self.current.pages[i].unread) for i in pages))
        else:
            self.findings.set_hits([], False, self.docs)
            self.findings.set_unread_info([], 0)
        self._update_doc_list()

    def _show_hit(self, hit_id: int) -> None:
        if not self.current:
            return
        hit = next((h for h in self.current.hits if h.id == hit_id), None)
        if hit is None:
            return
        self.page_no = hit.page
        self.highlight_id = hit.id
        self._refresh_view()
        self.view.center_on_hit(hit)

    def _find_hit(self, hit_id: int) -> Hit | None:
        for d in self.docs:
            for h in d.hits:
                if h.id == hit_id:
                    return h
        return None

    def _toggle_hit(self, hit_id: int, everywhere: bool = True) -> None:
        hit = self._find_hit(hit_id)
        if hit is None:
            return
        new_state = not hit.enabled
        if everywhere and hit.group:
            n = set_group_enabled(self.docs, hit.group, new_state)
            self.status_msg.setText(f"„{hit.text}“ ({hit.label}): {n} Stelle(n) in allen Dokumenten "
                                    f"{'geschwärzt' if new_state else 'ausgenommen'}. "
                                    f"Strg+Klick ändert nur eine einzelne Stelle.")
        else:
            hit.enabled = new_state
        self._refresh_all()

    def _group_toggled(self, group: str, enabled: bool) -> None:
        n = set_group_enabled(self.docs, group, enabled)
        self.status_msg.setText(f"{n} Stelle(n) {'geschwärzt' if enabled else 'ausgenommen'}.")
        self._refresh_all()

    def _question_decided(self, group: str, redact: bool) -> None:
        members = group_members(self.docs, group)
        for _d, h in members:
            h.enabled = redact
            h.question = ""
        self.status_msg.setText(f"Rückfrage beantwortet: {len(members)} Stelle(n) "
                                f"{'werden geschwärzt' if redact else 'bleiben lesbar'}.")
        self._refresh_all()

    # ------------------------------------------------------------ Rechtsklick / nachträgliche Begriffe
    def _context_menu(self, x: float, y: float, hit_id: int, global_pos) -> None:
        d = self.current
        if d is None:
            return
        menu = QMenu(self)
        hit = self._find_hit(hit_id) if hit_id >= 0 else None
        word_idx = d.word_at(self.page_no, x, y)
        word = d.pages[self.page_no].words[word_idx].text if word_idx is not None else ""
        if hit is not None:
            verb = "nicht " if hit.enabled else "doch "
            if hit.group:
                a = menu.addAction(f"„{hit.text}“ überall {verb}schwärzen")
                a.triggered.connect(lambda: self._toggle_hit(hit.id, True))
            a = menu.addAction(f"Nur diese Stelle {verb}schwärzen")
            a.triggered.connect(lambda: self._toggle_hit(hit.id, False))
            a = menu.addAction(f"Kürzel „{hit.label}“ überall ändern …")
            a.triggered.connect(lambda: self._ask_rename(hit.label))
            if hit.priority == PRIO_MANUAL:
                a = menu.addAction("Manuelle Schwärzung löschen")
                a.triggered.connect(lambda: self._remove_hit(hit.id))
            menu.addSeparator()
        if word:
            clean = word.strip(".,;:!?()[]\"'„“")
            a = menu.addAction(f"„{clean}“ überall schwärzen (alle Dokumente) …")
            a.triggered.connect(lambda: self._add_term(clean, everywhere=True))
            a = menu.addAction(f"„{clean}“ nur hier schwärzen …")
            a.triggered.connect(lambda: self._add_term(clean, everywhere=False, page=self.page_no,
                                                       segments=[Segment(word_idx)]))
        if menu.isEmpty():
            return
        menu.exec(global_pos)

    def _ask_label(self, title: str, text: str, default: str = "") -> str | None:
        labels = sorted({h.label for dd in self.docs for h in dd.hits})
        for std in ("Mandant", "Gegner", "Adresse Mandant", "Adresse Gegner", "geschwärzt"):
            if std not in labels:
                labels.append(std)
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        form = QFormLayout(dlg)
        info = QLabel(text)
        info.setWordWrap(True)
        form.addRow(info)
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(labels)
        combo.setCurrentText(default or ("geschwärzt" if "geschwärzt" in labels else labels[0]))
        form.addRow("Kürzel:", combo)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return combo.currentText().strip() or None

    def _add_term(self, term: str, everywhere: bool, page: int | None = None,
                  segments: list[Segment] | None = None) -> None:
        d = self.current
        if d is None or not term:
            return
        label = self._ask_label("Nachträglich schwärzen",
                                f"„{term}“ {'in allen geladenen Dokumenten' if everywhere else 'nur an dieser Stelle'}"
                                " schwärzen. Welches Kürzel soll darüberstehen?")
        if not label:
            return
        if everywhere:
            n = apply_user_term(self.docs, term, label, self.settings.fuzzy_matching)
            self.status_msg.setText(f"„{term}“ → „{label}“: {n} Stelle(n) in allen Dokumenten geschwärzt.")
        else:
            p = d.pages[page if page is not None else self.page_no]
            segs = p.trim_segments(segments or [])
            hit = Hit(p.index, segs, p.segment_text(segs), label, "benutzer", PRIO_MANUAL)
            others = [h for h in d.hits if h.page != p.index]
            d.hits = others + merge_page_hits(p, d.hits_on(p.index) + [hit])
            assign_groups(self.docs)
        d.analyzed = True
        self._refresh_all()
        self._update_actions()

    def _ask_rename(self, label: str) -> None:
        new, ok = QInputDialog.getText(self, "Kürzel ändern", f"Neues Kürzel für „{label}“:", text=label)
        new = new.strip()
        if ok and new and new != label:
            self._rename_label(label, new)

    def _unread_clicked(self, index: int) -> None:
        d = self.current
        if d is None:
            return
        p = d.pages[self.page_no]
        if not 0 <= index < len(p.unread):
            return
        rect = p.unread[index]
        label = self._ask_label("Ungelesenen Bereich schwärzen",
                                "Dieser Bereich enthält Tinte, die weder Texterkennung noch KI lesen konnten "
                                "(Handschrift, Unterschrift, Stempel?). Schwärzen?")
        if not label:
            return
        d.hits.append(Hit(page=self.page_no, segments=[], text="(ungelesener Bereich)", label=label,
                          category="manuell", priority=PRIO_MANUAL, rects=[rect]))
        p.unread.pop(index)
        d.analyzed = True
        self._refresh_all()
        self._update_actions()

    def _next_unread(self) -> None:
        d = self.current
        if d is None:
            return
        order = list(range(self.page_no, len(d.pages))) + list(range(0, self.page_no))
        # auf der aktuellen Seite zuerst, sonst die nächste Seite mit ungelesenen Bereichen
        for i in order:
            if d.pages[i].unread:
                self.page_no = i
                self.act_unread.setChecked(True)
                self._refresh_view()
                self.view.center_on_rect(d.pages[i].unread[0])
                return
        self.status_msg.setText("Keine ungelesenen Bereiche mehr in diesem Dokument.")

    def _toggle_manual(self) -> None:
        self.view.set_manual_mode(self.act_manual.isChecked())

    def _manual_rect(self, rect: QRectF) -> None:
        """Bereich aufgezogen: weiß zeigen und sofort das Kürzel abfragen (Enter übernimmt)."""
        d = self.current
        if d is None:
            return
        r = (rect.left(), rect.top(), rect.right(), rect.bottom())
        p = d.pages[self.page_no]
        word_ids = d.words_in(self.page_no, r)
        phrase = " ".join(p.words[i].text for i in word_ids).strip(".,;:!?()[]\"'„“") if word_ids else ""
        labels = sorted({h.label for dd in self.docs for h in dd.hits} | {"geschwärzt", "Mandant", "Gegner"})
        self.view.begin_label_edit(rect, labels, phrase, default=self._last_manual_label)
        self.status_msg.setText("Kürzel eingeben und Enter drücken – Esc bricht ab.")

    def _rect_labeled(self, rect: QRectF, label: str, everywhere: bool) -> None:
        d = self.current
        if d is None:
            return
        self._last_manual_label = label
        r = (rect.left(), rect.top(), rect.right(), rect.bottom())
        p = d.pages[self.page_no]
        word_ids = d.words_in(self.page_no, r)
        phrase = " ".join(p.words[i].text for i in word_ids).strip(".,;:!?()[]\"'„“") if word_ids else ""
        d.hits.append(Hit(page=self.page_no, segments=[], text=phrase or "(manueller Bereich)", label=label,
                          category="manuell", priority=PRIO_MANUAL, rects=[r]))
        msg = f"Bereich als „{label}“ geschwärzt."
        if everywhere and phrase:
            n = apply_user_term(self.docs, phrase, label, self.settings.fuzzy_matching)
            msg = f"„{phrase}“ → „{label}“: Bereich und {n} weitere Stelle(n) geschwärzt."
        d.analyzed = True
        self._refresh_all()
        self._update_actions()
        self.status_msg.setText(msg)

    def _remove_hit(self, hit_id: int) -> None:
        if self.current:
            self.current.hits = [h for h in self.current.hits if h.id != hit_id]
            self._refresh_all()

    def _rename_label(self, old: str, new: str) -> None:
        self.registry.rename(old, new)
        for d in self.docs:
            for h in d.hits:
                if h.label == old:
                    h.label = new
                elif old in h.label.split("/"):
                    h.label = "/".join(new if p == old else p for p in h.label.split("/"))
        self._refresh_all()

    # ================================================================ Analyse
    def analyze(self) -> None:
        if not self.docs:
            QMessageBox.information(self, APP_NAME, "Bitte zuerst Dokumente laden.")
            return
        if not self._can_start():
            return
        inputs = self.inputs.inputs()
        if inputs.is_empty() and QMessageBox.question(
                self, APP_NAME, "Es sind keine Angaben zu Mandant/Gegner eingetragen.\n"
                                "Trotzdem nur mit KI und Regeln analysieren?") != QMessageBox.StandardButton.Yes:
            return
        todo = [d for d in self.docs if not d.analyzed]
        if not todo:
            if QMessageBox.question(self, APP_NAME, "Alle Dokumente wurden bereits analysiert.\n"
                                    "Erneut analysieren? (Manuelle Schwärzungen bleiben erhalten, "
                                    "ausgeschaltete Funde werden wieder eingeschaltet.)") != QMessageBox.StandardButton.Yes:
                return
            todo = list(self.docs)
            self.registry = PersonRegistry()

        use_ai = True
        if self.ai.state in (aim.OFF, aim.MISSING, aim.ERROR):
            if self.settings.ki_mode == "lokal" and self.ai.state == aim.OFF and not self.settings.ki_autostart:
                self.ai.start()
            if self.ai.state in (aim.OFF, aim.MISSING, aim.ERROR):
                msg = ("Die KI ist nicht verfügbar:\n" + self.ai.message + "\n\n"
                       "Ohne KI werden nur Ihre Angaben und die festen Regeln (E-Mail, Telefon, IBAN, "
                       "Versicherungsnr., Geburtsdatum) verwendet. Weitere Namen werden dann NICHT erkannt.\n\n"
                       "Trotzdem fortfahren?")
                if QMessageBox.warning(self, "KI nicht verfügbar", msg,
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                        != QMessageBox.StandardButton.Yes:
                    return
                use_ai = False

        settings, registry, ai = self.settings, self.registry, self.ai

        def job(progress, cancel, docs):
            detector = None
            if use_ai:
                if ai.state != aim.READY:
                    progress(0, 0, "Warte, bis die KI geladen ist …")
                    if not ai.wait_ready(settings.ki_start_timeout, cancel):
                        raise RuntimeError("Die KI konnte nicht gestartet werden: " + ai.message)
                client = ai.client()
                if client is None:
                    raise RuntimeError("Die KI ist nicht bereit.")
                detector = AIDetector(client, registry, inputs, settings.ki_exclude_professionals)
            return Analyzer(settings, inputs, registry, detector).run(docs, progress, cancel)

        self._run(job, todo, on_done=self._analyzed, label="Analyse")

    def _analyzed(self, report: AnalysisReport) -> None:
        self._refresh_all()
        self.tabs.setCurrentIndex(1)
        msg = f"Analyse fertig: {report.hit_count} Fundstellen."
        if not report.ai_used:
            msg += " (ohne KI)"
        questions = {h.group for d in self.docs for h in d.hits if h.question}
        if questions:
            msg += f"  ❓ {len(questions)} Rückfrage(n) der KI – bitte im Reiter „Funde prüfen“ beantworten."
        self.status_msg.setText(msg)
        if report.ai_errors:
            QMessageBox.warning(self, "KI-Fehler",
                                "Einige Seiten konnten von der KI nicht geprüft werden "
                                "(Regeln und Eingaben wurden trotzdem angewendet):\n\n"
                                + "\n".join(report.ai_errors[:15]))
        self._update_actions()

    # ================================================================ Export
    def export(self) -> None:
        docs = [d for d in self.docs if d.analyzed]
        if not docs:
            QMessageBox.information(self, APP_NAME, "Bitte zuerst analysieren.")
            return
        if not self._can_start():
            return
        default = str(docs[0].path.parent)
        folder = QFileDialog.getExistingDirectory(self, "Zielordner für die geschwärzten PDFs", default)
        if not folder:
            return
        folder_p = Path(folder)
        targets = [(d, output_path_for(d, folder_p, self.settings.export_suffix,
                                       self.settings.export_neutral_filename)) for d in docs]
        names = [t.name for _d, t in targets]
        if len(set(names)) != len(names):  # gleiche neutrale Namen durchnummerieren
            targets = [(d, t.with_name(f"{t.stem}_{i + 1}{t.suffix}")) for i, (d, t) in enumerate(targets)]
        existing = [t for _, t in targets if t.exists()]
        if existing and QMessageBox.question(
                self, APP_NAME, f"{len(existing)} Datei(en) existieren bereits und werden überschrieben:\n"
                + "\n".join(p.name for p in existing[:10])) != QMessageBox.StandardButton.Yes:
            return
        s = self.settings
        tessdata = s.tessdata_path or str(paths.find_tessdata() or "")

        def job(progress, cancel, targets_):
            results = []
            for i, (d, out) in enumerate(targets_):
                def sub(done, total, msg, i=i):
                    progress(i, len(targets_), f"[{i + 1}/{len(targets_)}] {msg}")
                results.append(export_document(d, out, s.export_mode, s.export_dpi, s.export_searchable,
                                               tessdata, s.ocr_languages, sub, cancel))
                d.exported_to = out
            return results

        self._run(job, targets, on_done=self._exported, label="Speichern")

    def _exported(self, results: list[ExportResult]) -> None:
        self._update_doc_list()
        warnings = [w for r in results for w in r.warnings]
        text = "\n".join(f"✔ {r.path.name}  ({r.pages} S., {r.redactions} Schwärzungen)" for r in results)
        box = QMessageBox(self)
        box.setWindowTitle("Gespeichert")
        if warnings:
            box.setIcon(QMessageBox.Icon.Warning)
            text += "\n\nACHTUNG – Prüfung hat Reste gefunden:\n" + "\n".join(warnings[:20])
        else:
            box.setIcon(QMessageBox.Icon.Information)
        box.setText(text)
        open_btn = box.addButton("Ordner öffnen", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is open_btn and results:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(results[0].path.parent)))
        self.status_msg.setText(f"{len(results)} geschwärzte Datei(en) gespeichert.")

    # ================================================================ Hintergrundarbeit
    def _can_start(self) -> bool:
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, APP_NAME, "Bitte warten, bis der laufende Vorgang fertig ist.")
            return False
        return True

    def _run(self, fn, *args, on_done, label: str) -> None:
        w = Worker(fn, *args, parent=self)
        self.worker = w
        self._work_started = time.monotonic()
        w.progress.connect(self._progress)
        w.succeeded.connect(on_done)
        w.failed.connect(lambda msg, det: self._failed(label, msg, det))
        w.cancelled.connect(lambda: self.status_msg.setText(f"{label} abgebrochen."))
        w.finished.connect(self._work_finished)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_msg.setText(f"{label} läuft …")
        self._update_actions()
        w.start()

    def _progress(self, done: int, total: int, msg: str) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            elapsed = time.monotonic() - self._work_started
            if done >= 2 and elapsed > 5 and done < total:
                rest = elapsed / done * (total - done)
                msg += f"   (noch ca. {_fmt_duration(rest)})"
        else:
            self.progress.setRange(0, 0)
        self.status_msg.setText(msg)

    def _failed(self, label: str, msg: str, details: str) -> None:
        box = QMessageBox(QMessageBox.Icon.Critical, f"{label} fehlgeschlagen", msg, parent=self)
        box.setDetailedText(details)
        box.exec()
        self.status_msg.setText(f"{label} fehlgeschlagen.")

    def _work_finished(self) -> None:
        self.progress.setVisible(False)
        self.worker = None
        self._update_actions()

    def cancel_work(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.status_msg.setText("Wird abgebrochen …")

    def _update_actions(self) -> None:
        busy = self.worker is not None and self.worker.isRunning()
        has_docs = bool(self.docs)
        self.act_analyze.setEnabled(has_docs and not busy)
        self.inputs.analyze_btn.setEnabled(has_docs and not busy)
        self.act_export.setEnabled(any(d.analyzed for d in self.docs) and not busy)
        self.act_cancel.setEnabled(busy)
        self.act_open.setEnabled(not busy)
        self.act_new.setEnabled(not busy)

    # ================================================================ Vorgang sichern / laden
    def save_session(self) -> None:
        if not self.docs:
            QMessageBox.information(self, APP_NAME, "Es ist kein Dokument geladen.")
            return
        default = str(self.docs[0].path.with_suffix(session.SUFFIX))
        path, _ = QFileDialog.getSaveFileName(self, "Vorgang sichern", default,
                                              f"Blackline-2-Vorgang (*{session.SUFFIX})")
        if not path:
            return
        if not path.endswith(session.SUFFIX):
            path += session.SUFFIX
        try:
            session.save_session(Path(path), self.docs, self.inputs.inputs(), self.registry)
        except OSError as exc:
            QMessageBox.critical(self, APP_NAME, f"Speichern fehlgeschlagen: {exc}")
            return
        self.status_msg.setText(f"Vorgang gesichert: {path}  (enthält Mandantendaten – vertraulich!)")

    def load_session(self) -> None:
        if not self._can_start():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Vorgang laden", "",
                                              f"Blackline-2-Vorgang (*{session.SUFFIX})")
        if not path:
            return
        try:
            data = session.load_session(Path(path))
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, APP_NAME, f"Vorgang konnte nicht geladen werden: {exc}")
            return
        files = session.session_paths(data)
        missing = [f for f in files if not f.exists()]
        if missing:
            QMessageBox.warning(self, APP_NAME, "Diese Dateien wurden nicht gefunden und werden übersprungen:\n"
                                + "\n".join(str(m) for m in missing))
        self.inputs.set_inputs(session.session_inputs(data))
        self.registry = PersonRegistry()
        session.restore_registry(data, self.registry)
        self._pending_session = data
        known = {d.path.resolve() for d in self.docs}
        todo = [f for f in files if f.exists() and f.resolve() not in known]
        if todo:
            self.load_files(todo)
        else:
            self._pending_session = None
            self.status_msg.setText("Alle Dokumente des Vorgangs sind bereits geladen.")

    # ================================================================ Sonstiges
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if dlg.apply():
                self.ai.start()

    def show_help(self) -> None:
        QMessageBox.information(self, f"{APP_NAME} {__version__}", HELP_TEXT)

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            if QMessageBox.question(self, APP_NAME, "Es läuft noch ein Vorgang. Trotzdem beenden?") \
                    != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(5000)
        self.status_msg.setText("KI wird beendet …")
        QApplication.processEvents()
        self.ai.stop()
        for rd in self._render_docs.values():
            rd.close()
        event.accept()


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{max(seconds, 1)} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


HELP_TEXT = """So funktioniert Blackline 2:

1. Dokumente laden (PDF oder Bild) – per „Dateien öffnen“ oder Ziehen ins Fenster.
   Eingescannte Seiten werden automatisch per Texterkennung gelesen.

2. Angaben eintragen: Mandant, Gegner (Name + Adresse) und bis zu 5 freie
   Suchbegriffe mit gewünschtem Kürzel.

3. „Analysieren“: Die lokale KI liest jede Seite und findet Namen und
   persönliche Daten. Zusätzlich greifen feste Regeln für E-Mail, Telefon,
   IBAN, Versicherungs-/Rentennummern und Geburtsdaten.

4. Funde prüfen: Klick auf eine Markierung schaltet sie überall (alle
   Dokumente) aus/ein, Strg+Klick nur an dieser Stelle. Rückfragen der KI
   oben im Reiter beantworten – die Antwort gilt für alle gleichen Stellen.
   Rechtsklick auf ein Wort = nachträglich überall schwärzen.
   Gelb gestrichelt = ungelesene Bereiche (Handschrift, Stempel): ansehen
   und bei Bedarf anklicken. „Vorschau Schwärzung“ zeigt das Ergebnis;
   dort einfach einen Bereich mit der Maus aufziehen, das Kürzel tippen
   und Enter drücken (Shift+Ziehen geht in jeder Ansicht).

5. „Geschwärzt speichern“: Die Stellen werden weiß überdeckt und mit dem
   Kürzel in schwarzer Schrift beschriftet. Das Original bleibt unverändert.

Datenschutz: Alles läuft auf diesem Rechner. Die KI startet mit dem
Programm und wird beim Schließen beendet."""
