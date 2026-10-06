"""Gesamtablauf der Erkennung über alle geladenen Dokumente.

Reihenfolge / Vorrang bei Überschneidungen:
  1. Manuell gezogene Bereiche
  2. Eingaben (Mandant, Gegner, Adressen, freie Begriffe)
  3. Regeln (E-Mail, Telefon, IBAN, Versicherungsnr., Geburtsdatum)
  4. KI-Funde auf der jeweiligen Seite
  5. Teile der Eingaben (nur Nachname, nur Vorname …)
  6. KI-Funde, übertragen auf alle anderen Seiten/Dokumente
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from blackline2.ai.client import AIClientError
from blackline2.ai.detector import SETTINGS_CATEGORY, AIDetector, Finding
from blackline2.detect_inputs import UserInputs, detect_inputs, is_organisation, split_values
from blackline2.detect_patterns import MOBILE_PREFIX, detect_patterns
from blackline2.labels import CATEGORY_LABELS, PersonRegistry, name_tokens
from blackline2.loader import Cancelled
from blackline2.matching import PageIndex, norm
from blackline2.model import (PRIO_AI, PRIO_AI_SPREAD, PRIO_INPUT_PART, PRIO_MANUAL, Document, Hit, PageData,
                              Segment)
from blackline2.settings import Settings

ProgressFn = Callable[[int, int, str], None]


@dataclass
class AnalysisReport:
    ai_used: bool = False
    ai_errors: list[str] = field(default_factory=list)
    hit_count: int = 0


def _label_for_finding(f: Finding, registry: PersonRegistry, inputs: UserInputs) -> tuple[str, str] | None:
    """-> (Kürzel, Kategorie) oder None."""
    k = f.kategorie
    if k == "name":
        person = registry.resolve(f.bezug or f.text) or registry.resolve(f.text)
        if person is None:
            return None
        # Namensangabe ist Teil des Personennamens -> Schreibweise merken
        if person.kind == "person" and not is_organisation(f.text):
            person.add_name(f.text)
        return person.label, "name"
    if k == "adresse":
        person = registry.resolve(f.bezug, create=False) if f.bezug else None
        return (person.address_label if person else CATEGORY_LABELS["adresse"]), "adresse"
    if k == "benutzer":
        terms = inputs.custom_terms()
        for term, label in terms:
            if norm(term) == norm(f.bezug) or norm(term) == norm(f.text):
                return label, "benutzer"
        if len(terms) == 1:
            return terms[0][1], "benutzer"
        return None
    if k == "telefon":
        label = CATEGORY_LABELS["handy"] if MOBILE_PREFIX.match(f.text) else CATEGORY_LABELS["telefon"]
        return label, "telefon"
    simple = {
        "geburtsdatum": "geburtsdatum", "geburtsort": "geburtsort", "email": "email", "iban": "iban",
        "versicherungsnummer": "versicherung", "steuer_id": "steuer_id", "ausweisnummer": "ausweis",
        "kennzeichen": "kennzeichen", "sonstiges": "sonstiges",
    }
    key = simple.get(k, "sonstiges")
    return CATEGORY_LABELS[key], SETTINGS_CATEGORY.get(k, "sonstiges")


def _subtract(c0: int, c1: int, taken: list[tuple[int, int]]) -> list[tuple[int, int]]:
    parts = [(c0, c1)]
    for a, b in taken:
        nxt = []
        for x, y in parts:
            if b <= x or a >= y:
                nxt.append((x, y))
                continue
            if x < a:
                nxt.append((x, a))
            if b < y:
                nxt.append((b, y))
        parts = nxt
    return parts


def resolve_overlaps(page: PageData, hits: list[Hit]) -> list[Hit]:
    """Jede Textstelle bekommt genau ein Kürzel (das mit dem höchsten Vorrang)."""
    hits = sorted(hits, key=lambda h: (h.priority, -len(h.segments)))
    taken: dict[int, list[tuple[int, int]]] = {}
    out: list[Hit] = []
    for h in hits:
        keep: list[Segment] = []
        for seg in h.segments:
            c0, c1 = seg.char_range(page.words)
            wlen = len(page.words[seg.word].text)
            for a, b in _subtract(c0, c1, taken.get(seg.word, [])):
                if not page.words[seg.word].text[a:b].strip(" .,;:"):
                    continue
                keep.append(Segment(seg.word, a or None, None if b == wlen else b))
        if not keep:
            continue
        for seg in keep:
            taken.setdefault(seg.word, []).append(seg.char_range(page.words))
        if len(keep) != len(h.segments) or any(
                (k.c0, k.c1) != (s.c0, s.c1) for k, s in zip(keep, h.segments, strict=False)):
            h.segments = keep
            h.text = page.segment_text(keep)
        out.append(h)
    for h in out:
        h.rects = page.rects_for_segments(h.segments)
    out.sort(key=lambda h: (h.segments[0].word, h.segments[0].c0 or 0))
    return out


class Analyzer:
    def __init__(self, settings: Settings, inputs: UserInputs, registry: PersonRegistry,
                 detector: AIDetector | None = None) -> None:
        self.settings = settings
        self.inputs = inputs
        self.registry = registry
        self.detector = detector
        self.enabled = {k for k, v in settings.categories.items() if v}

    def run(self, docs: list[Document], progress: ProgressFn | None = None,
            cancel: threading.Event | None = None) -> AnalysisReport:
        report = AnalysisReport(ai_used=self.detector is not None)
        self.registry.set_parties(split_values(self.inputs.mandant_name), split_values(self.inputs.gegner_name))
        pages = [(d, p) for d in docs for p in d.pages]
        total = len(pages) * (2 if self.detector else 1)
        step = 0
        indexes = {(id(d), p.index): PageIndex(p) for d, p in pages}
        new_hits: dict[tuple[int, int], list[Hit]] = {key: [] for key in indexes}

        def tick(msg: str) -> None:
            nonlocal step
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            step += 1
            if progress:
                progress(step, total, msg)

        # 1) Eingaben + Regeln
        for d, p in pages:
            key = (id(d), p.index)
            new_hits[key] += detect_inputs(p, self.inputs, self.registry, self.enabled,
                                           self.settings.fuzzy_matching, indexes[key])
            new_hits[key] += detect_patterns(p, self.enabled)
            tick(f"{d.name}: Regeln, Seite {p.index + 1}")

        # 2) KI je Seite
        spread: list[tuple[Finding, str, str]] = []  # (Fund, Kürzel, Kategorie)
        if self.detector is not None:
            for d, p in pages:
                key = (id(d), p.index)
                tick(f"{d.name}: KI prüft Seite {p.index + 1} von {len(d.pages)} …")
                try:
                    findings = self.detector.analyze_page(p, p.index + 1, len(d.pages), d.name)
                except AIClientError as exc:
                    report.ai_errors.append(f"{d.name}, Seite {p.index + 1}: {exc}")
                    continue
                for f in findings:
                    cat0 = SETTINGS_CATEGORY.get(f.kategorie, "sonstiges")
                    if cat0 != "benutzer" and cat0 not in self.enabled:
                        continue
                    # erst prüfen, ob die Stelle wirklich im Text steht (keine erfundenen Personen anlegen)
                    found = indexes[key].find(f.text, fuzzy=self.settings.fuzzy_matching,
                                              possessive=(f.kategorie == "name"))
                    if not found and f.kategorie == "name":
                        # z. B. anders umbrochen: einzelne Namensteile suchen
                        for tok in name_tokens(f.text):
                            if len(tok) >= 3:
                                found += indexes[key].find([tok], fuzzy=len(tok) >= 6,
                                                           possessive=True, require_capital=True)
                    if not found:
                        continue
                    lab = _label_for_finding(f, self.registry, self.inputs)
                    if lab is None:
                        continue
                    label, cat = lab
                    for segs in found:
                        segs = p.trim_segments(segs)
                        new_hits[key].append(Hit(p.index, segs, p.segment_text(segs), label, cat, PRIO_AI))
                    spread.append((f, label, cat))

        # 3) KI-Funde auf alle Seiten übertragen (die KI übersieht mal eine Stelle)
        if spread:
            self._spread(pages, indexes, new_hits, spread)

        # 4) Namensteile, die mehreren Personen gehören (gleicher Nachname) kennzeichnen
        self._mark_shared_tokens(new_hits)

        # 5) Zusammenführen
        for d in docs:
            manual = [h for h in d.hits if h.priority == PRIO_MANUAL]
            hits: list[Hit] = []
            for p in d.pages:
                hits += resolve_overlaps(p, new_hits[(id(d), p.index)])
            d.hits = manual + hits
            d.analyzed = True
            report.hit_count += len(d.hits)
        return report

    def _mark_shared_tokens(self, new_hits: dict[tuple[int, int], list[Hit]]) -> None:
        owners: dict[str, list[str]] = {}
        for person in self.registry.all():
            if any(is_organisation(n) for n in person.names):
                continue
            for tok in person.tokens:
                if person.label not in owners.setdefault(tok, []):
                    owners[tok].append(person.label)
        shared = {t: "/".join(labels) for t, labels in owners.items() if len(labels) > 1}
        if not shared:
            return
        for hits in new_hits.values():
            for h in hits:
                if h.category != "name" or h.priority not in (PRIO_INPUT_PART, PRIO_AI_SPREAD):
                    continue
                t = norm(h.text.split()[-1]) if h.text.split() else ""
                for cand in (t, t[:-1] if t.endswith("s") else t):
                    if cand in shared:
                        h.label = shared[cand]
                        break

    def _spread(self, pages, indexes, new_hits, spread) -> None:
        phrases: dict[tuple[str, str], tuple[str, str]] = {}
        token_owner: dict[str, set[str]] = {}
        for f, label, cat in spread:
            phrases.setdefault((norm(f.text), label), (f.text, cat))
        # Namensteile aller von der KI gefundenen Personen
        for person in self.registry.persons:
            if any(is_organisation(n) for n in person.names):
                continue
            for tok in person.tokens:
                if len(tok) >= 3:
                    token_owner.setdefault(tok, set()).add(person.label)
        for d, p in pages:
            key = (id(d), p.index)
            idx = indexes[key]
            for (_n, label), (text, cat) in phrases.items():
                for segs in idx.find(text, fuzzy=self.settings.fuzzy_matching, possessive=(cat == "name")):
                    segs = p.trim_segments(segs)
                    new_hits[key].append(Hit(p.index, segs, p.segment_text(segs), label, cat, PRIO_AI_SPREAD))
            if "name" in self.enabled:
                for tok, owners in token_owner.items():
                    label = "/".join(sorted(owners))
                    for segs in idx.find([tok], fuzzy=len(tok) >= 6, possessive=True, require_capital=True):
                        segs = p.trim_segments(segs)
                        new_hits[key].append(Hit(p.index, segs, p.segment_text(segs), label, "name",
                                                 PRIO_AI_SPREAD))
