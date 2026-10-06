"""Texterkennung (OCR) für eingescannte Seiten.

Verwendet die in PyMuPDF/MuPDF eingebaute Tesseract-Engine. Es muss also
kein separates Tesseract-Programm installiert sein – nur die Sprachdateien
(deu.traineddata usw.) in einem tessdata-Ordner.

Ablauf je Seite:
  1. Seite in Graustufen mit hoher Auflösung (Standard 300 dpi) rendern
  2. Ausrichtung prüfen (90°/180°/270° gedrehte Scans erkennen)
  3. Schräglage messen und begradigen (Deskew)
  4. Ungleichmäßige Ausleuchtung ausgleichen (z. B. Handyfotos, Grauschleier)
  5. OCR, Wortpositionen zurück auf die Originalseite umrechnen
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np
import pymupdf
from PIL import Image, ImageFilter

# Rohergebnis: (text, (x0, y0, x1, y1) in PDF-Punkten, zeilennummer)
RawWord = tuple[str, tuple[float, float, float, float], int]

_GOOD_WORD = re.compile(r"^[A-Za-zÄÖÜäöüß][a-zäöüß]{2,}[.,;:!?)]?$")


@dataclass
class OcrOptions:
    tessdata: str
    languages: str = "deu+eng"
    dpi: int = 300
    deskew: bool = True
    orientation: bool = True
    recover: bool = True  # übersprungene Textbereiche erneut lesen


# ---------------------------------------------------------------- Bildvorverarbeitung

def render_gray(page: pymupdf.Page, dpi: int) -> Image.Image:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def estimate_skew(img: Image.Image, max_angle: float = 6.0) -> float:
    """Schräglage per Projektionsprofil schätzen (Grad, gegen den Uhrzeigersinn positiv)."""
    small = img.copy()
    small.thumbnail((1000, 1000))
    arr = np.asarray(small, dtype=np.uint8)
    ink = (arr < 160)
    if ink.mean() < 0.002:  # (fast) leere Seite
        return 0.0
    ink_img = Image.fromarray((ink * 255).astype(np.uint8))

    def score(angle: float) -> float:
        rot = np.asarray(ink_img.rotate(angle, resample=Image.NEAREST, fillcolor=0), dtype=np.float32)
        rows = rot.sum(axis=1)
        return float(np.var(rows))

    best = 0.0
    best_score = score(0.0)
    step = 0.5
    for a in np.arange(-max_angle, max_angle + 1e-6, step):
        s = score(float(a))
        if s > best_score:
            best, best_score = float(a), s
    for a in np.arange(best - step, best + step + 1e-6, 0.1):
        s = score(float(a))
        if s > best_score:
            best, best_score = float(a), s
    return round(best, 2)


def _strengthen_faint_text(arr: np.ndarray) -> Image.Image:
    """Blasse Schrift (z. B. Kopie einer Kopie) kräftiger machen.

    Bewusst NICHT über ein Histogramm der ganzen Seite: Auf textarmen Seiten
    würde das Kompressionsrauschen schwarz und die Schrift unleserlich.
    """
    dark = arr[arr < 170]
    if dark.size < 100:
        return Image.fromarray(arr)
    lo = float(np.percentile(dark, 5))
    if lo <= 50:  # Schrift ist bereits kräftig
        return Image.fromarray(arr)
    scale = 255.0 / (255.0 - lo)
    out = np.clip((arr.astype(np.float32) - lo) * scale, 0, 255).astype(np.uint8)
    return Image.fromarray(out)


def normalize_background(img: Image.Image) -> Image.Image:
    """Grauschleier / ungleichmäßige Ausleuchtung entfernen (nur wenn nötig)."""
    w, h = img.size
    small = img.resize((max(1, w // 8), max(1, h // 8)), Image.BILINEAR)
    bg = small.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(6))
    bg_arr = np.asarray(bg, dtype=np.float32)
    lo, hi = np.percentile(bg_arr, [5, 95])
    arr = np.asarray(img, dtype=np.uint8)
    if hi - lo < 25 and lo > 215:  # gleichmäßig heller Hintergrund
        return _strengthen_faint_text(arr)
    bg_full = np.asarray(bg.resize((w, h), Image.BILINEAR), dtype=np.float32)
    norm = np.clip(arr.astype(np.float32) / np.maximum(bg_full, 1.0) * 255.0, 0, 255).astype(np.uint8)
    return _strengthen_faint_text(norm)


# ---------------------------------------------------------------- OCR-Kern

def _ocr_image(img: Image.Image, dpi: int, opts: OcrOptions) -> list[tuple[str, tuple[float, float, float, float], int]]:
    """OCR auf einem Graustufenbild. Liefert Wörter in Pixelkoordinaten."""
    gray = pymupdf.Pixmap(pymupdf.csGRAY, img.width, img.height, img.tobytes(), False)
    # Die eingebaute OCR liefert nur mit RGB-Pixmaps Ergebnisse
    pix = pymupdf.Pixmap(pymupdf.csRGB, gray)
    pix.set_dpi(dpi, dpi)
    data = pix.pdfocr_tobytes(compress=False, language=opts.languages, tessdata=opts.tessdata)
    with pymupdf.open("pdf", data) as ocr_doc:
        page = ocr_doc[0]
        sx = img.width / page.rect.width
        sy = img.height / page.rect.height
        out = []
        line_ids: dict[tuple[int, int], int] = {}
        for x0, y0, x1, y1, text, block, line, _wno in page.get_text("words"):
            text = text.strip()
            if not text:
                continue
            lid = line_ids.setdefault((block, line), len(line_ids))
            out.append((text, (x0 * sx, y0 * sy, x1 * sx, y1 * sy), lid))
    return out


def _bands(profile: np.ndarray, min_val: int, max_gap: int) -> list[tuple[int, int]]:
    """Zusammenhängende Abschnitte, in denen profile >= min_val (Lücken bis max_gap überbrückt)."""
    idx = np.flatnonzero(profile >= min_val)
    if idx.size == 0:
        return []
    out = []
    start = prev = int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i - prev > max_gap + 1:
            out.append((start, prev + 1))
            start = i
        prev = i
    out.append((start, prev + 1))
    return out


def recover_missed_text(img: Image.Image, words, dpi: int, opts: OcrOptions,
                        max_regions: int = 40) -> list[tuple[str, tuple[float, float, float, float], int]]:
    """Zweiter Durchgang für Textbereiche, die die OCR übersprungen hat.

    Tesseract lässt bei ungewöhnlichem Layout (Stempel, Tabellen, gemischte
    Schriftgrößen) gelegentlich ganze Zeilen aus. Für eine Schwärzung ist das
    gefährlich (nicht erkannt = nicht geschwärzt). Deshalb: Bereiche mit
    Druckerschwärze, die keinem erkannten Wort gehören, ausschneiden und
    einzeln erneut lesen.
    """
    f = 4  # Raster für die Suche
    w, h = img.size
    small = np.asarray(img.resize((max(1, w // f), max(1, h // f)), Image.BOX), dtype=np.uint8)
    ink = small < 150
    covered = np.zeros_like(ink)
    pad = max(2, int(dpi / 150))
    for _t, (x0, y0, x1, y1), _l in words:
        covered[max(0, int(y0 / f) - pad):int(y1 / f) + pad + 1, max(0, int(x0 / f) - pad):int(x1 / f) + pad + 1] = True
    rest = ink & ~covered
    if rest.sum() < 15:
        return []
    min_h = max(2, int(dpi / 100))      # ca. 3 pt Schrifthöhe
    max_h = int(dpi / 2.5 / f)          # höher als ~ 0,4 Zoll: eher Bild/Logo
    found = []
    next_line = 1 + max((l for *_x, l in words), default=-1)
    regions = 0
    for r0, r1 in _bands(rest.sum(axis=1), 1, 1):
        if r1 - r0 < min_h or r1 - r0 > max_h:
            continue
        cols = rest[r0:r1].sum(axis=0)
        for c0, c1 in _bands(cols, 1, int(dpi / 20 / f)):
            if (c1 - c0) < 2 * min_h or rest[r0:r1, c0:c1].sum() < 12:
                continue
            regions += 1
            if regions > max_regions:
                return found
            m = int(dpi / 15)
            box = (max(0, c0 * f - m), max(0, r0 * f - m), min(w, c1 * f + m), min(h, r1 * f + m))
            crop = Image.new("L", (box[2] - box[0] + 2 * m, box[3] - box[1] + 2 * m), 255)
            crop.paste(img.crop(box), (m, m))
            ox, oy = box[0] - m, box[1] - m
            lines: dict[int, int] = {}
            for text, (x0, y0, x1, y1), line in _ocr_image(crop, dpi, opts):
                lid = lines.setdefault(line, next_line + len(lines))
                found.append((text, (x0 + ox, y0 + oy, x1 + ox, y1 + oy), lid))
            next_line += len(lines)
    return found


def text_quality(words) -> int:
    """Anzahl 'echter' Wörter – grobes Maß für eine gelungene Erkennung."""
    return sum(1 for w in words if _GOOD_WORD.match(w[0]))


def detect_orientation(img: Image.Image, dpi: int, opts: OcrOptions, base_quality: int, base_count: int) -> int:
    """Prüft, ob die Seite gedreht eingescannt wurde. Liefert 0/90/180/270 (im Uhrzeigersinn)."""
    # überwiegend echte Wörter erkannt -> Seite steht richtig herum
    if base_quality >= 3 and base_quality >= 0.45 * base_count:
        return 0
    arr = np.asarray(img)
    if (arr < 128).mean() < 0.003:
        return 0  # leere Seite
    small = img.copy()
    small.thumbnail((1800, 1800))
    small_dpi = max(72, int(dpi * small.width / img.width))
    best_rot, best_q = 0, base_quality
    for rot in (90, 180, 270):
        rimg = small.rotate(-rot, expand=True)
        q = text_quality(_ocr_image(rimg, small_dpi, opts))
        if q > best_q:
            best_rot, best_q = rot, q
    if best_rot and best_q >= max(10, 2 * base_quality):
        return best_rot
    return 0


def _make_inverse_mapper(angle: float, w: int, h: int):
    """Punkt im (von PIL) gedrehten Bild -> Punkt im Ausgangsbild."""
    a = -math.radians(angle)
    cos, sin = math.cos(a), math.sin(a)
    cx, cy = w / 2.0, h / 2.0

    def f(x: float, y: float) -> tuple[float, float]:
        dx, dy = x - cx, y - cy
        return cos * dx + sin * dy + cx, -sin * dx + cos * dy + cy

    return f


def ocr_page(page: pymupdf.Page, opts: OcrOptions) -> tuple[list[RawWord], int]:
    """OCR einer Seite. Liefert (Wörter in Seitenkoordinaten, empfohlene Drehung).

    Ist die empfohlene Drehung != 0, beziehen sich die Wörter bereits auf die
    gedrehte Seite (Breite/Höhe ggf. vertauscht). Der Aufrufer muss die Seite
    dann entsprechend drehen.
    """
    dpi = opts.dpi
    img = render_gray(page, dpi)

    def prepare(src: Image.Image):
        angle = estimate_skew(src) if opts.deskew else 0.0
        work = normalize_background(src)
        if abs(angle) >= 0.3:
            work = work.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
            return work, _make_inverse_mapper(angle, work.width, work.height)
        return work, None

    work, mapper = prepare(img)
    words_px = _ocr_image(work, dpi, opts)
    rotation = 0
    if opts.orientation:
        rotation = detect_orientation(work, dpi, opts, text_quality(words_px), len(words_px))
        if rotation:
            img = img.rotate(-rotation, expand=True)
            work, mapper = prepare(img)
            words_px = _ocr_image(work, dpi, opts)

    if opts.recover:
        words_px = words_px + recover_missed_text(work, words_px, dpi, opts)

    scale = 72.0 / dpi
    out: list[RawWord] = []
    for text, (x0, y0, x1, y1), line in words_px:
        if mapper:
            pts = [mapper(x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        out.append((text, (x0 * scale, y0 * scale, x1 * scale, y1 * scale), line))
    return out, rotation


# ---------------------------------------------------------------- Worker (Prozesspool)

_worker_doc: pymupdf.Document | None = None


def worker_init(pdf_bytes: bytes) -> None:
    global _worker_doc
    _worker_doc = pymupdf.open("pdf", pdf_bytes)


def worker_ocr(page_index: int, opts: OcrOptions) -> tuple[int, list[RawWord], int]:
    assert _worker_doc is not None
    words, rot = ocr_page(_worker_doc[page_index], opts)
    return page_index, words, rot
