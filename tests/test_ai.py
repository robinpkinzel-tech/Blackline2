import time

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
