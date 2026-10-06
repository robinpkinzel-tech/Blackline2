"""Ende-zu-Ende-Test mit der ECHTEN lokalen KI.

Läuft nur, wenn BLACKLINE_REAL_KI=1 gesetzt ist und KI + OCR eingerichtet sind
(python -m blackline2.setup_ki). In der CI wird das automatisch gemacht.
"""

import os
import time
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from blackline2 import paths
from blackline2.ai.client import ChatClient
from blackline2.ai.detector import AIDetector
from blackline2.ai.server import LocalAIServer
from blackline2.analysis import Analyzer
from blackline2.detect_inputs import UserInputs
from blackline2.export import export_document
from blackline2.labels import PersonRegistry
from blackline2.loader import load_document
from blackline2.settings import Settings

pytestmark = pytest.mark.skipif(os.environ.get("BLACKLINE_REAL_KI") != "1",
                                reason="nur mit BLACKLINE_REAL_KI=1")

LETTER = [
    "Meier & Kollegen Rechtsanwälte - Bahnhofstraße 12 - 65549 Limburg",
    "",
    "Herrn",
    "Robin Kinzel",
    "Musterweg 15",
    "12345 Musterstadt",
    "",
    "In der Sache Kinzel ./. Schneider, Az. 2 C 123/24",
    "",
    "Sehr geehrter Herr Kinzel,",
    "die Zeugin Petra Musterfrau, wohnhaft Lindenweg 4, 35781 Weilburg,",
    "hat bestätigt, dass Herr Schneider am 12.03.2024 die Zahlung verweigert hat.",
    "Ihre Nachbarin Frau Gül Yilmaz kann dies ebenfalls bezeugen.",
    "Das Amtsgericht Limburg hat Termin auf den 15.10.2024 bestimmt.",
    "",
    "Mit freundlichen Grüßen",
    "Dr. Hans Meier",
    "Rechtsanwalt",
]


def _font(size):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "C:/Windows/Fonts/times.ttf",
              "C:/Windows/Fonts/arial.ttf", "/System/Library/Fonts/Supplemental/Times New Roman.ttf"):
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    pytest.skip("keine Schrift")


@pytest.fixture(scope="module")
def server():
    exe, model = paths.find_llama_server(), paths.find_model()
    if not exe or not model:
        pytest.skip("KI nicht eingerichtet")
    s = Settings()
    srv = LocalAIServer(exe, model, s.ki_context, s.ki_threads, s.ki_gpu_layers, s.ki_extra_args)
    srv.start()
    try:
        srv.wait_ready(600)
        yield srv
    finally:
        srv.stop()


def test_real_ki_letter(server, tmp_path):
    tessdata = paths.find_tessdata()
    if not tessdata:
        pytest.skip("OCR nicht eingerichtet")
    img = Image.new("L", (2480, 3508), 255)
    d = ImageDraw.Draw(img)
    f = _font(40)
    for i, line in enumerate(LETTER):
        d.text((200, 250 + i * 80), line, font=f, fill=0)
    img = img.rotate(0.8, fillcolor=255)
    png = tmp_path / "brief.png"
    img.save(png)

    s = Settings()
    s.tessdata_path = str(tessdata)
    doc = load_document(png, s)
    print("\nOCR-Text:\n" + doc.pages[0].text)

    inputs = UserInputs("Robin Kinzel", "Musterweg 15, 12345 Musterstadt", "Klaus Schneider", "")
    reg = PersonRegistry()
    det = AIDetector(ChatClient(server.base_url, server.api_key, timeout=900), reg, inputs)
    t0 = time.monotonic()
    report = Analyzer(s, inputs, reg, det).run([doc])
    print(f"\nKI-Analyse: {time.monotonic() - t0:.1f} s, Fehler: {report.ai_errors}")
    for h in doc.hits:
        print(f"  {h.source:16} {h.label:20} {h.text!r}")
    assert not report.ai_errors

    texts = " ".join(h.text for h in doc.hits if h.enabled)
    assert "Musterfrau" in texts, "KI hat die Zeugin nicht gefunden"
    assert "Yilmaz" in texts or "Yılmaz" in texts, "KI hat die Nachbarin nicht gefunden"
    assert not any(h.text == "Zeugin" for h in doc.hits), "Rollenwort als Name geschwärzt"
    by_text = {h.text: h.label for h in doc.hits}
    assert by_text.get("Petra Musterfrau", "").startswith("Person"), by_text
    for d in ("12.03.2024", "15.10.2024"):
        if d in by_text:
            print(f"HINWEIS: gewöhnliches Datum {d} wurde geschwärzt ({by_text[d]})")

    out = tmp_path / "brief_geschwärzt.pdf"
    export_document(doc, out, mode="bild", dpi=200)
    s2 = Settings()
    s2.tessdata_path = str(tessdata)
    s2.ocr_mode = "immer"
    again = load_document(out, s2).pages[0].text
    print("\nNach Schwärzung:\n" + again)
    for secret in ("Kinzel", "Schneider", "Musterfrau", "Yilmaz", "Lindenweg", "Musterstadt"):
        assert secret not in again, secret
    with pymupdf.open(out) as r:
        assert r.page_count == 1
