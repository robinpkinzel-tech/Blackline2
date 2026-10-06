import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from blackline2.ai.client import AIClientError, ChatClient, extract_json
from blackline2.ai.detector import AIDetector
from blackline2.ai.server import AIServerError, LocalAIServer
from blackline2.analysis import Analyzer
from blackline2.detect_inputs import UserInputs
from blackline2.labels import PersonRegistry
from blackline2.model import Document
from blackline2.settings import Settings


def test_extract_json_variants():
    assert extract_json('{"funde": []}') == {"funde": []}
    assert extract_json('<think>hmm</think>\n```json\n{"funde": [1]}\n```') == {"funde": [1]}
    assert extract_json('Hier: {"funde": [2]} fertig') == {"funde": [2]}
    cut = '{"funde": [{"text": "Erika", "kategorie": "name", "bezug": ""}, {"text": "Mus'
    assert extract_json(cut) == {"funde": [{"text": "Erika", "kategorie": "name", "bezug": ""}]}


def test_server_lifecycle_and_detection(fake_server, page_factory):
    exe, model = fake_server
    srv = LocalAIServer(exe, model)
    srv.start()
    try:
        srv.wait_ready(30)
        pid = srv.proc.pid
        assert srv.health()
        if sys.platform == "win32":  # KI hängt am Job-Objekt (stirbt mit Blackline 2)
            assert srv._job is not None
            assert not any("Job-Objekt" in line for line in srv.log)
        # Server lauscht nur lokal und verlangt den Schlüssel
        bad = ChatClient(srv.base_url, api_key="falsch", timeout=10)
        with pytest.raises(AIClientError, match="401"):
            bad.chat_json("s", "u")

        page = page_factory("Sehr geehrte Frau Erika Mustermann,\nIhr Nachbar Herr Jens Beispiel und Herr Kinzel")
        reg = PersonRegistry()
        inputs = UserInputs(mandant_name="Robin Kinzel")
        det = AIDetector(ChatClient(srv.base_url, srv.api_key, timeout=30), reg, inputs)
        doc = Document(path=__import__("pathlib").Path("x.pdf"), pdf_bytes=b"", pages=[page])
        report = Analyzer(Settings(), inputs, reg, det).run([doc])
        labels = {(h.label, h.text) for h in doc.hits}
        assert report.ai_used and not report.ai_errors
        assert ("Person A", "Erika Mustermann") in labels
        assert ("Person B", "Jens Beispiel") in labels
        assert ("Mandant", "Kinzel") in labels
    finally:
        srv.stop()
    time.sleep(0.3)
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_ai_spread_to_other_pages(fake_server, page_factory):
    """Was die KI auf Seite 1 findet, wird auch auf Seite 2 geschwärzt."""
    exe, model = fake_server
    srv = LocalAIServer(exe, model)
    srv.start()
    try:
        srv.wait_ready(30)
        p1 = page_factory("Zeugin ist Frau Erika Mustermann.", 0)
        p2 = page_factory("Die Mustermann bestätigte. Erika war anwesend.", 1)
        reg = PersonRegistry()
        det = AIDetector(ChatClient(srv.base_url, srv.api_key, timeout=30), reg, UserInputs())
        doc = Document(path=__import__("pathlib").Path("x.pdf"), pdf_bytes=b"", pages=[p1, p2])
        Analyzer(Settings(), UserInputs(), reg, det).run([doc])
        page2 = {(h.label, h.text) for h in doc.hits if h.page == 1}
        assert ("Person A", "Mustermann") in page2
        assert ("Person A", "Erika") in page2
    finally:
        srv.stop()


def test_missing_files_raise(tmp_path):
    with pytest.raises(AIServerError):
        LocalAIServer(tmp_path / "nope", tmp_path / "nope.gguf").start()


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="Shell-Skript")
def test_server_crash_reported(tmp_path):
    exe = tmp_path / "llama-server"
    exe.write_text("#!/bin/sh\necho 'error: model kaputt'\nexit 1\n")
    exe.chmod(0o755)
    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    srv = LocalAIServer(exe, model)
    srv.start()
    with pytest.raises(AIServerError, match="unerwartet beendet"):
        srv.wait_ready(10)
    srv.stop()


class _HallucinatingDetector:
    """Liefert eine Person, die gar nicht im Text steht, und eine echte."""

    def analyze_page(self, page, page_no, total, doc_name=""):
        from blackline2.ai.detector import Finding
        return [Finding("Erika Mustermann", "name", "Erika Mustermann"),
                Finding("Jens Beispiel", "name", "Jens Beispiel")]


def test_hallucinated_person_gets_no_label(page_factory):
    page = page_factory("Herr Jens Beispiel war anwesend.")
    reg = PersonRegistry()
    doc = Document(path=__import__("pathlib").Path("x.pdf"), pdf_bytes=b"", pages=[page])
    Analyzer(Settings(), UserInputs(), reg, _HallucinatingDetector()).run([doc])
    assert {(h.label, h.text) for h in doc.hits} == {("Person A", "Jens Beispiel")}
    assert [p.display for p in reg.persons] == ["Jens Beispiel"]


def test_finding_filters():
    clean = AIDetector._clean
    assert clean({"text": "Herr Dr. Kinzel", "kategorie": "name", "bezug": "Mandant"}).text == "Kinzel"
    assert clean({"text": "Amtsgericht Limburg", "kategorie": "name", "bezug": ""}) is None
    assert clean({"text": "kläger", "kategorie": "name", "bezug": ""}) is None
    assert clean({"text": "der nachbar", "kategorie": "name", "bezug": ""}) is None
    assert clean({"text": "12345 Musterstadt", "kategorie": "adresse", "bezug": ""}).text == "12345 Musterstadt"
    assert clean({"text": "x", "kategorie": "quatsch", "bezug": ""}) is None
    assert clean({"text": "Personalnr. 4711", "kategorie": "quatsch", "bezug": ""}).kategorie == "sonstiges"


def test_server_dies_when_app_crashes(fake_server, tmp_path):
    """Wird Blackline 2 hart beendet (Absturz/Task-Manager), darf die KI nicht weiterlaufen."""
    exe, model = fake_server
    root = Path(__file__).resolve().parent.parent
    code = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        "from blackline2.ai.server import LocalAIServer\n"
        f"s = LocalAIServer({str(exe)!r}, {str(model)!r})\n"
        "s.start(); s.wait_ready(30)\n"
        "print(s.proc.pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    env = dict(os.environ, APPDATA=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path))
    app = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=env)
    try:
        pid = int(app.stdout.readline().strip())
        procs = [psutil.Process(pid)]
        time.sleep(1.0)
        procs += procs[0].children(recursive=True)
        app.kill()  # harter Absturz von "Blackline 2"
        app.wait(10)
        gone, alive = psutil.wait_procs(procs, timeout=15)
        alive = [p for p in alive if p.status() != psutil.STATUS_ZOMBIE]
        assert not alive, f"KI-Prozess läuft weiter: {alive}"
    finally:
        if app.poll() is None:
            app.kill()


class _ScriptedDetector:
    """Gibt vorgegebene KI-Funde zurück (simuliert typische Fehler kleiner Modelle)."""

    def __init__(self, findings):
        self.findings = findings

    def analyze_page(self, page, page_no, total, doc_name=""):
        from blackline2.ai.detector import AIDetector, Finding
        out = []
        for t, k, b in self.findings:
            f = AIDetector._clean({"text": t, "kategorie": k, "bezug": b})
            if f:
                out.append(Finding(f.text, f.kategorie, f.bezug))
        return out


def _run_scripted(page, findings, inputs=None):
    inputs = inputs or UserInputs()
    reg = PersonRegistry()
    doc = Document(path=Path("x.pdf"), pdf_bytes=b"", pages=[page])
    Analyzer(Settings(), inputs, reg, _ScriptedDetector(findings)).run([doc])
    return {(h.label, h.text) for h in doc.hits}, reg


def test_ai_wrong_party_assignment_is_corrected(page_factory):
    page = page_factory("Herr Robin Kinzel und die Zeugin Petra Musterfrau, Bahnhofstraße 7, 35578 Wetzlar")
    hits, reg = _run_scripted(page, [
        ("Petra Musterfrau", "name", "Mandant"),        # falsch zugeordnet
        ("Kinzel", "name", "Mandant"),                  # richtig
        ("Bahnhofstraße 7", "adresse", "Mandant"),      # passt nicht zur angegebenen Adresse
    ], UserInputs("Robin Kinzel", "Musterweg 15, 12345 Musterstadt"))
    assert ("Person A", "Petra Musterfrau") in hits
    assert ("Mandant", "Kinzel") in hits or ("Mandant", "Robin Kinzel") in hits
    assert ("Adresse", "Bahnhofstraße 7") in hits


def test_ai_party_without_user_input_is_not_guessed(page_factory):
    page = page_factory("Herr Jens Beispiel war da.")
    hits, _ = _run_scripted(page, [("Jens Beispiel", "name", "Gegner")])
    assert hits == {("Person A", "Jens Beispiel")}


def test_role_words_are_not_name_parts(page_factory):
    page = page_factory("Die Zeugin Petra Musterfrau sagte aus. Die Zeugin blieb.")
    hits, reg = _run_scripted(page, [("Zeugin Petra Musterfrau", "name", "Zeugin Petra Musterfrau")])
    assert ("Person A", "Petra Musterfrau") in hits
    assert not any(t == "Zeugin" for _l, t in hits)
    assert "zeugin" not in reg.persons[0].tokens


def test_ai_dates_need_birth_context(page_factory):
    page = page_factory("Frau Muster, geb. 05.05.1960, hat am 12.03.2024 nicht gezahlt. Termin 15.10.2024.")
    hits, _ = _run_scripted(page, [
        ("05.05.1960", "geburtsdatum", ""),
        ("12.03.2024", "geburtsdatum", ""),
        ("15.10.2024", "sonstiges", ""),
    ])
    texts = {t for _l, t in hits}
    assert "05.05.1960" in texts
    assert "12.03.2024" not in texts and "15.10.2024" not in texts
