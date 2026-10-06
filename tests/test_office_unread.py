"""Word-Dateien und ungelesene Bereiche (Handschrift, Stempel)."""

import math
import random
from pathlib import Path
from unittest import mock

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from blackline2 import loader
from blackline2.analysis import Analyzer
from blackline2.detect_inputs import UserInputs
from blackline2.labels import PersonRegistry
from blackline2.loader import load_document
from blackline2.settings import Settings


def _font(size):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "C:/Windows/Fonts/times.ttf",
              "/System/Library/Fonts/Supplemental/Times New Roman.ttf"):
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    pytest.skip("keine TrueType-Schrift gefunden")


@pytest.fixture
def docx_file(tmp_path):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_heading("Klage", 1)
    d.add_paragraph("Sehr geehrte Frau Erika Mustermann, wir vertreten Herrn Robin Kinzel.")
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "IBAN"
    t.cell(0, 1).text = "DE89 3704 0044 0532 0130 00"
    path = tmp_path / "klage.docx"
    d.save(str(path))
    return path


def test_docx_fallback_without_office(docx_file):
    with mock.patch.object(loader, "_find_soffice", lambda: None), \
            mock.patch.object(loader, "_convert_with_word", lambda *_a: None):
        data, method = loader.office_to_pdf(docx_file)
    assert "vereinfacht" in method
    with pymupdf.open("pdf", data) as pdf:
        text = pdf[0].get_text()
        assert "Erika Mustermann" in text and "DE89" in text


def test_docx_load_and_redact(docx_file, tmp_path):
    s = Settings()
    doc = load_document(docx_file, s)
    assert doc.note.startswith("umgewandelt")
    assert doc.pages[0].source == "text"
    Analyzer(s, UserInputs("Robin Kinzel"), PersonRegistry()).run([doc])
    labels = {(h.label, h.text) for h in doc.hits}
    assert ("Mandant", "Robin Kinzel") in labels
    assert any(l == "IBAN" for l, _t in labels)


@pytest.mark.skipif(loader._find_soffice() is None, reason="LibreOffice nicht installiert")
def test_docx_via_libreoffice(docx_file):
    data, method = loader.office_to_pdf(docx_file)
    assert method == "LibreOffice"
    with pymupdf.open("pdf", data) as pdf:
        assert "Mustermann" in pdf[0].get_text()


@pytest.fixture
def handwriting_scan(tmp_path):
    """Gedruckter Text, dazu Handschrift-Imitation, Unterschrift, Tabellenlinien und ein Logo."""
    random.seed(1)
    img = Image.new("L", (2480, 3508), 255)
    d = ImageDraw.Draw(img)
    f = _font(40)
    for i, line in enumerate(["An Herrn Robin Kinzel, Musterweg 15", "Sehr geehrter Herr Kinzel,",
                              "die Sache wird vor dem Amtsgericht verhandelt."]):
        d.text((200, 400 + i * 90), line, font=f, fill=0)
    for k in range(3):  # "Handschrift" rechts
        pts = [(1500 + j * 6 + random.randint(-2, 2), 500 + k * 70 + int(18 * math.sin(j / 3.0)) + random.randint(-3, 3))
               for j in range(120)]
        d.line(pts, fill=0, width=5)
    pts = [(300 + j * 5, 1800 + int(40 * math.sin(j / 5.0)) * (1 if j % 40 < 20 else -1)) for j in range(200)]
    d.line(pts, fill=0, width=6)  # Unterschrift
    for yy in range(2200, 2600, 100):  # Tabellenlinien
        d.line((200, yy, 2300, yy), fill=0, width=3)
    d.rectangle((2000, 150, 2300, 350), fill=0)  # Logo
    png = tmp_path / "hand.png"
    img.save(png)
    return png


def test_unread_regions_detected(handwriting_scan, tessdata):
    s = Settings()
    s.tessdata_path = tessdata
    doc = load_document(handwriting_scan, s)
    page = doc.pages[0]
    assert "Amtsgericht" in page.text
    unread = page.unread
    assert unread, "Handschrift/Unterschrift nicht gemeldet"

    def covers(x, y):
        return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in unread)

    assert covers(1850 * 72 / 300, 540 * 72 / 300), "Handschrift rechts fehlt"
    assert covers(800 * 72 / 300, 1800 * 72 / 300), "Unterschrift fehlt"
    assert not covers(2150 * 72 / 300, 250 * 72 / 300), "Logo fälschlich gemeldet"
    assert not covers(1200 * 72 / 300, 2250 * 72 / 300), "Tabellenlinien fälschlich gemeldet"
    assert not covers(300 * 72 / 300, 420 * 72 / 300), "gedruckter Text fälschlich gemeldet"
    assert len(unread) <= 6
