"""Nachträgliche Entscheidungen: Gruppen, Rückfragen, Begriffe hinzufügen, Vorgang sichern/laden."""

from pathlib import Path

from blackline2 import session
from blackline2.analysis import (Analyzer, apply_user_term, assign_groups, group_members, merge_page_hits,
                                 set_group_enabled)
from blackline2.detect_inputs import UserInputs
from blackline2.labels import PersonRegistry
from blackline2.model import PRIO_MANUAL, PRIO_USER_TERM, Document, Hit, Segment
from blackline2.settings import Settings


def _docs(page_factory):
    d1 = Document(path=Path("a.pdf"), pdf_bytes=b"", pages=[
        page_factory("Herr Robin Kinzel und Frau Erika Mustermann.\nMustermann sagte zu.", 0),
        page_factory("Kinzel widersprach. Tel. 06433/123456", 1)])
    d2 = Document(path=Path("b.pdf"), pdf_bytes=b"", pages=[
        page_factory("Frau Mustermann erschien nicht. Tel. 06433/123456", 0)])
    return [d1, d2]


def test_groups_span_all_documents(page_factory):
    docs = _docs(page_factory)
    Analyzer(Settings(), UserInputs("Robin Kinzel"), PersonRegistry()).run(docs)
    groups = {h.group for d in docs for h in d.hits}
    assert "name|Mandant" in groups and "name|Person A" in groups
    phone = next(h for h in docs[0].hits if h.category == "telefon")
    members = group_members(docs, phone.group)
    assert len(members) == 2 and {d.name for d, _h in members} == {"a.pdf", "b.pdf"}
    n = set_group_enabled(docs, "name|Person A", False)
    assert n == 3
    assert all(not h.enabled for d in docs for h in d.hits if h.label == "Person A")
    assert all(h.enabled for d in docs for h in d.hits if h.label == "Mandant")


def test_apply_user_term_everywhere_without_ai(page_factory):
    docs = _docs(page_factory)
    Analyzer(Settings(), UserInputs(), PersonRegistry()).run(docs)
    assert not any(h.text == "Frau" for d in docs for h in d.hits)
    n = apply_user_term(docs, "Frau", "Anrede")
    assert n == 2
    hits = {(d.name, h.text, h.label, h.priority) for d in docs for h in d.hits if h.text == "Frau"}
    assert hits == {("a.pdf", "Frau", "Anrede", PRIO_USER_TERM), ("b.pdf", "Frau", "Anrede", PRIO_USER_TERM)}
    # eine Gruppe über beide Dokumente
    g = next(h.group for d in docs for h in d.hits if h.text == "Frau")
    assert len(group_members(docs, g)) == 2


def test_user_term_overrides_existing_hit(page_factory):
    docs = _docs(page_factory)
    Analyzer(Settings(), UserInputs("Robin Kinzel"), PersonRegistry()).run(docs)
    apply_user_term(docs, "Robin Kinzel", "Kläger")
    texts = [(h.text, h.label) for h in docs[0].hits if "Kinzel" in h.text]
    assert ("Robin Kinzel", "Kläger") in texts
    assert ("Robin Kinzel", "Mandant") not in texts  # keine doppelte Schwärzung derselben Stelle


def test_manual_rect_survives_reanalysis(page_factory):
    docs = _docs(page_factory)
    docs[0].hits.append(Hit(0, [], "(manueller Bereich)", "geschwärzt", "manuell", PRIO_MANUAL,
                            rects=[(10, 10, 50, 20)]))
    docs[0].hits.append(Hit(1, [Segment(0)], "Kinzel", "Zeuge", "benutzer", PRIO_MANUAL))
    Analyzer(Settings(), UserInputs("Robin Kinzel"), PersonRegistry()).run(docs)
    kinds = {(h.text, h.label) for h in docs[0].hits}
    assert ("(manueller Bereich)", "geschwärzt") in kinds
    assert ("Kinzel", "Zeuge") in kinds           # manuell gesetztes Kürzel gewinnt gegen die Eingabe


def test_merge_keeps_rect_hits(page_factory):
    page = page_factory("Robin Kinzel")
    rect = Hit(0, [], "x", "geschwärzt", "manuell", PRIO_MANUAL, rects=[(0, 0, 5, 5)])
    seg = Hit(0, [Segment(0)], "Robin", "Mandant", "name", 10)
    out = merge_page_hits(page, [rect, seg])
    assert rect in out and seg in out


def test_session_roundtrip(tmp_path, page_factory):
    docs = _docs(page_factory)
    reg = PersonRegistry()
    inputs = UserInputs("Robin Kinzel", "", "", "", [("Köln", "Ort")])
    Analyzer(Settings(), inputs, reg, None).run(docs)
    docs[0].hits[0].enabled = False
    docs[0].hits[0].question = "Berufsträger?"
    docs[0].pages[0].unread = [(1, 2, 3, 4)]
    path = tmp_path / "test.blackline2"
    session.save_session(path, docs, inputs, reg)

    data = session.load_session(path)
    assert session.session_inputs(data) == inputs
    assert [p.name for p in session.session_paths(data)] == ["a.pdf", "b.pdf"]
    reg2 = PersonRegistry()
    session.restore_registry(data, reg2)
    assert [p.label for p in reg2.persons] == ["Person A"]
    assert reg2.resolve("Jens Beispiel").label == "Person B"

    fresh = _docs(page_factory)  # "neu geladen"
    ok, dropped = session.apply_session_hits(data, fresh[0])
    assert dropped == 0 and ok == len(docs[0].hits)
    assert {(h.text, h.label, h.enabled) for h in fresh[0].hits} == {(h.text, h.label, h.enabled) for h in docs[0].hits}
    assert fresh[0].hits[0].question == "Berufsträger?"
    assert fresh[0].pages[0].unread == [(1, 2, 3, 4)]
    assert all(h.group for h in fresh[0].hits)


def test_session_relocates_changed_ocr(tmp_path, page_factory):
    """Hat sich die Texterkennung leicht verschoben, wird die Stelle über den Text wiedergefunden."""
    docs = _docs(page_factory)
    Analyzer(Settings(), UserInputs("Robin Kinzel"), PersonRegistry()).run(docs)
    path = tmp_path / "t.blackline2"
    session.save_session(path, docs, UserInputs(), PersonRegistry())
    shifted = Document(path=Path("a.pdf"), pdf_bytes=b"", pages=[
        page_factory("Betreff: Akte\nHerr Robin Kinzel und Frau Erika Mustermann.\nMustermann sagte zu.", 0),
        page_factory("Kinzel widersprach. Tel. 06433/123456", 1)])
    ok, dropped = session.apply_session_hits(session.load_session(path), shifted)
    assert dropped == 0
    texts = {h.text for h in shifted.hits}
    assert {"Robin Kinzel", "Kinzel"} <= texts
    for h in shifted.hits:
        assert shifted.pages[h.page].segment_text(h.segments) == h.text


def test_question_propagates_to_group(page_factory):
    docs = _docs(page_factory)
    Analyzer(Settings(), UserInputs(), PersonRegistry()).run(docs)
    target = next(h for d in docs for h in d.hits if h.label == "Person A")
    target.question = "Nur dienstlich genannt?"
    assign_groups(docs)
    assert all(h.question == "Nur dienstlich genannt?" for d in docs for h in d.hits if h.label == "Person A")
