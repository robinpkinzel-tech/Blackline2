# Blackline 2 – KI-gestützte Schwärzung für die Anwaltspraxis

Blackline 2 liest PDFs und Bilder (auch eingescannte Papierakten), findet mit einer
**lokalen KI** Namen und persönliche Daten und schwärzt sie: Die Stelle wird **weiß
überdeckt** und mit einem **Kürzel in schwarzer Schrift** beschriftet
(z. B. „Mandant“, „Adresse Gegner“, „Person A“, „Telefon“).

**Alles läuft auf Ihrem Rechner.** Es werden keine Dokumente ins Internet geschickt.
Die KI startet mit dem Programm und wird **beim Schließen automatisch beendet** –
auch wenn das Programm abstürzt oder per Task-Manager beendet wird.

---

## Funktionen

| Bereich | Was passiert |
|---|---|
| **Texterkennung (OCR)** | Eingescannte Seiten werden mit 300 dpi gelesen. Schief eingescannte Seiten werden begradigt, auf dem Kopf stehende oder quer liegende Seiten erkannt und aufgerichtet, Grauschleier/ungleichmäßige Ausleuchtung ausgeglichen. Digitale PDFs werden direkt gelesen. |
| **Ihre Angaben** | Mandant (Name, Adresse), Gegner (Name, Adresse) und 5 freie Felder „Suchbegriff → Kürzel“. Mehrere Angaben je Feld mit `;` trennen. Gefunden werden auch Varianten: nur Nachname, „R. Kinzel“, „Kinzels“, Silbentrennung („Kin-/zel“), OCR-Fehler („Kinzei“), „Str.“/„Straße“. |
| **KI** | Liest jede Seite vollständig und meldet alle Namen und persönlichen Daten natürlicher Personen. Jede Person bekommt ein festes Kürzel (Person A, Person B …), das in allen Dokumenten des Vorgangs gleich bleibt. Was die KI auf einer Seite findet, wird automatisch auch auf allen anderen Seiten geschwärzt. Erfundene Funde („Halluzinationen“) werden verworfen, weil nur geschwärzt wird, was wirklich im Text steht. |
| **Feste Regeln** | E-Mail, Telefon/Handy/Fax, IBAN (mit Prüfziffer) und Kontonummern, Sozialversicherungs-/Renten-/Krankenversichertennummern, Versicherungsscheinnummern, Geburtsdaten („geb.“, „geb. am“, „geboren am“, „Geburtsdatum:“, „\*“), Geburtsort und Geburtsname. |
| **Prüfen** | Alle Funde farbig markiert, Klick schaltet einzelne Stellen ab/an, Liste nach Kürzel gruppiert, Kürzel umbenennbar, fehlende Stellen per Maus manuell aufziehen, Vorschau des Endergebnisses. |
| **Speichern** | Standard „Bild-PDF“: Jede Seite wird als Bild neu aufgebaut, die Bildpunkte unter den Schwärzungen werden überschrieben – es bleibt nichts Verstecktes übrig. Alternativ „Text-PDF“ (durchsuchbar) mit echter PDF-Redaction und automatischer Nachprüfung. Das Original wird nie verändert. |

Unterstützte Dateien: PDF, PNG, JPG, TIFF (auch mehrseitig), BMP, GIF, WEBP.
Word-Dateien folgen im nächsten Schritt.

---

## Einrichtung unter Windows (einmalig)

Voraussetzungen: Windows 10/11, mind. 16 GB RAM empfohlen, ca. 8 GB freier Speicher.

1. **Python installieren** (falls noch nicht vorhanden): <https://www.python.org/downloads/> →
   „Download Python 3.12“ → Installer starten → unten **„Add python.exe to PATH“ anhaken** →
   „Install Now“.
2. **Blackline 2 herunterladen**: Auf GitHub → grüner Knopf **„Code“** → **„Download ZIP“** →
   ZIP z. B. nach `C:\Blackline2` entpacken.
   (Oder mit Git: `git clone https://github.com/robinpkinzel-tech/Blackline2.git C:\Blackline2`)
3. **Einrichten**: Im Ordner `C:\Blackline2` doppelt auf **`einrichten_windows.bat`** klicken.
   Das Skript installiert alles Nötige und lädt die KI (ca. 5 GB) sowie die deutschen
   OCR-Sprachdaten herunter. Am Ende folgt ein kurzer Selbsttest der KI.
4. **Starten**: Doppelklick auf **`Blackline2_starten.bat`**.

### Varianten der Einrichtung

Im Terminal (Eingabeaufforderung) im Programmordner:

```bat
cd C:\Blackline2
einrichten_windows.bat --modell schnell      :: kleineres Modell (2,5 GB) für ältere Rechner
einrichten_windows.bat --gpu vulkan          :: Grafikkarte nutzen (AMD/Intel/NVIDIA)
einrichten_windows.bat --gpu cuda            :: NVIDIA-Grafikkarte mit CUDA
einrichten_windows.bat --modell gruendlich   :: größtes Modell (7 GB), sehr gutes Deutsch
```

| Modell | Größe | Empfehlung |
|---|---|---|
| `schnell` – Qwen3 4B | ca. 2,5 GB | ältere Rechner ohne Grafikkarte, 8–16 GB RAM |
| `ausgewogen` – Qwen3 8B (Standard) | ca. 5 GB | 16 GB RAM |
| `gruendlich` – Gemma 3 12B | ca. 7 GB | Grafikkarte oder 32 GB RAM |

Richtwert ohne Grafikkarte: ca. 20–60 Sekunden pro Seite für die KI-Prüfung
(Texterkennung ca. 1–2 Sekunden pro Seite). Mit Grafikkarte deutlich schneller.

Eigenes Modell: Jede `.gguf`-Datei in `ki\modelle\` kann verwendet werden
(Einstellungen → KI → KI-Modell).

---

## Bedienung

1. **Dokumente laden** – „Dateien öffnen“ oder Dateien/Ordner ins Fenster ziehen.
2. **Angaben eintragen** (Reiter „1. Angaben“):
   * Mandant: Name → wird zu „Mandant“, Adresse → „Adresse Mandant“
   * Gegner: Name → „Gegner“, Adresse → „Adresse Gegner“
   * Bis zu 5 freie Begriffe, z. B. `Volkswagen` → `Arbeitgeber`
3. **Analysieren** – Regeln und KI laufen über alle Seiten aller Dokumente.
4. **Funde prüfen** (Reiter „2. Funde prüfen“):
   * Markierung anklicken = diese Stelle nicht schwärzen (gestrichelt) / wieder schwärzen
   * Rechtsklick in der Liste = Kürzel ändern (z. B. „Person A“ → „Zeuge 1“)
   * „Bereich manuell schwärzen“ = fehlende Stelle mit der Maus aufziehen
   * „Vorschau Schwärzung“ = so sieht das Ergebnis aus
5. **Geschwärzt speichern** – Zielordner wählen; Dateien erhalten den Zusatz `_geschwärzt`.

Farben: rot = Namen, orange = Adressen, blau = E-Mail, lila = Telefon,
türkis = IBAN, braun = Versicherungsnr., pink = Geburtsdaten, grün = eigene Begriffe.

---

## Datenschutz und Sicherheit

* **Keine Cloud**: Die KI (llama.cpp) läuft lokal und ist nur über `127.0.0.1` erreichbar,
  mit einem bei jedem Start neu erzeugten Zugangsschlüssel.
* **KI nur solange das Programm offen ist**: Beim Schließen wird die KI beendet.
  Unter Windows ist sie zusätzlich an ein Job-Objekt gekoppelt – endet Blackline 2
  auf irgendeine Weise, beendet Windows die KI automatisch mit. Verwaiste Prozesse
  eines früheren Absturzes werden beim nächsten Start aufgeräumt.
* **Keine Speicherung von Mandantendaten**: Die Eingabefelder werden nicht gespeichert.
  Gespeichert werden nur technische Einstellungen (`%APPDATA%\Blackline2\einstellungen.json`).
* **Sichere Schwärzung**: Im Bild-Modus enthält das Ergebnis nur noch Bildpunkte,
  unter den Schwärzungen weiß überschrieben. Metadaten werden entfernt.
* **Kontrolle bleibt beim Menschen**: Die KI ist ein Hilfsmittel. Bitte das Ergebnis
  vor der Weitergabe in der Vorschau prüfen.

---

## Fehlerbehebung

| Problem | Lösung |
|---|---|
| Statusleiste: „Nicht eingerichtet“ | `einrichten_windows.bat` erneut ausführen. |
| „deu.traineddata nicht gefunden“ | Terminal: `cd C:\Blackline2` → `.venv\Scripts\python -m blackline2.setup_ki --nur-ocr` |
| KI startet nicht | In der Statusleiste auf den KI-Status klicken → Protokoll ansehen. Ggf. `--modell schnell` probieren oder unter Einstellungen → KI die GPU-Schichten auf 0 setzen. |
| KI sehr langsam | Kleineres Modell (`--modell schnell`) oder Grafikkarte nutzen (`--gpu vulkan`). |
| Download bricht ab | Einfach erneut starten – angefangene Downloads werden fortgesetzt. |
| Selbsttest der KI | `cd C:\Blackline2` → `.venv\Scripts\python -m blackline2.setup_ki --test` |

---

## Für Entwickler

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt pytest
python -m blackline2.setup_ki --nur-ocr
python -m pytest                                  # Tests (KI wird durch eine Attrappe ersetzt)
BLACKLINE_REAL_KI=1 python -m pytest tests/test_real_ki.py -s   # mit echter KI
python -m blackline2                              # Programm starten
```

Aufbau:

```
blackline2/
  loader.py          PDF/Bilder laden, Seitendrehung normalisieren, OCR je Seite (parallel)
  ocr.py             Vorverarbeitung (Begradigen, Ausleuchtung, Ausrichtung) + Tesseract via PyMuPDF
  matching.py        tolerante Suche (OCR-Fehler, Silbentrennung, Genitiv, Straße/Str.)
  detect_patterns.py feste Regeln (E-Mail, Telefon, IBAN, Versicherungsnr., Geburtsdatum)
  detect_inputs.py   Mandant/Gegner/freie Begriffe
  labels.py          Kürzel und Personenverwaltung (Person A, B, …)
  ai/server.py       Start/Stopp von llama-server (Job-Objekt, PDEATHSIG, Wächter)
  ai/client.py       OpenAI-kompatibler Client (nur localhost)
  ai/detector.py     Prompt, JSON-Schema, Auswertung der KI-Antworten
  analysis.py        Gesamtablauf, Vorrangregeln, Übertragung auf alle Seiten
  export.py          Schwärzen (weiß + Kürzel), Bild- oder Text-PDF, Nachprüfung
  setup_ki.py        Download von llama.cpp, Modell und OCR-Daten, Selbsttest
  gui/               Oberfläche (PySide6)
```

Die GitHub-Actions-Prüfung (`.github/workflows/tests.yml`) testet unter Windows und Linux
auch die echte KI: Download von llama.cpp und Modell, Selbsttest und ein vollständiger
Durchlauf Scan → OCR → KI → Schwärzung → erneute OCR-Prüfung des Ergebnisses.

## Nächste Schritte

* Word-Dateien (.docx) einlesen
* Fertige Windows-Installation als einzelne .exe (PyInstaller)
