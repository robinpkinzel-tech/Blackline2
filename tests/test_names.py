from blackline2.analysis import Analyzer
from blackline2.detect_inputs import UserInputs
from blackline2.detect_names import candidates
from blackline2.labels import PersonRegistry
from blackline2.model import Document
from blackline2.settings import Settings


def names(text):
    return [c[2] for c in candidates(text)]


def test_salutation_candidates():
    assert names("Ihre Nachbarin Frau Gül Yilmaz kann dies bezeugen.") == ["Gül Yilmaz"]
    assert names("Herrn\nRobin Kinzel\nMusterweg 15") == ["Robin Kinzel"]
    assert names("dass Herr Schneider Zahlungen verweigert") == ["Schneider"]
    assert names("Herr Dr. Hans-Peter von der Heide sagte") == ["Hans-Peter von der Heide"]
    assert names("Sehr geehrte Frau Vorsitzende,") == []
    assert names("Sehr geehrte Damen und Herren,") == []
    assert names("Die Frauen und Herrlichkeiten") == []


def test_safety_net_without_ai(page_factory):
    """Auch ganz ohne KI werden Namen nach Anreden geschwärzt – konsistent je Person."""
    page = page_factory("Sehr geehrter Herr Kinzel,\nIhre Nachbarin Frau Gül Yilmaz und Herr Jens Beispiel.\n"
                        "Frau Yilmaz bestätigte.")
    doc = Document(path=__import__("pathlib").Path("x.pdf"), pdf_bytes=b"", pages=[page])
    Analyzer(Settings(), UserInputs("Robin Kinzel"), PersonRegistry()).run([doc])
    hits = {(h.label, h.text) for h in doc.hits}
    assert ("Mandant", "Kinzel") in hits
    assert ("Person A", "Gül Yilmaz") in hits
    assert ("Person B", "Jens Beispiel") in hits
    assert ("Person A", "Yilmaz") in hits


def test_professionals_can_be_excluded(page_factory):
    page = page_factory("Rechtsanwalt Hans Meier vertritt Herrn Jens Beispiel.")
    s = Settings()
    s.ki_exclude_professionals = True
    doc = Document(path=__import__("pathlib").Path("x.pdf"), pdf_bytes=b"", pages=[page])
    Analyzer(s, UserInputs(), PersonRegistry()).run([doc])
    assert {h.text for h in doc.hits} == {"Jens Beispiel"}
