"""Vorgang sichern und wieder laden (Funde, Eingaben, Personen).

Die Datei enthält Mandantendaten (Namen, Fundstellen). Sie wird nur auf
ausdrücklichen Wunsch geschrieben und sollte wie die Akte selbst behandelt werden.
Die Dokumente selbst werden NICHT gespeichert – beim Laden werden sie erneut
eingelesen (Texterkennung läuft dann noch einmal).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from blackline2 import __version__
from blackline2.analysis import assign_groups, merge_page_hits
from blackline2.detect_inputs import UserInputs
from blackline2.labels import Person, PersonRegistry
from blackline2.matching import PageIndex
from blackline2.model import Document, Hit, Segment

FORMAT = 1
SUFFIX = ".blackline2"


def _hit_to_dict(h: Hit) -> dict:
    return {
        "page": h.page,
        "segments": [[s.word, s.c0, s.c1] for s in h.segments],
        "text": h.text, "label": h.label, "category": h.category, "priority": h.priority,
        "rects": [list(r) for r in h.rects], "enabled": h.enabled, "question": h.question,
    }


def save_session(path: Path, docs: list[Document], inputs: UserInputs, registry: PersonRegistry) -> None:
    data = {
        "format": FORMAT,
        "programm": f"Blackline 2 {__version__}",
        "hinweis": "Enthält Mandantendaten – vertraulich behandeln.",
        "inputs": asdict(inputs),
        "persons": [{"label": p.label, "kind": p.kind, "names": p.names, "fixed": p.fixed}
                    for p in registry.all()],
        "docs": [{
            "path": str(d.path),
            "analyzed": d.analyzed,
            "pages": len(d.pages),
            "hits": [_hit_to_dict(h) for h in d.hits],
            "unread": {str(p.index): [list(r) for r in p.unread] for p in d.pages if p.unread},
        } for d in docs],
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def load_session(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("Diese Datei ist kein gültiger Blackline-2-Vorgang.")
    return data


def session_inputs(data: dict) -> UserInputs:
    raw = data.get("inputs", {})
    custom = [tuple(x) for x in raw.get("custom", []) if isinstance(x, (list, tuple)) and len(x) == 2]
    return UserInputs(raw.get("mandant_name", ""), raw.get("mandant_adresse", ""),
                      raw.get("gegner_name", ""), raw.get("gegner_adresse", ""), custom,
                      bool(raw.get("gegner_organisation", False)))


def session_paths(data: dict) -> list[Path]:
    return [Path(d["path"]) for d in data.get("docs", [])]


def restore_registry(data: dict, registry: PersonRegistry) -> None:
    persons = []
    for p in data.get("persons", []):
        kind = p.get("kind", "person")
        if kind == "mandant":
            for n in p.get("names", []):
                registry.mandant.add_name(n)
        elif kind == "gegner":
            for n in p.get("names", []):
                registry.gegner.add_name(n)
        else:
            person = Person(p.get("label", "Person ?"), "person", fixed=bool(p.get("fixed", False)))
            for n in p.get("names", []):
                person.add_name(n)
            persons.append(person)
    registry.restore(persons)


def apply_session_hits(data: dict, doc: Document) -> tuple[int, int]:
    """Gespeicherte Funde auf ein (neu geladenes) Dokument übertragen. -> (übernommen, verworfen)"""
    entry = next((d for d in data.get("docs", []) if Path(d["path"]) == doc.path), None)
    if entry is None:
        return 0, 0
    ok = dropped = 0
    by_page: dict[int, list[Hit]] = {}
    indexes: dict[int, PageIndex] = {}
    for raw in entry.get("hits", []):
        page_no = int(raw.get("page", -1))
        if not 0 <= page_no < len(doc.pages):
            dropped += 1
            continue
        page = doc.pages[page_no]
        segs = [Segment(int(w), c0, c1) for w, c0, c1 in raw.get("segments", [])]
        text = raw.get("text", "")
        if segs:
            valid = all(0 <= s.word < len(page.words) for s in segs)
            if not valid or " ".join(page.segment_text(segs).split()) != " ".join(text.split()):
                # Texterkennung hat sich geändert: Stelle über den Text wiederfinden
                idx = indexes.setdefault(page_no, PageIndex(page))
                found = idx.find(text, fuzzy=False) if text.strip() else []
                if not found:
                    dropped += 1
                    continue
                segs = page.trim_segments(found[0])
        hit = Hit(page=page_no, segments=segs, text=text, label=raw.get("label", "geschwärzt"),
                  category=raw.get("category", "benutzer"), priority=int(raw.get("priority", 0)),
                  rects=[tuple(r) for r in raw.get("rects", [])] if not segs else [],
                  enabled=bool(raw.get("enabled", True)), question=raw.get("question", ""))
        if not segs and not hit.rects:
            dropped += 1
            continue
        by_page.setdefault(page_no, []).append(hit)
        ok += 1
    hits: list[Hit] = []
    for page_no, items in by_page.items():
        hits += merge_page_hits(doc.pages[page_no], items)
    doc.hits = hits
    doc.analyzed = bool(entry.get("analyzed", True))
    for key, rects in entry.get("unread", {}).items():
        try:
            doc.pages[int(key)].unread = [tuple(r) for r in rects]
        except (ValueError, IndexError):
            pass
    assign_groups([doc])
    return ok, dropped
