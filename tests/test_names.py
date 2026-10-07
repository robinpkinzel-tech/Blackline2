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
    assert ("G.Y.", "Gül Yilmaz") in hits
    assert ("J.B.", "Jens Beispiel") in hits
    assert ("G.Y.", "Yilmaz") in hits


def test_professionals_can_be_excluded(page_factory):
    page = page_factory("Rechtsanwalt Hans Meier vertritt Herrn Jens Beispiel.")
    s = Settings()
    s.ki_exclude_professionals = True
    doc = Document(path=__import__("pathlib").Path("x.pdf"), pdf_bytes=b"", pages=[page])
    Analyzer(s, UserInputs(), PersonRegistry()).run([doc])
    assert {h.text for h in doc.hits} == {"Jens Beispiel"}


def test_gegner_organisation_is_not_redacted(page_factory):
    """Gegner ist Behörde/Firma: Name und Anschrift bleiben stehen, deren Mitarbeiter nicht."""
    from blackline2.ai.detector import Finding

    class _Det:
        def analyze_page(self, page, page_no, total, doc_name=""):
            return [Finding("Muster Wohnbau GmbH", "sonstiges", "Gegner"),
                    Finding("Muster Wohnbau", "name", "Gegner"),
                    Finding("Amtsweg 3", "adresse", "Gegner"),
                    Finding("Petra Schulze", "name", "Gegner")]

    text = ("Muster Wohnbau GmbH, Amtsweg 3, 12345 Musterstadt\nSehr geehrter Herr Robin Kinzel,\n"
            "Ihre Sachbearbeiterin Frau Petra Schulze. Die Muster Wohnbau GmbH lehnt ab.")
    inputs = UserInputs("Robin Kinzel", "", "Muster Wohnbau GmbH", "Amtsweg 3, 12345 Musterstadt",
                        gegner_organisation=True)
    doc = Document(path=__import__("pathlib").Path("x.pdf"), pdf_bytes=b"", pages=[page_factory(text)])
    report = Analyzer(Settings(), inputs, PersonRegistry(), _Det()).run([doc])
    hits = {(h.label, h.text) for h in doc.hits}
    texts = " ".join(t for _l, t in hits)
    assert "Wohnbau" not in texts and "Amtsweg" not in texts and "12345" not in texts
    assert ("Mandant", "Robin Kinzel") in hits
    assert ("P.S.", "Petra Schulze") in hits
    assert not any(label.startswith(("Gegner", "Adresse Gegner")) for label, _t in hits)
    assert report.skipped_organisation >= 2

    # ohne Häkchen wird der Gegner wie bisher geschwärzt
    inputs.gegner_organisation = False
    doc = Document(path=__import__("pathlib").Path("x.pdf"), pdf_bytes=b"", pages=[page_factory(text)])
    Analyzer(Settings(), inputs, PersonRegistry(), None).run([doc])
    hits = {(h.label, h.text) for h in doc.hits}
    assert ("Gegner", "Muster Wohnbau GmbH") in hits and ("Adresse Gegner", "Amtsweg 3") in hits
