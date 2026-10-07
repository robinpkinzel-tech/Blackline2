"""Kürzel (Ersatztexte) und Personenverwaltung.

Jede erkannte Person bekommt ein festes Kürzel, das im ganzen Vorgang
(alle geladenen Dokumente) gleich bleibt: Mandant, Gegner und für alle weiteren
Personen die Anfangsbuchstaben ("Robin Kinzel" -> "R.K.").

Während der Analyse heißen neue Personen vorläufig "Person A", "Person B" …;
am Ende vergibt `PersonRegistry.assign_initials` die endgültigen Kürzel.
"""

from __future__ import annotations

import re
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


_INITIALS_LABEL = re.compile(r"^(?:[^\W\d_]{1,4}\.-?)+(?: \(\d+\))?$")


def short_label(label: str) -> str:
    if label in SHORT_LABELS:
        return SHORT_LABELS[label]
    if _INITIALS_LABEL.match(label):
        return label
    if label.startswith("Adresse Person "):
        return "Adr. " + label.rsplit(" ", 1)[-1]
    if label.startswith("Person "):
        return "P. " + label.rsplit(" ", 1)[-1]
    if "/" in label:
        return "/".join(short_label(p) for p in label.split("/"))
    if label.startswith("Adresse ") and _INITIALS_LABEL.match(label[8:]):
        return "Adr. " + label[8:]
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


# kleingeschriebene Namenszusätze im Kürzel ("Ursula von der Leyen" -> "U.v.d.L.")
_PARTICLES = {"von", "van", "vom", "zu", "zum", "zur", "der", "den", "dem", "de", "del", "della", "la", "le",
              "di", "da", "du", "ten", "ter", "op"}
# Stufen zum Unterscheiden gleicher Anfangsbuchstaben: (Buchstaben Vorname, Buchstaben Nachname)
_LEVELS = [(1, 1), (1, 2), (2, 1), (2, 2), (1, 3), (3, 1)]


def _name_words(name: str) -> list[tuple[str, bool]]:
    """Namensbestandteile ohne Anrede/Titel/Rolle -> [(Wort, ist_Zusatz)]."""
    name = " ".join((name or "").split())
    if name.count(",") == 1:  # "Kinzel, Robin" -> "Robin Kinzel"
        last, first = (x.strip() for x in name.split(","))
        if first and last:
            name = f"{first} {last}"
    out: list[tuple[str, bool]] = []
    for raw in name.replace(",", " ").split():
        w = raw.strip(".,;:()[]\"'„“”‚‘’")
        low = norm(w)
        if not w or not w[0].isalpha():
            continue
        if low in _PARTICLES:
            out.append((w, True))
        elif low not in NAME_STOP:
            out.append((w, False))
    while out and out[-1][1]:  # Zusatz am Ende ohne Namen
        out.pop()
    return out


def _part_initial(word: str, n: int) -> str:
    """'Kinzel' -> 'K.' (n=1) / 'Ki.' (n=2); Doppelnamen: 'Schmidt-Weber' -> 'S.-W.'"""
    parts = [p for p in word.split("-") if p and p[0].isalpha()]
    out = []
    for p in parts:
        letters = "".join(c for c in p if c.isalpha())
        out.append(letters[0].upper() + letters[1:n].lower() + ".")
    return "-".join(out)


def initials(name: str, first_n: int = 1, last_n: int = 1) -> str:
    """'Robin Kinzel' -> 'R.K.'; mit first_n/last_n mehr Buchstaben zur Unterscheidung."""
    words = _name_words(name)
    main = [i for i, (_w, particle) in enumerate(words) if not particle]
    if not main:
        return ""
    first, last = main[0], main[-1]
    out = []
    for i, (w, particle) in enumerate(words):
        if particle:
            out.append(w[0].lower() + ".")
            continue
        n = 1
        if i == first:
            n = max(n, first_n)
        if i == last:
            n = max(n, last_n)
        out.append(_part_initial(w, n))
    return "".join(out)


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
    fixed: bool = False  # Kürzel vom Nutzer umbenannt -> nicht mehr automatisch ändern

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

    def full_name(self) -> str:
        """Ausführlichste Schreibweise (meiste Namensteile) – Grundlage für das Kürzel."""
        best, best_n = "", 0
        for n in self.names:
            k = sum(1 for w, particle in _name_words(n) if not particle and len(w.rstrip(".")) > 1)
            k = k * 10 + sum(1 for w, particle in _name_words(n) if not particle)
            if k > best_n:
                best, best_n = n, k
        return best


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

    def restore(self, persons: list[Person]) -> None:
        """Gespeicherte Personen übernehmen; Buchstaben für neue Personen danach fortsetzen."""
        self.persons = list(persons)
        used = {p.label for p in persons}
        self._letters = _letters()
        self._letters = (c for c in self._letters if f"Person {c}" not in used)

    def rename(self, old: str, new: str) -> None:
        for p in self.all():
            if p.label == old:
                p.label = new
                p.fixed = True

    def assign_initials(self, reserved: set[str] | None = None) -> dict[str, str]:
        """Weiteren Personen ihre Anfangsbuchstaben als Kürzel geben.

        Mandant und Gegner behalten ihr Kürzel, ebenso vom Nutzer umbenannte Personen.
        Gleiche Anfangsbuchstaben werden durch weitere Buchstaben unterschieden
        ("R.Ki." / "R.Kl."), notfalls durch eine Nummer ("H.M." / "H.M. (2)").
        Liefert {altes Kürzel: neues Kürzel} für alle geänderten Kürzel (inkl. "Adresse …").
        """
        taken = {self.mandant.label, self.gegner.label} | set(reserved or ())
        taken |= {p.label for p in self.persons if p.fixed}
        todo = [p for p in self.persons if not p.fixed and initials(p.full_name())]
        names = {id(p): p.full_name() for p in todo}
        groups: dict[str, list[Person]] = {}
        for p in todo:
            groups.setdefault(initials(names[id(p)]), []).append(p)
        new: dict[int, str] = {}
        used = set(taken)
        for base, group in groups.items():
            if len(group) == 1 and base not in used:
                new[id(group[0])] = base
                used.add(base)
                continue
            # gleiche Anfangsbuchstaben: erste Stufe, auf der sich alle unterscheiden
            for first_n, last_n in _LEVELS[1:]:
                labs = [initials(names[id(p)], first_n, last_n) for p in group]
                if len(set(labs)) == len(labs) and not used & set(labs):
                    break
            else:  # nicht unterscheidbar ("Hans"/"Hanna Müller") -> Nummer
                labs, n = [], 1
                for _p in group:
                    lab = base
                    while lab in used or lab in labs:
                        n += 1
                        lab = f"{base} ({n})"
                    labs.append(lab)
            for p, lab in zip(group, labs, strict=True):
                new[id(p)] = lab
                used.add(lab)
        mapping: dict[str, str] = {}
        for p in todo:
            if p.label != new[id(p)]:
                mapping[p.label] = new[id(p)]
                mapping["Adresse " + p.label] = "Adresse " + new[id(p)]
                p.label = new[id(p)]
        return mapping


def relabel(label: str, mapping: dict[str, str]) -> str:
    """Kürzel eines Fundes umschreiben (auch zusammengesetzte wie "R.K./H.K.")."""
    if not mapping:
        return label
    if label in mapping:
        return mapping[label]
    if "/" in label:
        return "/".join(mapping.get(p, p) for p in label.split("/"))
    return label
