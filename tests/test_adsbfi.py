import json
from datetime import UTC, datetime, timedelta

import pytest

from airspace.models import PositionTimeBasis, SchemaError
from airspace.providers.adsbfi import AdsbFi

RECEIVED = datetime(2026, 10, 17, 18, 18, 18, 300_000, tzinfo=UTC)


def body(aircraft, now=1792261098001):
    return json.dumps({"ac": aircraft, "msg": "No error", "now": now, "total": len(aircraft)}).encode()


def parse(aircraft, **kw):
    return AdsbFi().parse(body(aircraft, **kw), RECEIVED)


def test_captured_fixture(fixture_bytes):
    parsed = AdsbFi().parse(fixture_bytes, RECEIVED)
    assert len(parsed.observations) == 54
    assert parsed.rejections == []
    assert parsed.provider_at == datetime.fromtimestamp(1792261098.001, UTC)

    by_id = {o.aircraft_id: o for o in parsed.observations}
    heli = by_id["a1f48f"]
    assert heli.registration == "N225LA"
    assert heli.callsign == "N225LA"  # padding stripped
    assert heli.category == "A7"
    assert heli.altitude_baro_ft == 1575 and heli.on_ground is False
    assert heli.position_source == "adsb_icao"
    assert heli.position_time_basis is PositionTimeBasis.DERIVED_AGE
    assert heli.position_age_s == pytest.approx(0.372, abs=1e-3)

    tisb = by_id["~298a99"]  # non-ICAO prefix kept
    assert tisb.position_source == "tisb_trackfile"
    assert tisb.registration is None and tisb.category is None

    grounded = [o for o in parsed.observations if o.on_ground]
    assert len(grounded) == 33
    assert all(o.altitude_baro_ft is None for o in grounded)


def test_zero_values_preserved():
    (obs,) = parse([{"hex": "abc123", "lat": 34.0, "lon": -118.4, "alt_baro": 0, "gs": 0, "track": 0,
                     "baro_rate": 0, "seen_pos": 0}]).observations
    assert obs.altitude_baro_ft == 0 and obs.on_ground is False
    assert obs.ground_speed_kt == 0 and obs.track_deg == 0 and obs.vertical_rate_fpm == 0
    assert obs.position_age_s == 0


def test_track_360_normalized():
    (obs,) = parse([{"hex": "abc123", "lat": 34.0, "lon": -118.4, "track": 360}]).observations
    assert obs.track_deg == 0


def test_missing_optional_fields():
    (obs,) = parse([{"hex": "ABC123", "lat": 34.0, "lon": -118.4, "flight": "   "}]).observations
    assert obs.aircraft_id == "abc123"
    assert obs.callsign is None
    assert obs.on_ground is None and obs.altitude_baro_ft is None
    assert obs.position_at is None and obs.position_time_basis is PositionTimeBasis.UNKNOWN


def test_geom_rate_fallback():
    (obs,) = parse([{"hex": "abc123", "lat": 34.0, "lon": -118.4, "geom_rate": -512}]).observations
    assert obs.vertical_rate_fpm == -512


def test_future_position_flagged():
    (obs,) = parse([{"hex": "abc123", "lat": 34.0, "lon": -118.4, "seen_pos": -30}]).observations
    assert obs.time_flag == "future_position"
    assert obs.position_at == obs.provider_at + timedelta(seconds=30)


def test_malformed_records_quarantined_individually():
    parsed = parse([
        {"hex": "good01", "lat": 34.0, "lon": -118.4},
        {"lat": 34.0, "lon": -118.4},                      # no hex
        {"hex": "bad002", "lat": -118.4, "lon": 34.0},     # reversed coordinates
        {"hex": "bad003", "lat": "north", "lon": -118.4},
        {"hex": "bad004", "lat": 34.0},                    # no position
        "not a record",
    ])
    assert [o.aircraft_id for o in parsed.observations] == ["good01"]
    assert len(parsed.rejections) == 5
    assert "hex" in parsed.rejections[0].reason


def test_empty_sky_is_a_valid_response():
    parsed = parse([])
    assert parsed.observations == [] and parsed.rejections == []


@pytest.mark.parametrize(
    "raw",
    [b"<html>oops</html>", b"[]", b'{"ac": null, "now": 1}', b'{"ac": []}', b'{"ac": [], "now": "soon"}'],
)
def test_schema_errors(raw):
    with pytest.raises(SchemaError):
        AdsbFi().parse(raw, RECEIVED)


def test_url_respects_radius_cap():
    assert AdsbFi().url(34.0, -118.42, 10).endswith("/lat/34.000000/lon/-118.420000/dist/10")
    with pytest.raises(ValueError):
        AdsbFi().url(34.0, -118.42, 251)
