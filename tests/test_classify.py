from airspace.classify import Classification, classify, classify_many


class FakeRegistry:
    def __init__(self, records):
        self.records = records
        self.asked = []

    def lookup(self, ids):
        self.asked.extend(ids)
        return {i: self.records[i] for i in ids if i in self.records}


def test_order():
    assert classify("A7") == Classification("rotorcraft", "adsb_category")
    assert classify("C2").kind == "surface"
    assert classify("A1", {"faa_type": "6"}).kind == "rotorcraft"
    assert classify("A1", {"faa_type": "6"}).rule == "faa_registry"
    assert classify(None, {"faa_type": "9"}).kind == "gyroplane"
    assert classify("A3", {"faa_type": "5"}).kind == "aircraft"
    assert classify(None).kind == "unknown"
    assert classify("A0").kind == "unknown"


def test_registry_only_for_unsettled():
    reg = FakeRegistry({"a1": {"faa_type": "6"}})
    result = classify_many({"a1": None, "h1": "A7", "v1": "C1"}, reg)
    assert reg.asked == ["a1"]
    assert result["a1"].kind == "rotorcraft" and result["a1"].rule == "faa_registry"
