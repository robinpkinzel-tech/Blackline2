from blackline2.matching import PageIndex, norm, tokens_match


def texts(page, results):
    return [page.segment_text(r) for r in results]


def test_norm():
    assert norm("„Kinzel,“") == "kinzel"
    assert norm("Hauptstraße") == "hauptstr"
    assert norm("Hauptstr.") == "hauptstr"
    assert norm("Hauptstrasse") == "hauptstr"


def test_fuzzy_tokens():
    assert tokens_match("kinzel", "kinzei")          # OCR: l -> i
    assert tokens_match("kinzel", "kinzel", fuzzy=False)
    assert tokens_match("meier", "maier")             # 1 Fehler bei 5 Zeichen erlaubt
    assert not tokens_match("max", "mai")             # kurze Wörter nur exakt
    assert tokens_match("15", "l5")                   # Ziffern: OCR-Verwechslung
    assert not tokens_match("65589", "65588")         # andere PLZ


def test_phrase_with_ocr_error_and_punctuation(page_factory):
    p = page_factory("An Herrn Robin Kinzei, Musterweg 15")
    idx = PageIndex(p)
    assert texts(p, idx.find("Robin Kinzel")) == ["Robin Kinzei,"]


def test_hyphenation_across_lines(page_factory):
    p = page_factory("Der Kläger Robin Kin-\nzel trägt vor")
    idx = PageIndex(p)
    assert texts(p, idx.find("Robin Kinzel")) == ["Robin Kin-\nzel"]


def test_split_and_glued_words(page_factory):
    p = page_factory("Herr RobinKinzel und Kin zel")
    idx = PageIndex(p)
    assert "RobinKinzel" in texts(p, idx.find("Robin Kinzel"))
    assert "Kin zel" in texts(p, idx.find("Kinzel"))


def test_possessive_and_capital(page_factory):
    p = page_factory("Kinzels Auto. Der kinzel-Test")
    idx = PageIndex(p)
    assert texts(p, idx.find(["kinzel"], possessive=True, require_capital=True)) == ["Kinzels"]


def test_address_street_variants(page_factory):
    p = page_factory("wohnhaft Bahnhofstr. 7, 35578 Wetzlar")
    idx = PageIndex(p)
    assert texts(p, idx.find("Bahnhofstraße 7")) == ["Bahnhofstr. 7,"]
    assert texts(p, idx.find("35578 Wetzlar")) == ["35578 Wetzlar"]
