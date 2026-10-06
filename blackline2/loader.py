"""Dokumente laden: PDF und Bilder -> normalisiertes PDF + erkannter Text je Seite.

Word-Dateien folgen in einem späteren Schritt (Umwandlung nach PDF).
"""

from __future__ import annotations

import html
import io
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
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
OFFICE_EXT = {".docx", ".doc", ".odt", ".rtf"}
SUPPORTED_EXT = PDF_EXT | IMAGE_EXT | OFFICE_EXT

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


# ---------------------------------------------------------------- Word & Co.

def _find_soffice() -> str | None:
    cands: list[str | None] = [shutil.which("soffice"), shutil.which("libreoffice")]
    if sys.platform == "win32":
        for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                   os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            cands.append(str(Path(pf) / "LibreOffice" / "program" / "soffice.exe"))
    elif sys.platform == "darwin":
        cands.append("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    for c in cands:
        if c and Path(c).is_file():
            return c
    return None


def _convert_with_word(path: Path, out_dir: Path) -> Path | None:
    """Umwandlung über ein installiertes Microsoft Word (nur Windows)."""
    if sys.platform != "win32":
        return None
    try:
        import pythoncom  # type: ignore[import-not-found]
        import win32com.client  # type: ignore[import-not-found]
    except ImportError:
        return None
    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(str(path.resolve()), ReadOnly=True, AddToRecentFiles=False)
        out = out_dir / (path.stem + ".pdf")
        doc.SaveAs2(str(out), FileFormat=17)  # wdFormatPDF
        doc.Close(False)
        return out if out.is_file() else None
    except Exception:  # noqa: BLE001 – Word nicht installiert / Datei defekt
        return None
    finally:
        try:
            if word is not None:
                word.Quit()
        except Exception:  # noqa: BLE001
            pass
        pythoncom.CoUninitialize()


def _convert_with_soffice(path: Path, out_dir: Path) -> Path | None:
    exe = _find_soffice()
    if not exe:
        return None
    profile = out_dir / "profil"
    try:
        subprocess.run([exe, "--headless", "--norestore", f"-env:UserInstallation={profile.resolve().as_uri()}",
                        "--convert-to", "pdf", "--outdir", str(out_dir), str(path)],
                       capture_output=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = out_dir / (path.stem + ".pdf")
    return out if out.is_file() else None


def _convert_simple(path: Path) -> bytes | None:
    """Notlösung ohne Word/LibreOffice: Text und Tabellen aus .docx in ein schlichtes PDF setzen."""
    if path.suffix.lower() != ".docx":
        return None
    try:
        import docx  # python-docx
    except ImportError:
        return None
    d = docx.Document(str(path))
    parts: list[str] = []
    body = d.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = "".join(t.text or "" for t in child.iter() if t.tag.endswith("}t"))
            parts.append(f"<p>{html.escape(text) or '&nbsp;'}</p>")
        elif tag == "tbl":
            rows = []
            for tr in child.iter():
                if not tr.tag.endswith("}tr"):
                    continue
                cells = []
                for tc in tr.iter():
                    if tc.tag.endswith("}tc"):
                        cells.append("".join(t.text or "" for t in tc.iter() if t.tag.endswith("}t")))
                rows.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
            parts.append("<table>" + "".join(rows) + "</table>")
    for section in d.sections:
        for hf in (section.header, section.footer):
            try:
                text = " ".join(p.text for p in hf.paragraphs if p.text.strip())
            except Exception:  # noqa: BLE001
                text = ""
            if text:
                parts.append(f"<p><i>{html.escape(text)}</i></p>")
    css = "body{font-family:sans-serif;font-size:11pt;} td{border:0.5pt solid #888;padding:2pt 4pt;}"
    story = pymupdf.Story(html="<html><body>" + "".join(parts) + "</body></html>", user_css=css)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (56, 56, -56, -56)
    more = True
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return buf.getvalue()


def office_to_pdf(path: Path) -> tuple[bytes, str]:
    """Word/ODT/RTF -> PDF-Bytes. Liefert (pdf, verwendetes Verfahren)."""
    with tempfile.TemporaryDirectory(prefix="blackline2_") as tmp:
        out_dir = Path(tmp)
        for name, fn in (("Microsoft Word", _convert_with_word), ("LibreOffice", _convert_with_soffice)):
            out = fn(path, out_dir)
            if out is not None:
                return out.read_bytes(), name
    data = _convert_simple(path)
    if data:
        return data, "vereinfachte Darstellung (ohne Word/LibreOffice)"
    raise LoadError(f"{path.name} konnte nicht in PDF umgewandelt werden. Bitte Microsoft Word oder "
                    "LibreOffice installieren oder die Datei als PDF speichern.")


def open_as_pdf(path: Path) -> tuple[pymupdf.Document, str]:
    ext = path.suffix.lower()
    note = ""
    try:
        if ext in PDF_EXT:
            doc = pymupdf.open(path)
            if doc.needs_pass:
                raise LoadError(f"{path.name} ist passwortgeschützt.")
        elif ext in IMAGE_EXT:
            doc = _image_to_pdf(path)
        elif ext in OFFICE_EXT:
            data, method = office_to_pdf(path)
            doc = pymupdf.open("pdf", data)
            note = f"umgewandelt über {method}"
        else:
            raise LoadError(f"Dateityp {ext} wird nicht unterstützt.")
    except LoadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LoadError(f"{path.name} konnte nicht geöffnet werden: {exc}") from exc
    if doc.page_count == 0:
        raise LoadError(f"{path.name} enthält keine Seiten.")
    # Kommentare, Stempel und Formularfelder in den Seiteninhalt einbrennen: so werden
    # Namen darin erkannt und geschwärzt, und versteckte Notizen fallen weg.
    try:
        doc.bake()
    except Exception:  # noqa: BLE001 – ältere PyMuPDF-Version oder defekte Anmerkung
        pass
    # Seitendrehung "einbacken": danach gilt überall dasselbe Koordinatensystem
    for page in doc:
        if page.rotation:
            page.remove_rotation()
    return doc, note


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
    seen: set[tuple[str, int, int]] = set()
    for x0, y0, x1, y1, text, block, line, _w in page.get_text("words"):
        text = text.strip()
        key = (text, round(x0), round(y0))
        if not text or key in seen:  # doppelt gezeichneter Text
            continue
        seen.add(key)
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


def _to_page(index: int, page: pymupdf.Page, raw: list[ocr.RawWord], source: str,
             unread: list | None = None) -> PageData:
    words = [Word(t, b, line) for t, b, line in raw]
    return PageData(index=index, width=page.rect.width, height=page.rect.height,
                    words=words, source=source, unread=list(unread or []))


def load_document(path: Path, settings: Settings, progress: ProgressFn | None = None,
                  cancel: threading.Event | None = None) -> Document:
    path = Path(path)
    with LOCK:
        doc, note = open_as_pdf(path)
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

    results: dict[int, tuple[list[ocr.RawWord], int, str, list]] = {}
    with LOCK:
        for i in range(n):
            if i not in ocr_pages:
                results[i] = (native_words(doc[i]), 0, "text", [])

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
                        idx, words, rot, unread = fut.result()
                        results[idx] = (words, rot, "ocr", unread)
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
                    words, rot, unread = ocr.ocr_page(doc[i], opts)
                results[i] = (words, rot, "ocr", unread)
                done += 1
                report(f"{path.name}: Seite {i + 1} erkannt")

    with LOCK:
        # gedreht eingescannte Seiten aufrichten
        for i, (_words, rot, _src, _unread) in results.items():
            if rot:
                doc[i].set_rotation(rot)
                doc[i].remove_rotation()
        pages = [_to_page(i, doc[i], results[i][0], results[i][2], results[i][3]) for i in range(n)]
        pdf_bytes = doc.tobytes(garbage=1, deflate=True)
        doc.close()
    return Document(path=path, pdf_bytes=pdf_bytes, pages=pages, note=note)
