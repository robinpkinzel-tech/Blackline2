from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from blackline2.analysis import Analyzer
from blackline2.detect_inputs import UserInputs
from blackline2.export import export_document
from blackline2.labels import PersonRegistry
from blackline2.loader import load_document
from blackline2.settings import Settings

LINES = [
    "An Herrn Robin Kinzel, Musterweg 15, 12345 Musterstadt",
    "Sehr geehrter Herr Kinzel,",
    "Tel.: 06433/123456, E-Mail: robin.kinzel@web.de",
    "IBAN: DE89 3704 0044 0532 0130 00",
    "Die Sache wird vor dem Amtsgericht verhandelt.",
]


def _font(size):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "C:/Windows/Fonts/times.ttf",
              "/System/Library/Fonts/Supplemental/Times New Roman.ttf"):
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    pytest.skip("keine TrueType-Schrift gefunden")


@pytest.fixture
def scanned_pdf(tmp_path):
    """Simulierter Scan: Text als Bild, leicht schief, ohne Textebene."""
    img = Image.new("L", (2480, 3508), 255)
    d = ImageDraw.Draw(img)
    f = _font(42)
    for i, line in enumerate(LINES):
        d.text((200, 300 + i * 90), line, font=f, fill=0)
    img = img.rotate(1.0, fillcolor=255)
    png = tmp_path / "scan.png"
    img.save(png)
    pdf = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=str(png))
    doc.save(pdf)
    return pdf


@pytest.fixture
def digital_pdf(tmp_path):
    pdf = tmp_path / "digital.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for i, line in enumerate(LINES):
        page.insert_text((60, 80 + i * 20), line, fontsize=11)
    doc.set_metadata({"author": "Robin Kinzel", "title": "Akte Kinzel"})
    doc.save(pdf)
    return pdf


def analyze(path, tessdata):
    s = Settings()
    s.tessdata_path = tessdata
    doc = load_document(path, s)
    inputs = UserInputs("Robin Kinzel", "Musterweg 15, 12345 Musterstadt")
    Analyzer(s, inputs, PersonRegistry()).run([doc])
    return doc


def test_ocr_reads_scan(scanned_pdf, tessdata):
    s = Settings()
    s.tessdata_path = tessdata
    doc = load_document(scanned_pdf, s)
    assert doc.pages[0].source == "ocr"
    text = doc.pages[0].text
    for word in ("Kinzel", "Musterstadt", "robin.kinzel@web.de", "Amtsgericht"):
        assert word in text


def test_ocr_detects_upside_down_page(tmp_path, tessdata):
    img = Image.new("L", (2480, 3508), 255)
    d = ImageDraw.Draw(img)
    for i in range(12):
        d.text((200, 300 + i * 90), LINES[i % len(LINES)], font=_font(42), fill=0)
    img.rotate(180).save(tmp_path / "kopf.png")
    s = Settings()
    s.tessdata_path = tessdata
    doc = load_document(tmp_path / "kopf.png", s)
    assert "Amtsgericht" in doc.pages[0].text


@pytest.mark.parametrize("mode", ["bild", "text"])
def test_export_removes_content(scanned_pdf, tessdata, tmp_path, mode):
    doc = analyze(scanned_pdf, tessdata)
    labels = {h.label for h in doc.hits}
    assert {"Mandant", "Adresse Mandant", "Telefon", "E-Mail", "IBAN"} <= labels
    out = tmp_path / f"out_{mode}.pdf"
    res = export_document(doc, out, mode=mode, dpi=200, tessdata=tessdata)
    assert res.warnings == []
    # Ergebnis erneut per OCR lesen: geschwärzte Inhalte dürfen nicht mehr lesbar sein
    s = Settings()
    s.tessdata_path = tessdata
    s.ocr_mode = "immer"
    again = load_document(out, s).pages[0].text
    for secret in ("Kinzel", "Musterstadt", "06433", "web.de", "DE89"):
        assert secret not in again, (secret, again)
    assert "Mandant" in again and "Amtsgericht" in again


def test_text_export_digital_pdf(digital_pdf, tessdata, tmp_path):
    doc = analyze(digital_pdf, tessdata)
    assert doc.pages[0].source == "text"
    out = tmp_path / "digital_out.pdf"
    res = export_document(doc, out, mode="text")
    assert res.warnings == []
    with pymupdf.open(out) as r:
        text = r[0].get_text()
        assert "Kinzel" not in text and "06433" not in text
        assert "Mandant" in text and "Amtsgericht" in text
        assert "Kinzel" not in str(r.metadata)


def test_original_is_never_overwritten(digital_pdf, tessdata):
    doc = analyze(digital_pdf, tessdata)
    with pytest.raises(ValueError):
        export_document(doc, digital_pdf)
