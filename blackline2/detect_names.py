"""Sicherheitsnetz für Namen: Wörter nach Anreden und Rollen.

Auch die beste KI übersieht gelegentlich einen Namen. Nach "Herr", "Frau",
"Dr.", "Zeugin", "Nachbar" usw. steht aber fast immer ein Name. Diese Regel
läuft deshalb zusätzlich zur KI (und auch ohne KI).
"""

from __future__ import annotations

import re

from blackline2.labels import PersonRegistry
from blackline2.matching import NAME_STOP, norm
from blackline2.model import Hit, PageData

PRIO_SALUTATION = 45

_TRIGGERS = (
    r"Herrn?|Frau|Hr\.|Fr\.|Dr\.|Prof\.|"
    r"Zeug(?:e|in|en)|Nachbar(?:in|n)?|Sachverständige[rn]?|Gutachter(?:in)?|"
    r"Kläger(?:in)?|Beklagte[rn]?|Antragsteller(?:in)?|Antragsgegner(?:in)?|"
    r"Ehemann|Ehefrau|Lebensgefährt(?:e|in)|Sohn|Tochter|Vater|Mutter|Bruder|Schwester|"
    r"Mitarbeiter(?:in)?|Kollege|Kollegin|Geschäftsführer(?:in)?|Vermieter(?:in)?|Mieter(?:in)?|"
    r"Rechtsanwalt|Rechtsanwältin|RA|RAin|Notar(?:in)?|Richter(?:in)?(?:\s+am\s+\w+)?"
)
_WORD = r"[^\W\d_][^\W\d_'’]*(?:-[^\W\d_][^\W\d_'’]*)?"
_PATTERN = re.compile(
    rf"\b(?:(?:{_TRIGGERS})(?![^\W\d_])(?:[ \t]+|[ \t]*\n[ \t]*))+"
    rf"(?:(?:Dr|Prof|Dipl)\.(?:-\w+\.)?[ \t]+)*"
    rf"(?P<name>{_WORD}(?:[ \t]+(?:von|van|de|zu|vom)(?:[ \t]+der)?)?(?:[ \t]+{_WORD})?)"
)
# Typische Endungen deutscher Hauptwörter -> zweites Wort ist kein Nachname
_NOUN_END = re.compile(r"(ung|ungen|heit|keit|schaft|tion|ionen|ität|nis|nisse|tum|ment|ismus|ei|ik)$", re.I)
_NOT_NAMES = {
    "bescheid", "kenntnis", "auskunft", "recht", "klage", "schreiben", "antrag", "termin", "frist",
    "vollmacht", "post", "geld", "zeit", "stellung", "angaben", "unterlagen", "kosten", "schaden",
    "vorsitzende", "vorsitzender", "kollegin", "kollege", "rechtsanwältin", "rechtsanwalt", "dr", "prof",
    "ihr", "ihre", "ihren", "ihrem", "ihrer", "sie", "unser", "unsere", "mein", "meine", "der", "die", "das",
}


def _is_name_word(w: str) -> bool:
    return bool(w) and w[0].isupper() and norm(w) not in NAME_STOP and norm(w) not in _NOT_NAMES


_PARTICLES = ("von", "van", "de", "zu", "vom", "der")


_PROFESSIONAL = re.compile(r"(?i)rechtsanw|\bRA(?:in)?\b|notar|richter|sachverständig|gutachter")


def candidates(text: str) -> list[tuple[int, int, str, str]]:
    """(Start, Ende, Name, vorangestellte Anrede/Rolle) aller Namenskandidaten im Text."""
    out = []
    for m in _PATTERN.finditer(text):
        base = m.start("name")
        kept_end = None
        kept: list[str] = []
        for i, wm in enumerate(re.finditer(r"\S+", m.group("name"))):
            w = wm.group(0)
            if w in _PARTICLES:
                kept.append(w)
                continue
            if not _is_name_word(w.rstrip(".,;:")) or (i > 0 and len(w) > 5 and _NOUN_END.search(w)):
                break
            kept.append(w.rstrip(".,;:"))
            kept_end = base + wm.end()
        while kept and kept[-1] in _PARTICLES:
            kept.pop()
        if kept and kept_end is not None:
            out.append((base, kept_end, " ".join(kept), text[m.start():base]))
    return out


def detect_salutation_names(page: PageData, registry: PersonRegistry,
                            exclude_professionals: bool = False) -> list[Hit]:
    hits: list[Hit] = []
    for start, end, name, prefix in candidates(page.text):
        if exclude_professionals and _PROFESSIONAL.search(prefix):
            continue
        person = registry.resolve(name)
        if person is None:
            continue
        segs = page.trim_segments(page.segments_for_span(start, end))
        if segs:
            hits.append(Hit(page.index, segs, page.segment_text(segs), person.label, "name", PRIO_SALUTATION))
    return hits
