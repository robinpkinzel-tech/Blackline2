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
from PIL import Image, ImageFilter, ImageOps

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


def normalize_background(img: Image.Image) -> Image.Image:
    """Grauschleier / ungleichmäßige Ausleuchtung entfernen (nur wenn nötig)."""
    w, h = img.size
    small = img.resize((max(1, w // 8), max(1, h // 8)), Image.BILINEAR)
    bg = small.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(6))
    bg_arr = np.asarray(bg, dtype=np.float32)
    lo, hi = np.percentile(bg_arr, [5, 95])
    if hi - lo < 25 and hi > 200:
        return ImageOps.autocontrast(img, cutoff=0.5)
    bg_full = np.asarray(bg.resize((w, h), Image.BILINEAR), dtype=np.float32)
    arr = np.asarray(img, dtype=np.float32)
    norm = np.clip(arr / np.maximum(bg_full, 1.0) * 255.0, 0, 255).astype(np.uint8)
    return ImageOps.autocontrast(Image.fromarray(norm), cutoff=0.5)


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


def text_quality(words) -> int:
    """Anzahl 'echter' Wörter – grobes Maß für eine gelungene Erkennung."""
    return sum(1 for w in words if _GOOD_WORD.match(w[0]))


def detect_orientation(img: Image.Image, dpi: int, opts: OcrOptions, base_quality: int, base_count: int) -> int:
    """Prüft, ob die Seite gedreht eingescannt wurde. Liefert 0/90/180/270 (im Uhrzeigersinn)."""
    if base_count >= 25 and base_quality >= 0.45 * base_count:
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
