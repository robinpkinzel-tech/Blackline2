"""Datenmodell: Wörter mit Positionen, Seiten, Funde (Hits) und Dokumente.

Alle Koordinaten sind PDF-Punkte (1/72 Zoll) im sichtbaren Seitenraum,
Ursprung oben links – so wie die Seite am Bildschirm aussieht.
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass, field
from pathlib import Path

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1

# Quelle eines Fundes -> Priorität (kleiner = wichtiger, gewinnt bei Überschneidung)
PRIO_MANUAL = 0
PRIO_USER_TERM = 5
PRIO_INPUT = 10
PRIO_PATTERN = 20
PRIO_AI = 30
PRIO_INPUT_PART = 40
PRIO_AI_SPREAD = 50

SOURCE_NAMES = {
    PRIO_MANUAL: "Manuell",
    PRIO_USER_TERM: "Manuell (überall)",
    PRIO_INPUT: "Eingabe",
    PRIO_PATTERN: "Regel",
    PRIO_AI: "KI",
    PRIO_INPUT_PART: "Eingabe (Teil)",
    45: "Regel (Anrede)",
    PRIO_AI_SPREAD: "KI (übertragen)",
}


_NARROW = set(",.;:!|'`´il1Ijtf()[]/\\-")
_WIDE = set("mwMWÄÖÜ@%&QGOD")


def _char_weight(ch: str) -> float:
    if ch in _NARROW:
        return 0.45
    if ch in _WIDE:
        return 1.35
    if ch.isupper() or ch.isdigit():
        return 1.1
    return 1.0


def _partial_x(text: str, x0: float, x1: float, c0: int, c1: int) -> tuple[float, float]:
    """x-Bereich eines Teilworts anhand geschätzter Zeichenbreiten.

    Zur Sicherheit wird an Schnittkanten etwas großzügiger geschwärzt: lieber ein
    Satzzeichen mit überdecken als einen Buchstabenrest stehen lassen.
    """
    weights = [_char_weight(c) for c in text] or [1.0]
    total = sum(weights)
    unit = (x1 - x0) / total
    start = x0 + unit * sum(weights[:c0])
    end = x0 + unit * sum(weights[:c1])
    margin = unit * 0.45
    if c0 > 0:
        start -= margin
    if c1 < len(text):
        end += margin
    return max(x0, start), min(x1, end)


@dataclass
class Word:
    text: str
    bbox: Rect
    line: int  # laufende Zeilennummer auf der Seite


@dataclass
class Segment:
    """Ein (Teil-)Wort, das geschwärzt werden soll. c0/c1 = Zeichenbereich im Wort."""

    word: int
    c0: int | None = None
    c1: int | None = None

    def char_range(self, words: list[Word]) -> tuple[int, int]:
        n = len(words[self.word].text)
        return (self.c0 or 0, n if self.c1 is None else self.c1)


_hit_ids = itertools.count(1)


@dataclass
class Hit:
    page: int
    segments: list[Segment]
    text: str
    label: str
    category: str
    priority: int
    rects: list[Rect] = field(default_factory=list)
    enabled: bool = True
    question: str = ""   # Rückfrage der KI ("unsicher: nur dienstlich genannt?")
    group: str = ""      # gleiche Funde (Person bzw. gleicher Text) über alle Dokumente
    id: int = field(default_factory=lambda: next(_hit_ids))

    @property
    def source(self) -> str:
        return SOURCE_NAMES.get(self.priority, "?")


@dataclass
class PageData:
    index: int
    width: float
    height: float
    words: list[Word] = field(default_factory=list)
    source: str = "text"  # text (eingebetteter Text) | ocr
    unread: list[Rect] = field(default_factory=list)  # Tinte ohne erkannten Text (Handschrift, Stempel)
    _text: str | None = field(default=None, repr=False)
    _spans: list[tuple[int, int]] | None = field(default=None, repr=False)

    # ---------- Text mit Zeichen->Wort-Zuordnung ----------
    def _build(self) -> None:
        parts: list[str] = []
        spans: list[tuple[int, int]] = []
        pos = 0
        prev_line = None
        for w in self.words:
            if prev_line is not None:
                sep = "\n" if w.line != prev_line else " "
                parts.append(sep)
                pos += 1
            spans.append((pos, pos + len(w.text)))
            parts.append(w.text)
            pos += len(w.text)
            prev_line = w.line
        self._text = "".join(parts)
        self._spans = spans

    @property
    def text(self) -> str:
        if self._text is None:
            self._build()
        return self._text  # type: ignore[return-value]

    @property
    def spans(self) -> list[tuple[int, int]]:
        if self._spans is None:
            self._build()
        return self._spans  # type: ignore[return-value]

    def invalidate(self) -> None:
        self._text = None
        self._spans = None

    def segments_for_span(self, start: int, end: int) -> list[Segment]:
        """Zeichenbereich im Seitentext -> betroffene (Teil-)Wörter."""
        segs: list[Segment] = []
        for i, (s, e) in enumerate(self.spans):
            if e <= start or s >= end:
                continue
            c0 = max(start, s) - s
            c1 = min(end, e) - s
            if c0 == 0 and c1 == e - s:
                segs.append(Segment(i))
            else:
                segs.append(Segment(i, c0, c1))
        return segs

    def trim_segments(self, segments: list[Segment]) -> list[Segment]:
        """Satzzeichen am Anfang/Ende eines Treffers nicht mitschwärzen ("Kinzel," -> "Kinzel")."""
        if not segments:
            return segments
        segs = list(segments)
        edge = ".,;:!?()[]{}\"'„“”‚‘’»«<>"
        first, last = segs[0], segs[-1]
        w = self.words[first.word].text
        c0, c1 = first.char_range(self.words)
        while c0 < c1 and w[c0] in edge:
            c0 += 1
        segs[0] = Segment(first.word, c0 or None, None if c1 == len(w) else c1)
        last = segs[-1]
        w = self.words[last.word].text
        c0, c1 = last.char_range(self.words)
        while c1 > c0 and w[c1 - 1] in edge:
            c1 -= 1
        if c1 <= c0:
            return segments
        segs[-1] = Segment(last.word, c0 or None, None if c1 == len(w) else c1)
        return segs

    def segment_text(self, segments: list[Segment]) -> str:
        out: list[str] = []
        prev_line = None
        for seg in segments:
            w = self.words[seg.word]
            c0, c1 = seg.char_range(self.words)
            if prev_line is not None:
                out.append("\n" if w.line != prev_line else " ")
            out.append(w.text[c0:c1])
            prev_line = w.line
        return "".join(out)

    # ---------- Geometrie ----------
    def _line_height(self, line: int) -> float:
        hs = [w.bbox[3] - w.bbox[1] for w in self.words if w.line == line]
        return statistics.median(hs) if hs else 10.0

    def rects_for_segments(self, segments: list[Segment]) -> list[Rect]:
        """Je Zeile ein zusammenhängendes Rechteck (leicht vergrößert)."""
        by_line: dict[int, list[Rect]] = {}
        for seg in segments:
            w = self.words[seg.word]
            x0, y0, x1, y1 = w.bbox
            n = max(len(w.text), 1)
            c0, c1 = seg.char_range(self.words)
            if c0 > 0 or c1 < n:
                x0, x1 = _partial_x(w.text, x0, x1, c0, c1)
            by_line.setdefault(w.line, []).append((x0, y0, x1, y1))
        rects: list[Rect] = []
        for line, boxes in by_line.items():
            x0 = min(b[0] for b in boxes)
            y0 = min(b[1] for b in boxes)
            x1 = max(b[2] for b in boxes)
            y1 = max(b[3] for b in boxes)
            h = y1 - y0
            lh = self._line_height(line)
            if h < lh:  # gleichmäßige Höhe je Zeile
                grow = (lh - h) / 2
                y0, y1 = y0 - grow, y1 + grow
                h = lh
            pad_y = max(1.0, h * 0.12)
            pad_x = max(1.2, h * 0.08)
            rects.append((
                max(0.0, x0 - pad_x), max(0.0, y0 - pad_y),
                min(self.width, x1 + pad_x), min(self.height, y1 + pad_y),
            ))
        return rects


@dataclass
class Document:
    path: Path
    pdf_bytes: bytes  # normalisiertes PDF (Drehung entfernt, Bilder als PDF)
    pages: list[PageData] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)
    analyzed: bool = False
    exported_to: Path | None = None
    note: str = ""  # z. B. "umgewandelt über LibreOffice"

    @property
    def name(self) -> str:
        return self.path.name

    def hits_on(self, page: int) -> list[Hit]:
        return [h for h in self.hits if h.page == page]

    def word_at(self, page: int, x: float, y: float) -> int | None:
        """Index des Wortes an einer Seitenposition (oder None)."""
        for i, w in enumerate(self.pages[page].words):
            x0, y0, x1, y1 = w.bbox
            if x0 - 1 <= x <= x1 + 1 and y0 - 1 <= y <= y1 + 1:
                return i
        return None

    def words_in(self, page: int, rect: Rect) -> list[int]:
        """Indizes der Wörter, deren Mitte in einem Rechteck liegt."""
        rx0, ry0, rx1, ry1 = rect
        out = []
        for i, w in enumerate(self.pages[page].words):
            x0, y0, x1, y1 = w.bbox
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if rx0 <= cx <= rx1 and ry0 <= cy <= ry1:
                out.append(i)
        return out
