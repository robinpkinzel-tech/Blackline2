"""Regelbasierte Erkennung von Daten mit festem Aufbau.

E-Mail, Telefon/Handy/Fax, IBAN/Kontonummer, Sozialversicherungs- und
Versichertennummern sowie Geburtsdaten (geb. / geb. am / geboren am / *).

Bereits gefundene Stellen werden im Arbeitstext maskiert, damit z. B. Teile
einer IBAN nicht zusätzlich als Telefonnummer erkannt werden.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from blackline2.labels import CATEGORY_LABELS
from blackline2.model import PRIO_PATTERN, Hit, PageData

# ---------------------------------------------------------------- Muster

EMAIL = re.compile(
    r"[A-Za-z0-9._%+\-ÄÖÜäöü]+[ \t]?(?:@|\(at\)|\[at\]|©|®)[ \t]?"
    r"[A-Za-z0-9\-ÄÖÜäöü]+(?:\.[A-Za-z0-9\-ÄÖÜäöü]+)*\.[A-Za-z]{2,}"
)

IBAN = re.compile(r"\b([A-Z]{2}[ \t]?\d{2}(?:[ \t]?[A-Z0-9]){10,30})")
IBAN_LENGTHS = {
    "DE": 22, "AT": 20, "CH": 21, "LI": 21, "NL": 18, "BE": 16, "LU": 20, "FR": 27, "IT": 27,
    "ES": 24, "PT": 25, "PL": 28, "CZ": 24, "DK": 18, "SE": 24, "NO": 15, "FI": 18, "GB": 22,
    "IE": 22, "GR": 27, "HR": 21, "SI": 19, "SK": 24, "HU": 28, "RO": 24, "BG": 22, "TR": 26,
}

KONTO = re.compile(
    r"(?i:Konto(?:nummer|[ \t]?-?[ \t]?Nr\.?)|Kto\.?(?:[ \t]?-?[ \t]?Nr\.?)?)[ \t]*[:.]?[ \t]*"
    r"(\d(?:[ \t]?\d){4,14})\b"
)

SV_NUMBER = re.compile(
    r"\b(\d{2}[ \t]?\d{2}[ \t]?\d{2}[ \t]?\d{2}[ \t]?[A-Z][ \t]?\d{2}[ \t]?\d)\b"
)
KV_NUMBER = re.compile(r"\b([A-Z][ \t]?\d{9})\b")
VERS_CONTEXT = re.compile(
    r"(?i:Versicherten[ \t]?-?[ \t]?(?:nummer|nr\.?)|Krankenversicherten[ \t]?-?[ \t]?(?:nummer|nr\.?)"
    r"|Versicherungs(?:schein)?[ \t]?-?[ \t]?(?:nummer|nr\.?)|Vers\.?[ \t]?-?[ \t]?Nr\.?"
    r"|Renten(?:versicherungs)?[ \t]?-?[ \t]?(?:nummer|nr\.?|zeichen)|RV[ \t]?-?[ \t]?Nr\.?"
    r"|SV[ \t]?-?[ \t]?Nr\.?|Sozialversicherungs[ \t]?-?[ \t]?(?:nummer|nr\.?)"
    r"|Mitglieds[ \t]?-?[ \t]?(?:nummer|nr\.?)|Police(?:n)?[ \t]?-?[ \t]?(?:nummer|nr\.?))"
    r"[ \t]*(?::|\.|lautet)?[ \t]*([A-Z0-9][A-Z0-9 \t./\-]{3,30})"
)

_MONTHS = (r"(?:Januar|Jänner|Februar|März|Maerz|April|Mai|Juni|Juli|August|September|Oktober"
           r"|November|Dezember|Jan|Feb|Mär|Mrz|Apr|Jun|Jul|Aug|Sept?|Okt|Nov|Dez)")
DATE = (r"(?:\d{1,2}[ \t]?\.[ \t]?\d{1,2}[ \t]?\.[ \t]?(?:\d{4}|\d{2})(?!\d)"
        r"|\d{1,2}\.?[ \t]+" + _MONTHS + r"\.?[ \t]+\d{4}"
        r"|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})")
_PLACE = r"[A-ZÄÖÜ][\w\-]+(?:[ \t]+(?:am|an[ \t]der|bei|im|in[ \t]der)[ \t]+[A-ZÄÖÜ][\w\-]+)?"
BIRTH = re.compile(
    r"(?:(?i:\bgeb(?:oren)?\b\.?|\bGeburtsdatum\b|\bGeb\.?[ \t]?-?[ \t]?Datum\b|\bGeb\.?[ \t]?dat\.?)"
    r"|(?<![\w*])\*)"
    r"[ \t]*:?[ \t\n]*(?:(?:am|den)[ \t]+)?"
    r"(?:in[ \t]+(?P<place1>" + _PLACE + r")[ \t]+(?:am[ \t]+)?)?"
    r"(?P<date>" + DATE + r")"
    r"(?:[ \t]+in[ \t]+(?P<place2>" + _PLACE + r"))?"
)
BIRTH_NAME = re.compile(r"(?i:\bgeb\.|\bgeborene[r]?\b)[ \t]+(?P<name>[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?)\b")

PHONE = re.compile(
    r"(?<![\w/.,\-+])"
    r"(?:(?:\+|00)[ \t]?\d{2,3}(?:[ \t]?\(0\))?[ \t\-/]*\(?\d{2,5}\)?|\(?0\d{2,5}\)?)"
    r"(?:[ \t]*[/\-–][ \t]*|[ \t]+)?"
    r"\d{2,}(?:[ \t\-–]?\d+){0,5}"
    r"(?![\w])"
)
PHONE_SKIP_CONTEXT = re.compile(
    r"(?i)(az\.?|aktenzeichen|geschäftszeichen|gz\.?|zeichen|rechnung|kunden|vertrag|steuer|ust|"
    r"konto|kto|blz|iban|bic|vers|police|mitglied|schaden|bestell|auftrag|art\.?-?nr|"
    r"artikel|seite|blatt|bl\.|anlage|urteil|beschluss|ag\b|lg\b|olg\b)[^\n]{0,12}$"
)
PHONE_FAX = re.compile(r"(?i)fax[^\n]{0,15}$")
PHONE_MOBILE = re.compile(r"(?i)(mobil|handy|cell|funk)[^\n]{0,15}$")
PHONE_TEL = re.compile(r"(?i)(tel|fon|ruf|☎|phone|erreichbar|durchwahl)[^\n]{0,15}$")
MOBILE_PREFIX = re.compile(r"^(?:\+|00)?[ \t]?(?:49)?[ \t]?(?:\(0\))?[ \t]?0?1[567]\d")


# ---------------------------------------------------------------- Hilfen

def iban_valid(compact: str) -> bool:
    if len(compact) < 15 or not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    s = compact[4:] + compact[:4]
    try:
        num = "".join(str(int(c, 36)) for c in s)
        return int(num) % 97 == 1
    except ValueError:
        return False


def _digits(s: str) -> int:
    return sum(c.isdigit() for c in s)


class _Work:
    """Seitentext mit Maskierung bereits gefundener Bereiche."""

    def __init__(self, page: PageData):
        self.page = page
        self.text = list(page.text)

    def masked(self) -> str:
        return "".join(self.text)

    def free(self, start: int, end: int) -> bool:
        return "\x00" not in self.text[start:end]

    def take(self, start: int, end: int) -> None:
        for i in range(start, end):
            if self.text[i] not in "\n":
                self.text[i] = "\x00"


def _hit(work: _Work, start: int, end: int, label: str, category: str, hits: list[Hit]) -> None:
    # Leerraum am Rand abschneiden
    t = work.page.text
    while start < end and t[start] in " \t\n":
        start += 1
    while end > start and t[end - 1] in " \t\n":
        end -= 1
    if end <= start or not work.free(start, end):
        return
    segs = work.page.segments_for_span(start, end)
    if not segs:
        return
    work.take(start, end)
    hits.append(Hit(page=work.page.index, segments=segs, text=t[start:end],
                    label=label, category=category, priority=PRIO_PATTERN))


# ---------------------------------------------------------------- Detektoren

def _emails(work: _Work, hits: list[Hit]) -> None:
    for m in EMAIL.finditer(work.masked()):
        _hit(work, m.start(), m.end(), CATEGORY_LABELS["email"], "email", hits)


def _ibans(work: _Work, hits: list[Hit]) -> None:
    text = work.masked()
    for m in IBAN.finditer(text):
        raw = m.group(1)
        compact_chars = [(i, c) for i, c in enumerate(raw) if not c.isspace()]
        compact = "".join(c for _, c in compact_chars)
        country = compact[:2]
        accepted = 0
        if country in IBAN_LENGTHS:
            ln = IBAN_LENGTHS[country]
            if len(compact) >= ln:
                cand = compact[:ln]
                fixed = cand[:2] + cand[2:].replace("O", "0").replace("I", "1").replace("l", "1")
                ibanish = country != "DE" or fixed[2:].isdigit()
                if iban_valid(fixed) or (ibanish and re.search(r"(?i)iban[^\n]{0,8}$", text[:m.start()])):
                    accepted = ln
                elif country == "DE" and fixed[2:].isdigit():
                    accepted = ln  # Prüfziffer evtl. durch OCR-Fehler verfälscht – sicherheitshalber
        else:
            for ln in range(min(34, len(compact)), 14, -1):
                if iban_valid(compact[:ln]):
                    accepted = ln
                    break
        if not accepted:
            continue
        end_in_raw = compact_chars[accepted - 1][0] + 1
        _hit(work, m.start(1), m.start(1) + end_in_raw, CATEGORY_LABELS["iban"], "iban", hits)
    for m in KONTO.finditer(work.masked()):
        _hit(work, m.start(1), m.end(1), CATEGORY_LABELS["konto"], "iban", hits)


def _insurance(work: _Work, hits: list[Hit]) -> None:
    for m in VERS_CONTEXT.finditer(work.masked()):
        value = m.group(1)
        # nur Teile übernehmen, die Ziffern enthalten (keine nachfolgenden Wörter)
        parts = re.split(r"([ \t]+)", value)
        keep = ""
        for idx in range(0, len(parts), 2):
            tok = parts[idx]
            nxt = parts[idx + 2] if idx + 2 < len(parts) else ""
            if any(c.isdigit() for c in tok) or (len(tok) == 1 and tok.isalpha() and any(c.isdigit() for c in nxt)):
                keep += (parts[idx - 1] if idx else "") + tok
            else:
                break
        keep = keep.rstrip(" ./-")
        if _digits(keep) >= 4:
            keyword = m.group(0)[: m.start(1) - m.start()].casefold()
            sv = any(k in keyword for k in ("renten", "rv", "sv", "sozialvers"))
            label = CATEGORY_LABELS["sv" if sv else "versicherung"]
            _hit(work, m.start(1), m.start(1) + len(keep), label, "versicherung", hits)
    for m in SV_NUMBER.finditer(work.masked()):
        _hit(work, m.start(1), m.end(1), CATEGORY_LABELS["sv"], "versicherung", hits)
    for m in KV_NUMBER.finditer(work.masked()):
        _hit(work, m.start(1), m.end(1), CATEGORY_LABELS["versicherung"], "versicherung", hits)


def _birth(work: _Work, hits: list[Hit]) -> None:
    for m in BIRTH.finditer(work.masked()):
        _hit(work, m.start("date"), m.end("date"), CATEGORY_LABELS["geburtsdatum"], "geburtsdatum", hits)
        for g in ("place1", "place2"):
            if m.group(g):
                _hit(work, m.start(g), m.end(g), CATEGORY_LABELS["geburtsort"], "geburtsdatum", hits)


def _birth_names(work: _Work, hits: list[Hit]) -> None:
    for m in BIRTH_NAME.finditer(work.masked()):
        _hit(work, m.start("name"), m.end("name"), CATEGORY_LABELS["geburtsname"], "name", hits)


def _phones(work: _Work, hits: list[Hit]) -> None:
    text = work.masked()
    for m in PHONE.finditer(text):
        value = m.group(0)
        d = _digits(value)
        if d < 6 or d > 16:
            continue
        before = text[max(0, m.start() - 40):m.start()]
        line_before = before.rsplit("\n", 1)[-1]
        tel_context = PHONE_TEL.search(line_before) or PHONE_FAX.search(line_before) or PHONE_MOBILE.search(line_before)
        if not tel_context and PHONE_SKIP_CONTEXT.search(line_before):
            continue
        if not tel_context and d < 7:
            continue
        if PHONE_FAX.search(line_before):
            label = CATEGORY_LABELS["fax"]
        elif PHONE_MOBILE.search(line_before) or MOBILE_PREFIX.match(value):
            label = CATEGORY_LABELS["handy"]
        else:
            label = CATEGORY_LABELS["telefon"]
        _hit(work, m.start(), m.end(), label, "telefon", hits)


def detect_patterns(page: PageData, enabled: Iterable[str]) -> list[Hit]:
    enabled = set(enabled)
    work = _Work(page)
    hits: list[Hit] = []
    if "email" in enabled:
        _emails(work, hits)
    if "iban" in enabled:
        _ibans(work, hits)
    if "versicherung" in enabled:
        _insurance(work, hits)
    if "geburtsdatum" in enabled:
        _birth(work, hits)
    if "name" in enabled:
        _birth_names(work, hits)
    if "telefon" in enabled:
        _phones(work, hits)
    return hits
