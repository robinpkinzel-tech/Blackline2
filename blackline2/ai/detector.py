"""KI-gestützte Erkennung personenbezogener Daten (vor allem Namen).

Die KI liest jede Seite vollständig und nennt alle Fundstellen wörtlich.
Blackline 2 sucht diese Fundstellen anschließend selbst im erkannten Text –
was nicht wirklich im Dokument steht (KI-"Halluzination"), wird verworfen.
"""

from __future__ import annotations

from dataclasses import dataclass

from blackline2.ai.client import AIClientError, ChatClient
from blackline2.detect_inputs import UserInputs, is_organisation, split_values
from blackline2.labels import PersonRegistry
from blackline2.matching import NAME_STOP, norm
from blackline2.model import PageData

AI_CATEGORIES = [
    "name", "adresse", "geburtsdatum", "geburtsort", "telefon", "email", "iban",
    "versicherungsnummer", "steuer_id", "ausweisnummer", "kennzeichen", "sonstiges", "benutzer",
]

# KI-Kategorie -> Einstellungs-Kategorie (für die Ein-/Ausschalter)
SETTINGS_CATEGORY = {
    "name": "name", "adresse": "adresse", "geburtsdatum": "geburtsdatum", "geburtsort": "geburtsdatum",
    "telefon": "telefon", "email": "email", "iban": "iban", "versicherungsnummer": "versicherung",
    "steuer_id": "sonstiges", "ausweisnummer": "sonstiges", "kennzeichen": "sonstiges",
    "sonstiges": "sonstiges", "benutzer": "benutzer",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "funde": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kategorie": {"type": "string", "enum": AI_CATEGORIES},
                    "bezug": {"type": "string"},
                    "unsicher": {"type": "boolean"},
                    "hinweis": {"type": "string"},
                },
                "required": ["text", "kategorie", "bezug", "unsicher", "hinweis"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["funde"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Du bist Experte für die Anonymisierung von Schriftstücken einer deutschen Rechtsanwaltskanzlei (Datenschutz/DSGVO).
Du erhältst den Text EINER Seite. Er stammt meist aus einer Texterkennung (OCR) eingescannter Papierakten und kann Erkennungsfehler, falsche Zeilenumbrüche und zerrissene Wörter enthalten.

AUFGABE: Finde ALLE Angaben, mit denen eine natürliche Person identifiziert werden kann. Lieber eine Angabe zu viel als eine zu wenig – übersehene Daten sind der schlimmste Fehler.

Kategorien:
- name: Vor- und Nachnamen natürlicher Personen – auch einzeln (nur Nachname oder nur Vorname), Doppelnamen, Geburts-/Ehenamen, Namen in Briefköpfen, Anschriftenfeldern, Grußformeln und Unterschriftszeilen, Initial mit Nachname ("R. Kinzel").
- adresse: Straße mit Hausnummer, Postleitzahl mit Ort, Postfach einer Privatperson.
- geburtsdatum: Geburtsdaten.
- geburtsort: Geburtsorte.
- telefon: Telefon-, Handy- und Faxnummern.
- email: E-Mail-Adressen.
- iban: IBAN und Kontonummern.
- versicherungsnummer: Sozialversicherungs-, Renten-, Krankenversicherten-, Versicherungsschein- und Mitgliedsnummern.
- steuer_id: Steuer-Identifikationsnummern und Steuernummern von Personen.
- ausweisnummer: Personalausweis-, Reisepass-, Führerscheinnummern.
- kennzeichen: Kfz-Kennzeichen.
- sonstiges: andere eindeutig auf eine Person verweisende Angaben (z. B. Personalnummer, Kundennummer einer Privatperson).
- benutzer: Begriffe aus der Liste "Zusätzliche Suchbegriffe" – auch abgewandelte oder fehlerhaft erkannte Schreibweisen.

NICHT angeben:
- Gerichte, Behörden, Firmen, Banken, Versicherungen, Kanzleien als solche (z. B. "Amtsgericht Limburg", "Allianz AG"). Steckt in einem Firmennamen der Name einer Person, nur den Personennamen angeben.
- Rollen und Anreden: Kläger, Beklagter, Mandant, Zeuge, Sachverständiger, Herr, Frau, Dr.
- Gesetze, Paragraphen, Aktenzeichen, Geldbeträge, gewöhnliche Datumsangaben (Schreiben vom …, Zahlungs-, Vertrags-, Unfalldaten, Fristen, Termine). Ein Datum ist nur dann ein Geburtsdatum, wenn es eindeutig als Geburtsdatum erkennbar ist.
- Orte ohne Bezug zu einer Privatanschrift (Gerichtsort, "in Limburg").
{professionals}
Für jeden Fund:
- "text": die Fundstelle WÖRTLICH wie im Text (auch mit OCR-Fehlern – nichts korrigieren, nichts ergänzen). Nur die zu schwärzende Angabe ohne Anrede oder Titel ("Kinzel", nicht "Herr Kinzel"). Jede unterschiedliche Schreibweise einmal angeben.
- "kategorie": eine der Kategorien oben.
- "bezug": wem die Angabe gehört: "Mandant" bzw. "Gegner" NUR, wenn es genau die oben genannte Person ist; bei allen anderen Personen deren vollständiger Name (z. B. "Erika Mustermann"); bei Kategorie benutzer der zugehörige Suchbegriff; sonst "".
- "unsicher": true, wenn ein Mensch entscheiden sollte, ob die Stelle geschwärzt wird – z. B. Richter, Anwälte, Notare oder Behördenmitarbeiter in dienstlicher Funktion; Firmen- oder Kanzleinamen, die einen Personennamen enthalten; Orte, bei denen unklar ist, ob es ein Wohnort ist; Angaben, bei denen unklar ist, ob sie eine natürliche Person betreffen. Sonst false. Im Zweifel die Stelle trotzdem angeben und unsicher=true setzen.
- "hinweis": bei unsicher=true eine kurze Begründung (höchstens 8 Wörter, z. B. "Rechtsanwalt der Gegenseite, dienstlich genannt"), sonst "".

Beispiel (Mandant: Robin Kinzel):
Text: "Sehr geehrter Herr Dr. Kinzel, Ihre Nachbarin Frau Erika Mustermann (geb. 05.05.1960), Hauptstr. 3, 12345 Musterstadt, Tel. 0171 2345678, hat am 01.02.2024 beim Amtsgericht Limburg (Az. 2 C 123/24) Klage erhoben. Gez. E. Mustermann"
Antwort: {"funde":[{"text":"Kinzel","kategorie":"name","bezug":"Mandant","unsicher":false,"hinweis":""},{"text":"Erika Mustermann","kategorie":"name","bezug":"Erika Mustermann","unsicher":false,"hinweis":""},{"text":"05.05.1960","kategorie":"geburtsdatum","bezug":"Erika Mustermann","unsicher":false,"hinweis":""},{"text":"Hauptstr. 3","kategorie":"adresse","bezug":"Erika Mustermann","unsicher":false,"hinweis":""},{"text":"12345 Musterstadt","kategorie":"adresse","bezug":"Erika Mustermann","unsicher":false,"hinweis":""},{"text":"0171 2345678","kategorie":"telefon","bezug":"Erika Mustermann","unsicher":false,"hinweis":""},{"text":"E. Mustermann","kategorie":"name","bezug":"Erika Mustermann","unsicher":false,"hinweis":""}]}
Wäre im Text zusätzlich "Rechtsanwalt Dr. Hans Meier" als Vertreter genannt: {"text":"Hans Meier","kategorie":"name","bezug":"Hans Meier","unsicher":true,"hinweis":"Rechtsanwalt, nur dienstlich genannt"}

Antworte ausschließlich mit JSON im Format {"funde": [...]}. Wenn nichts zu finden ist: {"funde": []}."""

PROFESSIONALS_RULE = ("- Namen von Richterinnen/Richtern, Rechtsanwältinnen/Rechtsanwälten, Notaren sowie "
                      "Gerichts- und Behördenmitarbeitern, die nur in dienstlicher Funktion genannt werden.\n")

_REJECT = {
    "kläger", "klägerin", "beklagte", "beklagter", "mandant", "mandantin", "gegner", "gegnerin",
    "zeuge", "zeugin", "herr", "herrn", "frau", "dr", "antragsteller", "antragstellerin",
    "antragsgegner", "antragsgegnerin", "sachverständiger", "rechtsanwalt", "rechtsanwältin",
    "unterschrift", "name", "vorname", "nachname", "anschrift", "adresse", "geburtsdatum",
}

MAX_CHARS = 6000


@dataclass
class Finding:
    text: str
    kategorie: str
    bezug: str
    unsicher: bool = False
    hinweis: str = ""


def _chunks(text: str, limit: int = MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur)
    return out


class AIDetector:
    def __init__(self, client: ChatClient, registry: PersonRegistry, inputs: UserInputs,
                 exclude_professionals: bool = False) -> None:
        self.client = client
        self.registry = registry
        self.inputs = inputs
        self.system = SYSTEM_PROMPT.replace(
            "{professionals}", PROFESSIONALS_RULE if exclude_professionals else "")

    def _context(self) -> str:
        lines = ["Bekannte Personen:"]
        m = split_values(self.inputs.mandant_name)
        g = split_values(self.inputs.gegner_name)
        lines.append(f"- Mandant: {'; '.join(m) if m else '(nicht angegeben)'}")
        if self.inputs.mandant_adresse.strip():
            lines.append(f"  Anschrift Mandant: {self.inputs.mandant_adresse.strip()}")
        lines.append(f"- Gegner: {'; '.join(g) if g else '(nicht angegeben)'}")
        if self.inputs.gegner_adresse.strip():
            lines.append(f"  Anschrift Gegner: {self.inputs.gegner_adresse.strip()}")
        others = [p for p in self.registry.persons]
        if others:
            lines.append("- Bereits gefundene weitere Personen (bei Bezug genau so schreiben): "
                         + "; ".join(p.display for p in others[:40]))
        terms = self.inputs.custom_terms()
        if terms:
            lines.append("Zusätzliche Suchbegriffe:")
            for term, label in terms:
                lines.append(f'- "{term}" (wird ersetzt durch "{label}")')
        return "\n".join(lines)

    def analyze_page(self, page: PageData, page_no: int, total: int, doc_name: str = "") -> list[Finding]:
        text = page.text.strip()
        if len(text) < 3:
            return []
        findings: list[Finding] = []
        seen: set[tuple[str, str]] = set()
        for chunk in _chunks(text):
            user = (f"{self._context()}\n\nSeite {page_no} von {total}"
                    f"{' (' + doc_name + ')' if doc_name else ''}:\n\"\"\"\n{chunk}\n\"\"\"")
            data = self.client.chat_json(self.system, user, SCHEMA)
            for item in data.get("funde", []) if isinstance(data, dict) else []:
                f = self._clean(item)
                if f and (key := (f.text, f.kategorie)) not in seen:
                    seen.add(key)
                    findings.append(f)
        return findings

    @staticmethod
    def _clean(item: object) -> Finding | None:
        if not isinstance(item, dict):
            return None
        text = " ".join(str(item.get("text", "")).split())
        kat = str(item.get("kategorie", "")).strip().lower()
        bezug = " ".join(str(item.get("bezug", "")).split())
        unsicher = item.get("unsicher", False)
        unsicher = unsicher if isinstance(unsicher, bool) else str(unsicher).lower() in ("true", "ja", "1")
        hinweis = " ".join(str(item.get("hinweis", "") or "").split())[:120]
        if kat not in AI_CATEGORIES:
            kat = "sonstiges"
            unsicher, hinweis = True, hinweis or "Kategorie unklar"
        n = norm(text)
        if len(n) < 2 or n in _REJECT:
            return None
        if kat == "name":
            # Anreden, Titel und Rollen vorne abschneiden ("Zeugin Dr. Petra Muster" -> "Petra Muster")
            words = text.split()
            while words and norm(words[0]) in NAME_STOP:
                words.pop(0)
            text = " ".join(words)
            if any(c.isdigit() for c in text) or len(norm(text)) < 2:
                return None
            # Namen werden großgeschrieben; Gerichte/Firmen/Behörden sind keine Personen
            if not any(c.isupper() for c in text) or is_organisation(text):
                return None
        return Finding(text, kat, bezug, unsicher, hinweis if unsicher else "")


__all__ = ["AIDetector", "Finding", "AIClientError", "SETTINGS_CATEGORY"]
