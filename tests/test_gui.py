"""Rauchtest der Oberfläche (ohne Bildschirm): laden, analysieren, schließen -> KI beendet."""

import sys
import time

import psutil
import pytest

pytest.importorskip("PySide6.QtWidgets")


def _wait(app, cond, timeout=60):
    end = time.time() + timeout
    while time.time() < end and not cond():
        app.processEvents()
        time.sleep(0.05)
    return cond()


def test_gui_flow(fake_server, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    try:
        app = QApplication.instance() or QApplication(sys.argv)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Qt nicht startbar: {exc}")
    from blackline2.gui.main_window import MainWindow
    from blackline2.settings import Settings

    import pymupdf

    pdf = tmp_path / "brief.pdf"
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((60, 80), "Sehr geehrte Frau Erika Mustermann, Tel. 06433/123456", fontsize=11)
    p.insert_text((60, 100), "Ihr Mandant Robin Kinzel", fontsize=11)
    d.save(pdf)

    exe, model = fake_server
    s = Settings()
    s.llama_server_path, s.model_path = str(exe), str(model)
    w = MainWindow(s)
    w.show()
    w.start_ai()
    assert _wait(app, lambda: w.ai.state == "ready"), w.ai.message
    pid = w.ai.server.proc.pid

    w.load_files([pdf])
    assert _wait(app, lambda: w.worker is None and w.docs)
    w.inputs.mandant_name.setText("Robin Kinzel")
    w.analyze()
    assert _wait(app, lambda: w.worker is None and w.docs[0].analyzed)
    labels = {h.label for h in w.docs[0].hits}
    assert {"Mandant", "E.M.", "Telefon"} <= labels

    # Klick auf Markierung schaltet um
    hit = w.docs[0].hits[0]
    w._toggle_hit(hit.id)
    assert hit.enabled is False

    w.close()
    _wait(app, lambda: False, 0.5)
    assert w.ai.server is None
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_gui_questions_and_everywhere(fake_server, tmp_path):
    """Rückfrage beantworten und Klick auf Markierung wirken auf alle gleichen Stellen in allen Dokumenten."""
    from PySide6.QtWidgets import QApplication

    try:
        app = QApplication.instance() or QApplication(sys.argv)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Qt nicht startbar: {exc}")
    import pymupdf

    from blackline2.gui.main_window import MainWindow
    from blackline2.settings import Settings

    files = []
    for name, text in (("a.pdf", "Herr Dr. Hans Meier und Frau Erika Mustermann.\nMustermann sagte zu."),
                       ("b.pdf", "Dr. Meier und Frau Mustermann erschienen.")):
        pdf = tmp_path / name
        d = pymupdf.open()
        p = d.new_page()
        for i, line in enumerate(text.split("\n")):
            p.insert_text((60, 80 + 20 * i), line, fontsize=11)
        d.save(pdf)
        files.append(pdf)

    exe, model = fake_server
    s = Settings()
    s.llama_server_path, s.model_path = str(exe), str(model)
    w = MainWindow(s)
    w.show()
    w.start_ai()
    assert _wait(app, lambda: w.ai.state == "ready"), w.ai.message
    w.load_files(files)
    assert _wait(app, lambda: w.worker is None and len(w.docs) == 2)
    w.inputs.mandant_name.setText("Robin Kinzel")  # sonst fragt ein Dialog nach
    w.analyze()
    assert _wait(app, lambda: w.worker is None and all(d.analyzed for d in w.docs))

    # Rückfrage der KI zu "Hans Meier" (Titel -> evtl. Berufsträger), über beide Dokumente
    meier = [h for d in w.docs for h in d.hits if "Meier" in h.text]
    assert meier and all(h.question for h in meier), [(h.text, h.question) for h in meier]
    assert w.findings.q_tree.topLevelItemCount() == 1
    w.findings.q_tree.setCurrentItem(w.findings.q_tree.topLevelItem(0))
    w.findings._decide(False)
    assert all(not h.enabled and not h.question for d in w.docs for h in d.hits if "Meier" in h.text)
    assert w.findings.q_tree.topLevelItemCount() == 0

    # Klick auf "Mustermann" in Dokument 1 schaltet alle Stellen in beiden Dokumenten aus
    target = next(h for h in w.docs[0].hits if h.text == "Erika Mustermann")
    w._toggle_hit(target.id, everywhere=True)
    muster = [h for d in w.docs for h in d.hits if "Mustermann" in h.text]
    assert len(muster) == 3 and all(not h.enabled for h in muster)
    # Strg+Klick: nur eine Stelle wieder an
    w._toggle_hit(target.id, everywhere=False)
    assert target.enabled and sum(h.enabled for h in muster) == 1

    # Begriff nachträglich überall schwärzen (ohne KI-Neulauf)
    from blackline2.analysis import apply_user_term
    assert apply_user_term(w.docs, "erschienen", "Verb") == 1

    # Vorgang sichern und wieder laden
    from blackline2 import session
    path = tmp_path / "v.blackline2"
    session.save_session(path, w.docs, w.inputs.inputs(), w.registry)
    data = session.load_session(path)
    assert len(session.session_paths(data)) == 2
    w.close()
    _wait(app, lambda: False, 0.5)
    assert w.ai.server is None


def test_gui_inline_label_editor(fake_server, tmp_path):
    """Bereich aufziehen -> Eingabefeld erscheint sofort -> Enter übernimmt das Kürzel."""
    from PySide6.QtCore import QRectF
    from PySide6.QtWidgets import QApplication

    try:
        app = QApplication.instance() or QApplication(sys.argv)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Qt nicht startbar: {exc}")
    import pymupdf

    from blackline2.gui.main_window import MainWindow
    from blackline2.settings import Settings

    pdf = tmp_path / "c.pdf"
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((60, 80), "Zeuge Jens Beispiel wohnt in Wetzlar.", fontsize=11)
    p.insert_text((60, 100), "Wetzlar ist schön.", fontsize=11)
    d.save(pdf)

    exe, model = fake_server
    s = Settings()
    s.llama_server_path, s.model_path = str(exe), str(model)
    s.ki_mode = "aus"
    w = MainWindow(s)
    w.show()
    w.load_files([pdf])
    assert _wait(app, lambda: w.worker is None and w.docs)
    doc = w.docs[0]

    # Rechteck um das Wort "Wetzlar" in Zeile 1 (Vorschau-Modus: Ziehen = Schwärzen)
    w.act_preview.setChecked(True)
    w._refresh_view()
    assert w.view.preview_mode
    idx = next(i for i, wd in enumerate(doc.pages[0].words) if wd.text.startswith("Wetzlar"))
    x0, y0, x1, y1 = doc.pages[0].words[idx].bbox
    rect = QRectF(x0 - 1, y0 - 1, x1 - x0 + 2, y1 - y0 + 2)
    w.view.rect_drawn.emit(rect)
    assert w.view.active_editor is not None, "Eingabefeld erschien nicht"
    assert w.view.active_everywhere is not None and w.view.active_everywhere.isVisible()

    w.view.active_editor.setText("Ort")
    w.view.active_editor.confirmed.emit()
    _wait(app, lambda: w.view.active_editor is None, 5)
    assert w.view.active_editor is None
    hits = {(h.text, h.label, h.priority) for h in doc.hits}
    assert ("Wetzlar", "Ort", 0) in hits            # der gezogene Bereich
    assert any(t == "Wetzlar" and l == "Ort" and pr == 5 for t, l, pr in hits), hits  # überall gefunden
    assert "Ort" in w.status_msg.text()

    # Esc bricht ab
    w.view.rect_drawn.emit(QRectF(10, 10, 50, 20))
    assert w.view.active_editor is not None
    w.view.active_editor.cancelled.emit()
    _wait(app, lambda: w.view.active_editor is None, 5)
    assert w.view.active_editor is None
    assert len([h for h in doc.hits if h.text == "(manueller Bereich)"]) == 0
    w.close()


def test_input_panel_gegner_organisation(tmp_path):
    from PySide6.QtWidgets import QApplication

    try:
        QApplication.instance() or QApplication(sys.argv)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Qt nicht startbar: {exc}")
    from blackline2 import session
    from blackline2.gui.input_panel import InputPanel
    from blackline2.labels import PersonRegistry
    from blackline2.settings import Settings

    panel = InputPanel(Settings())
    assert panel.inputs().gegner_organisation is False
    panel.gegner_name.setText("Jobcenter Musterstadt")
    panel.gegner_org.setChecked(True)
    ui = panel.inputs()
    assert ui.gegner_organisation and ui.gegner_names() == [] and "lesbar" in panel._box_g.title()
    # bleibt im gespeicherten Vorgang erhalten
    path = tmp_path / "v.blackline2"
    session.save_session(path, [], ui, PersonRegistry())
    restored = session.session_inputs(session.load_session(path))
    assert restored == ui
    panel.clear()
    assert not panel.gegner_org.isChecked()
    panel.set_inputs(restored)
    assert panel.gegner_org.isChecked()
