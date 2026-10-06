"""Suche nach den vom Nutzer eingetragenen Angaben (Mandant, Gegner, freie Begriffe)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from blackline2.labels import LABEL_ADR_GEGNER, LABEL_ADR_MANDANT, PersonRegistry, name_tokens
from blackline2.matching import PageIndex, norm
from blackline2.model import PRIO_INPUT, PRIO_INPUT_PART, Hit, PageData, Segment

_INITIAL = re.compile(r"^[A-ZÄÖÜ]\.$")
# Firmen/Behörden: dort keine Einzelwörter übertragen ("Deutsche", "Bund" …)
_ORGANISATION = re.compile(
    r"(?i)\b(gmbh|mbh|ag|kg|ohg|gbr|ug|se|e\.\s?v\.?|co\.?|bank|sparkasse|versicherung\w*|"
    r"\w*versicherung|\w*kasse|stadt|gemeinde|landkreis|kreis|land|bund|amt|\w*amt|behörde|verein|verband|"
    r"stiftung|gesellschaft|holding|partner|kanzlei|klinik\w*|krankenhaus|jobcenter|agentur|"
    r"gericht|\w*gericht|ministerium|universität|schule)\b"
)


def is_organisation(name: str) -> bool:
    return bool(_ORGANISATION.search(name))


def split_values(text: str) -> list[str]:
    """Mehrere Angaben in einem Feld: durch ; oder Zeilenumbruch getrennt."""
    return [v.strip() for v in re.split(r"[;\n]+", text or "") if v.strip()]


@dataclass
class UserInputs:
    mandant_name: str = ""
    mandant_adresse: str = ""
    gegner_name: str = ""
    gegner_adresse: str = ""
    custom: list[tuple[str, str]] = field(default_factory=list)  # (Suchbegriff, Ersetzung)

    def custom_terms(self) -> list[tuple[str, str]]:
        out = []
        for term, label in self.custom:
            for t in split_values(term):
                out.append((t, (label or "").strip() or "geschwärzt"))
        return out

    def is_empty(self) -> bool:
        return not any([self.mandant_name.strip(), self.mandant_adresse.strip(), self.gegner_name.strip(),
                        self.gegner_adresse.strip(), self.custom_terms()])


def address_parts(address: str) -> list[str]:
    """'Musterweg 15, 12345 Musterstadt' -> ['Musterweg 15', '12345 Musterstadt']"""
    parts = []
    for value in split_values(address):
        for p in re.split(r",", value):
            p = p.strip()
            if len(p) >= 3:
                parts.append(p)
    return parts


def _extend_initial(page: PageData, segs: list[Segment], first_names: list[str]) -> list[Segment]:
    """'R. Kinzel': vorangestelltes Initial des Vornamens mit schwärzen."""
    first = segs[0].word
    if first == 0:
        return segs
    prev = page.words[first - 1]
    if prev.line == page.words[first].line and _INITIAL.match(prev.text):
        initial = prev.text[0].casefold()
        if any(fn and fn[0] == initial for fn in first_names):
            return [Segment(first - 1)] + segs
    return segs


def _name_hits(page: PageData, idx: PageIndex, names: list[str], label: str, fuzzy: bool,
               shared_tokens: dict[str, str]) -> list[Hit]:
    hits: list[Hit] = []
    for name in names:
        toks = name_tokens(name)
        single = len(name.split()) == 1
        for segs in idx.find(name, fuzzy=fuzzy, possessive=True, require_capital=single):
            segs = page.trim_segments(segs)
            hits.append(Hit(page.index, segs, page.segment_text(segs), label, "name", PRIO_INPUT))
        if len(toks) < 2 or is_organisation(name):
            continue
        first_names = toks[:-1]
        for tok in toks:
            if len(tok) < 3:
                continue
            tok_label = shared_tokens.get(tok, label)
            for segs in idx.find([tok], fuzzy=fuzzy and len(tok) >= 6, possessive=True, require_capital=True):
                segs = _extend_initial(page, segs, first_names)
                segs = page.trim_segments(segs)
                hits.append(Hit(page.index, segs, page.segment_text(segs), tok_label, "name", PRIO_INPUT_PART))
    return hits


def _shared_tokens(groups: list[tuple[str, list[str]]]) -> dict[str, str]:
    """Namensteile, die mehreren Personen gehören (z. B. gleicher Nachname)."""
    owners: dict[str, list[str]] = {}
    for label, names in groups:
        for n in names:
            for t in name_tokens(n):
                if label not in owners.setdefault(t, []):
                    owners[t].append(label)
    return {t: "/".join(labels) for t, labels in owners.items() if len(labels) > 1}


def detect_inputs(page: PageData, inputs: UserInputs, registry: PersonRegistry,
                  enabled: set[str], fuzzy: bool = True, idx: PageIndex | None = None) -> list[Hit]:
    idx = idx or PageIndex(page)
    hits: list[Hit] = []
    mandant = split_values(inputs.mandant_name)
    gegner = split_values(inputs.gegner_name)
    groups = [(registry.mandant.label, mandant), (registry.gegner.label, gegner)]
    shared = _shared_tokens(groups)

    if "name" in enabled:
        hits += _name_hits(page, idx, mandant, registry.mandant.label, fuzzy, shared)
        hits += _name_hits(page, idx, gegner, registry.gegner.label, fuzzy, shared)

    if "adresse" in enabled:
        for address, label in ((inputs.mandant_adresse, LABEL_ADR_MANDANT),
                               (inputs.gegner_adresse, LABEL_ADR_GEGNER)):
            for part in address_parts(address):
                for segs in idx.find(part, fuzzy=fuzzy):
                    segs = page.trim_segments(segs)
                    hits.append(Hit(page.index, segs, page.segment_text(segs), label, "adresse", PRIO_INPUT))

    for term, label in inputs.custom_terms():
        single = len(term.split()) == 1
        for segs in idx.find(term, fuzzy=fuzzy and len(norm(term)) >= 5, possessive=single):
            segs = page.trim_segments(segs)
            hits.append(Hit(page.index, segs, page.segment_text(segs), label, "benutzer", PRIO_INPUT))
    return hits
