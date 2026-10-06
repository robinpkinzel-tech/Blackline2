"""Dokumente laden: PDF und Bilder -> normalisiertes PDF + erkannter Text je Seite.

Word-Dateien folgen in einem späteren Schritt (Umwandlung nach PDF).
"""

from __future__ import annotations

import io
import multiprocessing
import os
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import pymupdf
from PIL import Image, ImageOps, ImageSequence

from blackline2 import ocr, paths
from blackline2.mupdf_lock import LOCK
from blackline2.model import Document, PageData, Word
from blackline2.settings import Settings

PDF_EXT = {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
SUPPORTED_EXT = PDF_EXT | IMAGE_EXT

ProgressFn = Callable[[int, int, str], None]


class LoadError(Exception):
    pass


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- Öffnen

def _image_to_pdf(path: Path) -> pymupdf.Document:
    """Bild(er) -> PDF. Seitenbreite DIN A4 (595 pt), Höhe nach Seitenverhältnis."""
    doc = pymupdf.open()
    with Image.open(path) as im:
        for frame in ImageSequence.Iterator(im):
            fr = ImageOps.exif_transpose(frame.copy())
            if fr.mode not in ("L", "RGB"):
                fr = fr.convert("RGB")
            buf = io.BytesIO()
            if fr.mode == "L" and len(fr.getcolors(256) or []) <= 2:
                fr.save(buf, format="PNG", optimize=True)
            else:
                fr.save(buf, format="JPEG", quality=92)
            width = 595.0
            height = width * fr.height / fr.width
            page = doc.new_page(width=width, height=height)
            page.insert_image(page.rect, stream=buf.getvalue())
    return doc


def open_as_pdf(path: Path) -> pymupdf.Document:
    ext = path.suffix.lower()
    try:
        if ext in PDF_EXT:
            doc = pymupdf.open(path)
            if doc.needs_pass:
                raise LoadError(f"{path.name} ist passwortgeschützt.")
        elif ext in IMAGE_EXT:
            doc = _image_to_pdf(path)
        elif ext in {".doc", ".docx"}:
            raise LoadError("Word-Dateien werden im nächsten Ausbauschritt unterstützt. "
                            "Bitte vorerst als PDF speichern.")
        else:
            raise LoadError(f"Dateityp {ext} wird nicht unterstützt.")
    except LoadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LoadError(f"{path.name} konnte nicht geöffnet werden: {exc}") from exc
    if doc.page_count == 0:
        raise LoadError(f"{path.name} enthält keine Seiten.")
    # Seitendrehung "einbacken": danach gilt überall dasselbe Koordinatensystem
    for page in doc:
        if page.rotation:
            page.remove_rotation()
    return doc


# ---------------------------------------------------------------- Text je Seite

def _image_coverage(page: pymupdf.Page) -> float:
    area = page.rect.width * page.rect.height or 1.0
    covered = 0.0
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & page.rect
        covered = max(covered, r.width * r.height)
    return covered / area


def native_words(page: pymupdf.Page) -> list[ocr.RawWord]:
    out: list[ocr.RawWord] = []
    line_ids: dict[tuple[int, int], int] = {}
    for x0, y0, x1, y1, text, block, line, _w in page.get_text("words"):
        text = text.strip()
        if not text:
            continue
        lid = line_ids.setdefault((block, line), len(line_ids))
        out.append((text, (x0, y0, x1, y1), lid))
    return out


def needs_ocr(page: pymupdf.Page, mode: str) -> bool:
    if mode == "immer":
        return True
    if mode == "nie":
        return False
    text = page.get_text("text").strip()
    if len(text) < 30:
        return True
    # Eingescannte Seite mit (oft schlechter) Scanner-Textebene -> selbst erkennen
    if _image_coverage(page) > 0.5:
        return True
    # Sehr viele unlesbare Zeichen -> kaputte Schriftkodierung
    bad = sum(1 for ch in text if ch == "�" or (ord(ch) < 32 and ch not in "\n\t\r"))
    return bad > 0.05 * len(text)


def _to_page(index: int, page: pymupdf.Page, raw: list[ocr.RawWord], source: str) -> PageData:
    words = [Word(t, b, line) for t, b, line in raw]
    return PageData(index=index, width=page.rect.width, height=page.rect.height,
                    words=words, source=source)


def load_document(path: Path, settings: Settings, progress: ProgressFn | None = None,
                  cancel: threading.Event | None = None) -> Document:
    path = Path(path)
    with LOCK:
        doc = open_as_pdf(path)
    n = doc.page_count
    tessdata = settings.tessdata_path or str(paths.find_tessdata() or "")
    opts = ocr.OcrOptions(tessdata=tessdata, languages=settings.ocr_languages,
                          dpi=settings.ocr_dpi, deskew=settings.ocr_deskew,
                          orientation=settings.ocr_orientation)

    with LOCK:
        ocr_pages = [i for i in range(n) if needs_ocr(doc[i], settings.ocr_mode)]
    if ocr_pages and not (tessdata and Path(tessdata, "deu.traineddata").is_file()):
        raise LoadError("Für eingescannte Seiten wird die Texterkennung benötigt, aber die "
                        "deutschen Sprachdaten (deu.traineddata) wurden nicht gefunden.\n"
                        "Bitte 'python -m blackline2.setup_ki --nur-ocr' ausführen.")

    results: dict[int, tuple[list[ocr.RawWord], int, str]] = {}
    with LOCK:
        for i in range(n):
            if i not in ocr_pages:
                results[i] = (native_words(doc[i]), 0, "text")

    done = len(results)

    def report(msg: str) -> None:
        if progress:
            progress(done, n, msg)

    report(f"{path.name}: Texterkennung …")
    if ocr_pages:
        with LOCK:
            pdf_bytes = doc.tobytes()
        workers = min(len(ocr_pages), max(1, (os.cpu_count() or 2) - 1), 6)
        if workers > 1 and len(ocr_pages) >= 3:
            ctx = multiprocessing.get_context("spawn")
            with ProcessPoolExecutor(max_workers=workers, mp_context=ctx,
                                     initializer=ocr.worker_init, initargs=(pdf_bytes,)) as pool:
                futures = [pool.submit(ocr.worker_ocr, i, opts) for i in ocr_pages]
                try:
                    for fut in as_completed(futures):
                        if cancel is not None and cancel.is_set():
                            raise Cancelled()
                        idx, words, rot = fut.result()
                        results[idx] = (words, rot, "ocr")
                        done += 1
                        report(f"{path.name}: Seite {idx + 1} erkannt")
                except BaseException:
                    for f in futures:
                        f.cancel()
                    raise
        else:
            for i in ocr_pages:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                with LOCK:
                    words, rot = ocr.ocr_page(doc[i], opts)
                results[i] = (words, rot, "ocr")
                done += 1
                report(f"{path.name}: Seite {i + 1} erkannt")

    with LOCK:
        # gedreht eingescannte Seiten aufrichten
        for i, (_words, rot, _src) in results.items():
            if rot:
                doc[i].set_rotation(rot)
                doc[i].remove_rotation()
        pages = [_to_page(i, doc[i], results[i][0], results[i][2]) for i in range(n)]
        pdf_bytes = doc.tobytes(garbage=1, deflate=True)
        doc.close()
    return Document(path=path, pdf_bytes=pdf_bytes, pages=pages)
