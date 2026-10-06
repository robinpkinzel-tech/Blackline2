"""Suchen von Begriffen in den erkannten Wörtern einer Seite.

Tolerant gegenüber typischen OCR-Problemen:
  * einzelne falsch erkannte Buchstaben (Kinzei statt Kinzel)
  * Silbentrennung am Zeilenende (Kin-\\nzel)
  * zerrissene oder zusammengeklebte Wörter (Kin zel / RobinKinzel)
  * Satzzeichen am Wortrand (Kinzel, / (Kinzel))
  * Genitiv-s (Kinzels)
  * Straße / Strasse / Str.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz.distance import Levenshtein

from blackline2.model import PageData, Segment

_EDGE = ".,;:!?()[]{}\"'„“”‚‘’»«<>*•·|"
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_STREET = re.compile(r"(stra(?:ß|ss)e|str)$")
_DIGIT_FIX = str.maketrans({"o": "0", "O": "0", "l": "1", "I": "1", "|": "1", "S": "5", "B": "8"})

# Wörter, die nie allein als Name übertragen werden
NAME_STOP = {
    "herr", "herrn", "frau", "dr", "prof", "dipl", "ing", "med", "jur", "rer", "nat", "phil",
    "von", "van", "vom", "zu", "der", "die", "das", "den", "dem", "de", "la", "le", "di", "da",
    "und", "geb", "verw", "gesch", "rechtsanwalt", "rechtsanwältin", "ra", "rain", "notar",
    "mandant", "mandantin", "gegner", "gegnerin", "kläger", "klägerin", "beklagte", "beklagter",
}


def norm(token: str, casefold: bool = True) -> str:
    s = unicodedata.normalize("NFKC", token).translate(_DASHES).strip()
    s = s.strip(_EDGE)
    if s.endswith("-") and len(s) > 1:
        s = s.rstrip("-")
    if casefold:
        s = s.casefold()
        s = _STREET.sub("str", s)
    return s


def tokenize(phrase: str) -> list[str]:
    toks = [norm(t) for t in re.split(r"[\s,;]+", phrase)]
    return [t for t in toks if t]


def _has_digit(s: str) -> bool:
    return any(c.isdigit() for c in s)


def tokens_match(query: str, word: str, fuzzy: bool = True, possessive: bool = False) -> bool:
    """query/word sind bereits normalisiert."""
    if not query or not word:
        return False
    if query == word:
        return True
    if possessive and (word == query + "s" or word in (query + "'s", query + "’s")):
        return True
    if not fuzzy:
        return False
    if _has_digit(query) or _has_digit(word):
        # Zahlen (Hausnr., PLZ) nur exakt – aber typische OCR-Verwechslungen erlauben
        return query.translate(_DIGIT_FIX) == word.translate(_DIGIT_FIX)
    longest = max(len(query), len(word))
    if longest < 5:
        return False
    allowed = 1 if longest < 9 else 2
    if possessive and word.endswith("s") and len(word) == len(query) + 1:
        word = word[:-1]
    return Levenshtein.distance(query, word, score_cutoff=allowed) <= allowed


class PageIndex:
    """Normalisierte Wörter einer Seite (einmal berechnet, oft durchsucht)."""

    def __init__(self, page: PageData):
        self.page = page
        self.norm = [norm(w.text) for w in page.words]

    def _consume(self, j: int, qtok: str, fuzzy: bool, possessive: bool) -> int:
        """Wie viele Wörter ab j bilden qtok? 0 = keine Übereinstimmung."""
        words = self.page.words
        if tokens_match(qtok, self.norm[j], fuzzy, possessive):
            return 1
        if j + 1 < len(words):
            raw = words[j].text.rstrip(_EDGE)
            nxt = words[j + 1].text
            # Silbentrennung am Zeilenende
            if raw.endswith(("-", "¬")) and words[j + 1].line != words[j].line:
                if tokens_match(qtok, norm(raw[:-1] + nxt), fuzzy, possessive):
                    return 2
            # zerrissenes Wort in derselben Zeile
            if words[j + 1].line == words[j].line and len(self.norm[j]) <= len(qtok):
                if tokens_match(qtok, norm(words[j].text + nxt), False, possessive):
                    return 2
        return 0

    def find(self, phrase: str | list[str], fuzzy: bool = True, possessive: bool = False,
             require_capital: bool = False) -> list[list[Segment]]:
        qtoks = tokenize(phrase) if isinstance(phrase, str) else phrase
        if not qtoks:
            return []
        words = self.page.words
        n = len(words)
        results: list[list[Segment]] = []
        i = 0
        while i < n:
            if not self.norm[i]:
                i += 1
                continue
            if require_capital:
                raw = words[i].text.lstrip(_EDGE)
                if not raw or not raw[0].isupper():
                    i += 1
                    continue
            used: list[int] = []
            j = i
            k = 0
            ok = True
            while k < len(qtoks):
                if j >= n:
                    ok = False
                    break
                if not self.norm[j] and used:  # reines Satzzeichen innerhalb des Treffers
                    used.append(j)
                    j += 1
                    continue
                last = k == len(qtoks) - 1
                c = self._consume(j, qtoks[k], fuzzy, possessive and last)
                if c:
                    used.extend(range(j, j + c))
                    j += c
                    k += 1
                    continue
                # zwei Suchwörter in einem erkannten Wort (RobinKinzel)
                if not last and tokens_match(qtoks[k] + qtoks[k + 1], self.norm[j], fuzzy, possessive):
                    used.append(j)
                    j += 1
                    k += 2
                    continue
                ok = False
                break
            if ok and used:
                while used and not self.norm[used[-1]]:
                    used.pop()
                results.append([Segment(w) for w in used])
                i = used[-1] + 1
            else:
                i += 1
        return results
