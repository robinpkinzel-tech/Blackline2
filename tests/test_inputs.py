from blackline2.detect_inputs import UserInputs, address_parts, detect_inputs, is_organisation, split_values
from blackline2.labels import PersonRegistry, short_label
from blackline2.settings import CATEGORIES


def run(page, inputs):
    reg = PersonRegistry()
    reg.set_parties(split_values(inputs.mandant_name), split_values(inputs.gegner_name))
    return [(h.label, h.text) for h in detect_inputs(page, inputs, reg, set(CATEGORIES))]


def test_mandant_full_and_parts(page_factory):
    p = page_factory("An Herrn Robin Kinzel, Musterweg 15, 12345 Musterstadt\nSehr geehrter Herr Kinzel,\nR. Kinzel")
    res = run(p, UserInputs("Robin Kinzel", "Musterweg 15, 12345 Musterstadt"))
    assert ("Mandant", "Robin Kinzel") in res
    assert ("Mandant", "Kinzel") in res
    assert ("Mandant", "R. Kinzel") in res
    assert ("Adresse Mandant", "Musterweg 15") in res
    assert ("Adresse Mandant", "12345 Musterstadt") in res


def test_gegner_and_custom_terms(page_factory):
    p = page_factory("Die Beklagte Anna Schmidt arbeitet bei Volkswagen in Wolfsburg.")
    res = run(p, UserInputs(gegner_name="Anna Schmidt", custom=[("Volkswagen", "Arbeitgeber"), ("Wolfsburg", "")]))
    assert ("Gegner", "Anna Schmidt") in res
    assert ("Arbeitgeber", "Volkswagen") in res
    assert ("geschwärzt", "Wolfsburg") in res


def test_shared_surname_gets_combined_label(page_factory):
    p = page_factory("Robin Kinzel und Anna Kinzel. Herr Kinzel erklärte")
    res = run(p, UserInputs("Robin Kinzel", gegner_name="Anna Kinzel"))
    assert ("Mandant", "Robin Kinzel") in res
    assert ("Gegner", "Anna Kinzel") in res
    assert ("Mandant/Gegner", "Kinzel") in res


def test_organisation_not_split(page_factory):
    assert is_organisation("Deutsche Rentenversicherung Bund")
    p = page_factory("Die Deutsche Bahn und der Bund")
    res = run(p, UserInputs(gegner_name="Deutsche Rentenversicherung Bund"))
    assert res == []


def test_multiple_values_and_address_parts():
    assert split_values("Robin Kinzel; Robin Müller\nR. K.") == ["Robin Kinzel", "Robin Müller", "R. K."]
    assert address_parts("Musterweg 15, 12345 Musterstadt") == ["Musterweg 15", "12345 Musterstadt"]


def test_short_labels():
    assert short_label("Mandant") == "Mdt."
    assert short_label("Person C") == "P. C"
    assert short_label("Adresse Person B") == "Adr. B"


def test_registry_resolves_name_variants():
    reg = PersonRegistry()
    reg.set_parties(["Robin Kinzel"], [])
    assert reg.resolve("Kinzel").label == "Mandant"
    a = reg.resolve("Erika Mustermann")
    assert a.label == "Person A"
    assert reg.resolve("Frau Mustermann") is a
    assert reg.resolve("Erika Maria Mustermann") is a
    assert reg.resolve("Jens Beispiel").label == "Person B"
