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


def test_initials_from_names():
    from blackline2.labels import initials
    assert initials("Robin Kinzel") == "R.K."
    assert initials("Kinzel, Robin") == "R.K."
    assert initials("Dr. Erika Maria Mustermann") == "E.M.M."
    assert initials("Ursula von der Leyen") == "U.v.d.L."
    assert initials("Anna-Lena Schmidt-Weber") == "A.-L.S.-W."
    assert initials("Herrn Müller") == "M."
    assert initials("R. Kinzel") == "R.K."
    assert initials("Gül Yilmaz") == "G.Y."
    assert short_label("R.K.") == "R.K."
    assert short_label("Adresse R.K.") == "Adr. R.K."
    assert short_label("U.v.d.L.") == "U.v.d.L."


def test_registry_assigns_initials_and_resolves_clashes():
    from blackline2.labels import relabel
    reg = PersonRegistry()
    reg.set_parties(["Max Mandant"], ["Gerd Gegner"])
    for n in ("Robin Kinzel", "Rita Klein", "Hans Müller", "Hanna Müller", "Jens Beispiel"):
        reg.resolve(n)
    mapping = reg.assign_initials()
    assert [p.label for p in reg.persons] == ["R.Ki.", "R.Kl.", "H.M.", "H.M. (2)", "J.B."]
    assert reg.mandant.label == "Mandant" and reg.gegner.label == "Gegner"
    assert mapping["Adresse Person E"] == "Adresse J.B."
    assert relabel("Person C/Person D", mapping) == "H.M./H.M. (2)"
    assert reg.assign_initials() == {}  # stabil
    # später bekannter vollständiger Name verfeinert das Kürzel
    reg2 = PersonRegistry()
    p = reg2.resolve("Mustermann")
    reg2.assign_initials()
    assert p.label == "M."
    p.add_name("Erika Mustermann")  # z. B. später im Dokument vollständig genannt
    assert reg2.assign_initials() == {"M.": "E.M.", "Adresse M.": "Adresse E.M."}
    # vom Nutzer umbenannte Personen behalten ihr Kürzel
    reg2.rename("E.M.", "Zeugin")
    assert reg2.assign_initials() == {} and p.label == "Zeugin"
