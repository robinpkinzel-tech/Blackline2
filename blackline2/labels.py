"""Kürzel (Ersatztexte) und Personenverwaltung.

Jede erkannte Person bekommt ein festes Kürzel, das im ganzen Vorgang
(alle geladenen Dokumente) gleich bleibt: Mandant, Gegner, Person A, Person B …
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field

from blackline2.matching import NAME_STOP, norm, tokens_match

LABEL_MANDANT = "Mandant"
LABEL_GEGNER = "Gegner"
LABEL_ADR_MANDANT = "Adresse Mandant"
LABEL_ADR_GEGNER = "Adresse Gegner"

CATEGORY_LABELS = {
    "email": "E-Mail",
    "telefon": "Telefon",
    "handy": "Handy",
    "fax": "Fax",
    "iban": "IBAN",
    "konto": "Konto-Nr.",
    "sv": "SV-Nr.",
    "versicherung": "Vers.-Nr.",
    "geburtsdatum": "Geb.-Datum",
    "geburtsort": "Geb.-Ort",
    "geburtsname": "Geburtsname",
    "adresse": "Adresse",
    "steuer_id": "Steuer-ID",
    "ausweis": "Ausweis-Nr.",
    "kennzeichen": "Kfz-Kz.",
    "sonstiges": "geschwärzt",
}

SHORT_LABELS = {
    "Mandant": "Mdt.",
    "Gegner": "Ggn.",
    "Adresse Mandant": "Adr. Mdt.",
    "Adresse Gegner": "Adr. Ggn.",
    "Telefon": "Tel.",
    "Handy": "Tel.",
    "E-Mail": "Mail",
    "Geb.-Datum": "Geb.",
    "Geb.-Ort": "Ort",
    "Geburtsname": "Name",
    "Vers.-Nr.": "Nr.",
    "SV-Nr.": "SV",
    "Konto-Nr.": "Kto.",
    "Steuer-ID": "St-ID",
    "Ausweis-Nr.": "Ausw.",
    "Kfz-Kz.": "Kfz",
    "geschwärzt": "X",
    "Adresse": "Adr.",
}


def short_label(label: str) -> str:
    if label in SHORT_LABELS:
        return SHORT_LABELS[label]
    if label.startswith("Adresse Person "):
        return "Adr. " + label.rsplit(" ", 1)[-1]
    if label.startswith("Person "):
        return "P. " + label.rsplit(" ", 1)[-1]
    if "/" in label:
        return "/".join(short_label(p) for p in label.split("/"))
    words = label.split()
    if len(words) > 1:
        return "".join(w[0].upper() for w in words if w)
    return label[:4] + "." if len(label) > 5 else label


def name_tokens(name: str) -> list[str]:
    """Namensbestandteile ohne Titel/Anreden, normalisiert."""
    out = []
    for raw in name.replace(",", " ").split():
        t = norm(raw)
        if t and t not in NAME_STOP and not (len(t) == 1):
            out.append(t)
    return out


def _letters():
    for c in string.ascii_uppercase:
        yield c
    n = 27
    while True:
        yield str(n)
        n += 1


@dataclass
class Person:
    label: str
    kind: str  # mandant | gegner | person
    names: list[str] = field(default_factory=list)   # vollständige Schreibweisen
    tokens: set[str] = field(default_factory=set)

    def add_name(self, name: str) -> None:
        name = " ".join(name.split())
        if name and name not in self.names:
            self.names.append(name)
            self.tokens.update(name_tokens(name))

    @property
    def address_label(self) -> str:
        if self.kind == "mandant":
            return LABEL_ADR_MANDANT
        if self.kind == "gegner":
            return LABEL_ADR_GEGNER
        return "Adresse " + self.label

    @property
    def display(self) -> str:
        return self.names[0] if self.names else self.label


class PersonRegistry:
    def __init__(self) -> None:
        self.persons: list[Person] = []
        self._letters = _letters()
        self.mandant = Person(LABEL_MANDANT, "mandant")
        self.gegner = Person(LABEL_GEGNER, "gegner")

    def set_parties(self, mandant_names: list[str], gegner_names: list[str]) -> None:
        for n in mandant_names:
            self.mandant.add_name(n)
        for n in gegner_names:
            self.gegner.add_name(n)

    def all(self) -> list[Person]:
        out = []
        if self.mandant.names:
            out.append(self.mandant)
        if self.gegner.names:
            out.append(self.gegner)
        return out + self.persons

    @staticmethod
    def _overlap(a: set[str], b: list[str]) -> bool:
        return any(tokens_match(x, y) for x in b for y in a)

    def _candidates(self, toks: list[str]) -> list[Person]:
        found = []
        for p in self.all():
            if not p.tokens:
                continue
            # alle Bestandteile der Angabe müssen zur Person passen
            if all(any(tokens_match(t, pt) for pt in p.tokens) for t in toks):
                found.append(p)
        return found

    def resolve(self, ref: str, create: bool = True) -> Person | None:
        """Person zu einer Angabe der KI finden (oder neu anlegen)."""
        ref = " ".join((ref or "").split())
        low = ref.casefold()
        if low in ("mandant", "mandantin", "mandantschaft"):
            return self.mandant
        if low in ("gegner", "gegnerin", "gegenseite"):
            return self.gegner
        toks = name_tokens(ref)
        if not toks:
            return None
        cands = self._candidates(toks)
        if len(cands) == 1:
            cands[0].add_name(ref)
            return cands[0]
        if len(cands) > 1:
            # exakt gleiche Tokenmenge bevorzugen
            exact = [p for p in cands if set(toks) == p.tokens]
            return exact[0] if exact else cands[0]
        # umgekehrt: bekannte Person ist vollständig in der Angabe enthalten
        # ("Erika Maria Mustermann" zu bekannter "Erika Mustermann")
        wider = [p for p in self.all()
                 if len(p.tokens) >= 2 and all(any(tokens_match(pt, t) for t in toks) for pt in p.tokens)]
        if len(wider) == 1:
            wider[0].add_name(ref)
            return wider[0]
        if not create:
            return None
        p = Person(f"Person {next(self._letters)}", "person")
        p.add_name(ref)
        self.persons.append(p)
        return p

    def rename(self, old: str, new: str) -> None:
        for p in self.all():
            if p.label == old:
                p.label = new
