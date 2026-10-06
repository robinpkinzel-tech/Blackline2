"""Erzeugen des geschwärzten PDFs.

Jede Fundstelle wird WEISS überdeckt und mit dem Kürzel in schwarzer Schrift
beschriftet (z. B. "Mandant", "Adresse Gegner", "Telefon").

Modus "bild" (Standard, maximal sicher):
    Jede Seite wird als Bild neu aufgebaut, die Bildpunkte unter den Schwärzungen
    werden weiß überschrieben. Im Ergebnis gibt es keinen versteckten Text, keine
    Ebenen, keine Metadaten – nichts, was sich "zurückholen" ließe.
Modus "text":
    Das PDF bleibt durchsuchbar. Text unter den Schwärzungen wird echt entfernt
    (PDF-Redaction), Bildpunkte darunter werden weiß überschrieben, Metadaten und
    Anhänge werden entfernt. Danach wird das Ergebnis automatisch geprüft.
"""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pymupdf

from blackline2 import ocr
from blackline2.labels import short_label
from blackline2.loader import Cancelled
from blackline2.matching import norm
from blackline2.model import Document, Hit, Rect
from blackline2.mupdf_lock import LOCK

ProgressFn = Callable[[int, int, str], None]


@dataclass
class ExportResult:
    path: Path
    pages: int
    redactions: int
    warnings: list[str] = field(default_factory=list)


def _fit(label: str, w: float, h: float) -> tuple[str, float]:
    """Schriftgröße so wählen, dass das Kürzel in das Rechteck passt."""
    base = max(min(h * 0.72, 12.0), 1.0)
    for text in (label, short_label(label)):
        size = base
        tl = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
        if tl > w - 2:
            size = size * max(w - 2, 1) / tl
        if size >= 4.5 or text != label:
            return text, size
    return label, base


def draw_label(page: pymupdf.Page, rect: Rect, label: str) -> None:
    r = pymupdf.Rect(rect)
    if r.width < 3 or r.height < 3:
        return
    text, size = _fit(label, r.width, r.height)
    if size < 3:
        return
    tl = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
    x = r.x0 + max((r.width - tl) / 2, 0.5)
    y = r.y0 + (r.height + size * 0.70) / 2
    page.insert_text((x, y), text, fontsize=size, fontname="helv", color=(0, 0, 0))


def _active(hits: list[Hit], page: int) -> list[Hit]:
    return [h for h in hits if h.page == page and h.enabled and h.rects]


def _is_color(page: pymupdf.Page) -> bool:
    pix = page.get_pixmap(dpi=24, colorspace=pymupdf.csRGB, alpha=False)
    s = pix.samples
    n = len(s) // 3
    if not n:
        return False
    step = max(1, n // 4000)
    colorful = 0
    checked = 0
    for i in range(0, n, step):
        r, g, b = s[3 * i], s[3 * i + 1], s[3 * i + 2]
        checked += 1
        if max(r, g, b) - min(r, g, b) > 40:
            colorful += 1
    return colorful / max(checked, 1) > 0.01


def _add_invisible_text(page: pymupdf.Page, pix: pymupdf.Pixmap, dpi: int, tessdata: str, languages: str) -> None:
    """Unsichtbare Textebene (durchsuchbar) – erkannt auf dem bereits GESCHWÄRZTEN Bild."""
    from PIL import Image

    mode = "L" if pix.n == 1 else "RGB"
    img = Image.frombytes(mode, (pix.width, pix.height), pix.samples).convert("L")
    opts = ocr.OcrOptions(tessdata=tessdata, languages=languages, dpi=dpi)
    scale = 72.0 / dpi
    for text, (x0, y0, x1, y1), _line in ocr._ocr_image(img, dpi, opts):
        x0, y0, x1, y1 = x0 * scale, y0 * scale, x1 * scale, y1 * scale
        h = max(y1 - y0, 1)
        size = h * 0.95
        tl = pymupdf.get_text_length(text, fontname="helv", fontsize=size) or 1
        sx = (x1 - x0) / tl
        p = pymupdf.Point(x0, y1 - h * 0.18)
        try:
            page.insert_text(p, text, fontsize=size, fontname="helv", render_mode=3,
                             morph=(p, pymupdf.Matrix(sx, 1)))
        except (ValueError, RuntimeError):
            continue


def _export_image(doc: Document, out_path: Path, dpi: int, searchable: bool, tessdata: str,
                  languages: str, progress: ProgressFn | None, cancel: threading.Event | None) -> ExportResult:
    src = pymupdf.open("pdf", doc.pdf_bytes)
    out = pymupdf.open()
    count = 0
    scale = dpi / 72.0
    try:
        for page in src:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            hits = _active(doc.hits, page.number)
            cs = pymupdf.csRGB if _is_color(page) else pymupdf.csGRAY
            pix = page.get_pixmap(dpi=dpi, colorspace=cs, alpha=False)
            white = tuple([255] * pix.n)
            for h in hits:
                for r in h.rects:
                    ir = pymupdf.IRect(math.floor(r[0] * scale), math.floor(r[1] * scale),
                                       math.ceil(r[2] * scale), math.ceil(r[3] * scale))
                    pix.set_rect(ir & pymupdf.IRect(0, 0, pix.width, pix.height), white)
            newp = out.new_page(width=page.rect.width, height=page.rect.height)
            newp.insert_image(newp.rect, stream=pix.tobytes("jpeg", jpg_quality=85))
            if searchable and tessdata:
                _add_invisible_text(newp, pix, dpi, tessdata, languages)
            for h in hits:
                for r in h.rects:
                    draw_label(newp, r, h.label)
                count += 1
            if progress:
                progress(page.number + 1, src.page_count, f"{doc.name}: Seite {page.number + 1} geschwärzt")
        out.set_metadata({"producer": "Blackline 2", "creator": "Blackline 2"})
        out.save(out_path, garbage=4, deflate=True)
        return ExportResult(out_path, src.page_count, count)
    finally:
        src.close()
        out.close()


def _export_text(doc: Document, out_path: Path, progress: ProgressFn | None,
                 cancel: threading.Event | None) -> ExportResult:
    src = pymupdf.open("pdf", doc.pdf_bytes)
    count = 0
    try:
        for page in src:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            pdata = doc.pages[page.number]
            if pdata.source == "ocr" and _image_dominated(page):
                # Vorhandene (Scanner-)Textebene liegt nicht exakt auf unseren Positionen -> ganz entfernen
                page.add_redact_annot(page.rect, fill=False)
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                      graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                      text=pymupdf.PDF_REDACT_TEXT_REMOVE)
            hits = _active(doc.hits, page.number)
            for h in hits:
                for r in h.rects:
                    page.add_redact_annot(pymupdf.Rect(r), fill=(1, 1, 1))
            if hits:
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                                      graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                      text=pymupdf.PDF_REDACT_TEXT_REMOVE)
            for h in hits:
                for r in h.rects:
                    draw_label(page, r, h.label)
                count += 1
            if progress:
                progress(page.number + 1, src.page_count, f"{doc.name}: Seite {page.number + 1} geschwärzt")
        src.scrub()
        src.set_metadata({"producer": "Blackline 2", "creator": "Blackline 2"})
        src.del_xml_metadata()
        src.save(out_path, garbage=4, deflate=True, clean=True)
        pages = src.page_count
    finally:
        src.close()
    warnings = verify_text_export(doc, out_path)
    return ExportResult(out_path, pages, count, warnings)


def _image_dominated(page: pymupdf.Page) -> bool:
    area = page.rect.width * page.rect.height or 1
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & page.rect
        if r.width * r.height > 0.5 * area:
            return True
    return False


def verify_text_export(doc: Document, out_path: Path) -> list[str]:
    """Prüft, ob geschwärzte Inhalte noch als Text im Ergebnis stecken."""
    warnings: list[str] = []
    with pymupdf.open(out_path) as res:
        for h in doc.hits:
            if not h.enabled or not h.text.strip() or h.page >= res.page_count:
                continue
            page_text = norm(" ".join(res[h.page].get_text("text").split()))
            needle = norm(" ".join(h.text.split()))
            if len(needle) >= 4 and needle in page_text and needle != norm(h.label):
                warnings.append(f"Seite {h.page + 1}: '{h.text}' ist noch als Text vorhanden.")
    return warnings


def neutral_stem(doc: Document) -> str:
    """Dateiname ohne Namen: geschwärzte Begriffe im Dateinamen durch Kürzel ersetzen.

    "Kinzel_Klage_2024" -> "Mandant_Klage_2024"
    """
    stem = doc.path.stem
    repl: dict[str, str] = {}
    for h in doc.hits:
        if not h.enabled or h.category == "manuell":
            continue
        for part in re.split(r"[\s,;]+", h.text):
            part = part.strip(".,;:()[]\"'")
            if len(part) >= 3 and not part.isdigit():
                repl.setdefault(part, h.label.replace(" ", "-").replace("/", "-"))
            elif part.isdigit() and len(part) >= 4:
                repl.setdefault(part, h.label.replace(" ", "-").replace("/", "-"))
    for part in sorted(repl, key=len, reverse=True):
        stem = re.sub(re.escape(part), repl[part], stem, flags=re.IGNORECASE)
    stem = re.sub(r'[<>:"/\\|?*]', "_", stem)
    return stem or "Dokument"


def output_path_for(doc: Document, folder: Path | None, suffix: str, neutral: bool = True) -> Path:
    folder = folder or doc.path.parent
    stem = neutral_stem(doc) if neutral else doc.path.stem
    return folder / f"{stem}{suffix}.pdf"


def export_document(doc: Document, out_path: Path, mode: str = "bild", dpi: int = 300,
                    searchable: bool = False, tessdata: str = "", languages: str = "deu",
                    progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> ExportResult:
    out_path = Path(out_path)
    if out_path.resolve() == doc.path.resolve():
        raise ValueError("Das Original darf nicht überschrieben werden.")
    with LOCK:
        if mode == "text":
            return _export_text(doc, out_path, progress, cancel)
        return _export_image(doc, out_path, dpi, searchable, tessdata, languages, progress, cancel)
