"""Hauptfenster von Blackline 2."""

from __future__ import annotations

from pathlib import Path

import pymupdf
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter, QTabWidget,
                               QToolBar, QVBoxLayout, QWidget)

from blackline2 import APP_NAME, __version__, paths
from blackline2.ai.detector import AIDetector
from blackline2.analysis import Analyzer, AnalysisReport
from blackline2.export import ExportResult, export_document, output_path_for
from blackline2.gui import ai_manager as aim
from blackline2.gui.findings_panel import FindingsPanel
from blackline2.gui.input_panel import InputPanel
from blackline2.gui.page_view import RENDER_DPI, PageView
from blackline2.gui.settings_dialog import SettingsDialog
from blackline2.gui.workers import Worker
from blackline2.labels import PersonRegistry
from blackline2.loader import SUPPORTED_EXT, LoadError, load_document
from blackline2.model import PRIO_MANUAL, Document, Hit
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
        self.act_analyze = action("▶ Analysieren", self.analyze, "F5")
        self.act_cancel = action("⏹ Abbrechen", self.cancel_work)
        tb.addSeparator()
        self.act_preview = action("👁 Vorschau Schwärzung", self._refresh_view, "Ctrl+P", checkable=True)
        self.act_manual = action("▭ Bereich manuell schwärzen", self._toggle_manual, "Ctrl+M", checkable=True)
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
        self.ai_label.setText(f"● {message}")
        self.ai_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        self.ai_label.setToolTip("Die lokale KI läuft nur, solange Blackline 2 geöffnet ist.\n"
                                 "Klicken für Details.")

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
            info.append("<br><b>Einrichtung:</b> Im Programmordner ausführen:<br>"
                        "<code>python -m blackline2.setup_ki</code>")
        lbl = QLabel("<br>".join(info))
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(lbl)
        log = QPlainTextEdit(self.ai.log_text() or "(kein Protokoll)")
        log.setReadOnly(True)
        lay.addWidget(log, 1)
        row = QHBoxLayout()
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
        for d in loaded:
            self.docs.append(d)
            item = QListWidgetItem()
            self.doc_list.addItem(item)
        self._update_doc_list()
        if loaded:
            self.doc_list.setCurrentRow(len(self.docs) - len(loaded))
            ocr_pages = sum(1 for d in loaded for p in d.pages if p.source == "ocr")
            total = sum(len(d.pages) for d in loaded)
            self.status_msg.setText(f"{len(loaded)} Dokument(e) geladen, {total} Seiten "
                                    f"({ocr_pages} per Texterkennung gelesen).")
        if errors:
            QMessageBox.warning(self, "Nicht geladen", "\n\n".join(errors))
        self._update_actions()

    def _update_doc_list(self) -> None:
        for i, d in enumerate(self.docs):
            item = self.doc_list.item(i)
            state = ("✔ gespeichert" if d.exported_to else
                     f"{sum(h.enabled for h in d.hits)} Funde" if d.analyzed else "nicht analysiert")
            item.setText(f"{d.name}\n   {len(d.pages)} S. · {state}")
            item.setToolTip(str(d.path))

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
        self.view.show_page(pix, p.width, p.height, hits, self.act_preview.isChecked(), self.highlight_id)

    def _schedule_refresh(self) -> None:
        self._refresh_timer.start()

    def _refresh_all(self) -> None:
        self._refresh_view()
        if self.current:
            self.findings.set_hits(self.current.hits, self.current.analyzed)
        else:
            self.findings.set_hits([], False)
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

    def _toggle_hit(self, hit_id: int) -> None:
        if not self.current:
            return
        for h in self.current.hits:
            if h.id == hit_id:
                h.enabled = not h.enabled
        self._refresh_all()

    def _toggle_manual(self) -> None:
        self.view.set_manual_mode(self.act_manual.isChecked())

    def _manual_rect(self, rect: QRectF) -> None:
        if not self.current:
            return
        labels = sorted({h.label for d in self.docs for h in d.hits} | {"geschwärzt"})
        label, ok = QInputDialog.getItem(self, "Manuell schwärzen", "Kürzel für diesen Bereich:",
                                         labels, labels.index("geschwärzt"), True)
        if not ok or not label.strip():
            return
        r = (rect.left(), rect.top(), rect.right(), rect.bottom())
        self.current.hits.append(Hit(page=self.page_no, segments=[], text="(manueller Bereich)",
                                     label=label.strip(), category="manuell", priority=PRIO_MANUAL,
                                     rects=[r]))
        self._refresh_all()

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
        targets = [(d, output_path_for(d, folder_p, self.settings.export_suffix)) for d in docs]
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


HELP_TEXT = """So funktioniert Blackline 2:

1. Dokumente laden (PDF oder Bild) – per „Dateien öffnen“ oder Ziehen ins Fenster.
   Eingescannte Seiten werden automatisch per Texterkennung gelesen.

2. Angaben eintragen: Mandant, Gegner (Name + Adresse) und bis zu 5 freie
   Suchbegriffe mit gewünschtem Kürzel.

3. „Analysieren“: Die lokale KI liest jede Seite und findet Namen und
   persönliche Daten. Zusätzlich greifen feste Regeln für E-Mail, Telefon,
   IBAN, Versicherungs-/Rentennummern und Geburtsdaten.

4. Funde prüfen: Farbige Markierungen anklicken schaltet sie aus/ein.
   „Vorschau Schwärzung“ zeigt das Ergebnis. Fehlende Stellen mit
   „Bereich manuell schwärzen“ aufziehen.

5. „Geschwärzt speichern“: Die Stellen werden weiß überdeckt und mit dem
   Kürzel in schwarzer Schrift beschriftet. Das Original bleibt unverändert.

Datenschutz: Alles läuft auf diesem Rechner. Die KI startet mit dem
Programm und wird beim Schließen beendet."""
