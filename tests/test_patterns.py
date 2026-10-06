import pytest

from blackline2.detect_patterns import detect_patterns, iban_valid
from blackline2.settings import CATEGORIES

ALL = set(CATEGORIES)


def found(page_factory, text):
    return [(h.label, h.text) for h in detect_patterns(page_factory(text), ALL)]


@pytest.mark.parametrize("text,expected", [
    ("E-Mail: robin.kinzel@web.de", "robin.kinzel@web.de"),
    ("Mail an max.mustermann@kanzlei-beispiel.de.", "max.mustermann@kanzlei-beispiel.de"),
    ("info @ example.com bitte", "info @ example.com"),
])
def test_email(page_factory, text, expected):
    assert ("E-Mail", expected) in found(page_factory, text)


@pytest.mark.parametrize("text,label,value", [
    ("Tel.: 06433/123456", "Telefon", "06433/123456"),
    ("Telefon 06433 9452-0", "Telefon", "06433 9452-0"),
    ("Fax: (06433) 9452-20", "Fax", "(06433) 9452-20"),
    ("Mobil: 0171 1234567", "Handy", "0171 1234567"),
    ("erreichbar unter +49 171 1234567", "Handy", "+49 171 1234567"),
    ("Tel +49 (0) 6431 12 34 56", "Telefon", "+49 (0) 6431 12 34 56"),
])
def test_phone(page_factory, text, label, value):
    assert (label, value) in found(page_factory, text)


@pytest.mark.parametrize("text", [
    "Schreiben vom 01.01.2024",
    "Az. 012345/2024",
    "Steuernummer 012/345/67890",
    "Betrag 1.000,00 EUR",
    "12345 Musterstadt",
    "Urteil des LG Limburg 2 O 123/24",
])
def test_phone_no_false_positive(page_factory, text):
    assert not [f for f in found(page_factory, text) if f[0] in ("Telefon", "Handy", "Fax")]


def test_iban_checksum():
    assert iban_valid("DE89370400440532013000")
    assert not iban_valid("DE89370400440532013001")


@pytest.mark.parametrize("text,value", [
    ("IBAN: DE89 3704 0044 0532 0130 00", "DE89 3704 0044 0532 0130 00"),
    ("IBAN DE89370400440532013000 BIC COBADEFFXXX", "DE89370400440532013000"),
    ("Konto AT61 1904 3002 3457 3201", "AT61 1904 3002 3457 3201"),
])
def test_iban(page_factory, text, value):
    res = found(page_factory, text)
    assert ("IBAN", value) in res
    assert not [f for f in res if f[0] in ("Telefon", "Handy")]


def test_iban_with_ocr_error_still_redacted(page_factory):
    # Prüfziffer falsch (OCR), Format eindeutig deutsch -> trotzdem schwärzen
    assert ("IBAN", "DE89 3704 0044 0532 0130 01") in found(page_factory, "IBAN: DE89 3704 0044 0532 0130 01")


@pytest.mark.parametrize("text,label,value", [
    ("Rentenversicherungsnummer 12 150380 M 015", "SV-Nr.", "12 150380 M 015"),
    ("Ihre SV-Nr.: 12150380M015", "SV-Nr.", "12150380M015"),
    ("Versichertennummer: A123456789", "Vers.-Nr.", "A123456789"),
    ("Versicherungsschein-Nr. 4711-0815/23 vom 01.01.2023", "Vers.-Nr.", "4711-0815/23"),
    ("Mitgliedsnummer 123 456 789", "Vers.-Nr.", "123 456 789"),
])
def test_insurance(page_factory, text, label, value):
    assert (label, value) in found(page_factory, text)


@pytest.mark.parametrize("text,value", [
    ("Robin Kinzel, geb. 01.01.2001, wohnhaft", "01.01.2001"),
    ("geb. am 1.1.2001", "1.1.2001"),
    ("geboren am 3. März 1980 in Limburg", "3. März 1980"),
    ("Geburtsdatum: 24.12.1975", "24.12.1975"),
    ("Max Muster, * 05.06.1970", "05.06.1970"),
])
def test_birthdate(page_factory, text, value):
    assert ("Geb.-Datum", value) in found(page_factory, text)


def test_birthplace_and_birthname(page_factory):
    res = found(page_factory, "Erika Muster geb. Beispiel, geboren am 3. März 1980 in Limburg")
    assert ("Geburtsname", "Beispiel") in res
    assert ("Geb.-Ort", "Limburg") in res


def test_ordinary_date_not_redacted(page_factory):
    assert found(page_factory, "Ihr Schreiben vom 01.01.2024 haben wir erhalten.") == []


def test_categories_can_be_disabled(page_factory):
    p = page_factory("Tel.: 06433/123456, E-Mail: a.b@web.de")
    hits = detect_patterns(p, {"email"})
    assert [h.label for h in hits] == ["E-Mail"]
