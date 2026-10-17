from datetime import UTC, datetime, timedelta

from airspace import report
from airspace.geometry import METERS_PER_MILE, destination
from airspace.models import ErrorKind, Observation, PollResult, PositionTimeBasis

T0 = datetime(2026, 10, 17, 18, 0, tzinfo=UTC)
HOME = (34.0, -118.42)


def obs(t, aircraft_id, *, age=1.0, miles=2.0, **kw):
    lat, lon = destination(*HOME, 0, miles * METERS_PER_MILE)
    return Observation(
        provider="adsbfi", aircraft_id=aircraft_id, received_at=t, provider_at=t,
        position_at=t - timedelta(seconds=age), position_time_basis=PositionTimeBasis.DERIVED_AGE,
        latitude=lat, longitude=lon, **kw,
    )


def record(store, sid, t, observations, error_kind=None):
    store.record_poll(sid, PollResult(
        provider="adsbfi", requested_at=t, received_at=t, latency_ms=100, status_code=200 if not error_kind else 503,
        provider_at=t if not error_kind else None, observations=observations,
        error_kind=error_kind, error="HTTP 503" if error_kind else None,
    ))


def build_session(store):
    sid = store.start_session("adsbfi", "collect", {})
    heli = dict(category="A7", registration="N225LA", track_deg=77.0, altitude_baro_ft=1500)
    for i in range(20):
        t = T0 + timedelta(seconds=10 * i)
        if i == 7:
            record(store, sid, t, [], ErrorKind.TRANSIENT)
            continue
        observations = [obs(t, "jet01", age=40 if i % 2 == 0 else 1, miles=8, category="A3")]
        if not 10 <= i < 15:  # helicopter drops out for 50 seconds
            observations.append(obs(t, "a1f48f", **heli))
            observations.append(obs(t, "~tisb01", position_source="tisb_trackfile", track_deg=78.0,
                                    altitude_baro_ft=1525))
        observations.append(obs(t, "veh01", category="C2", on_ground=True))
        record(store, sid, t, observations)
    store.record_gap(sid, "sleep_gap", 600)
    return sid


def test_report_metrics(config, store):
    sid = build_session(store)
    data = report.build(store, sid, config)
    req, pos, ac = data["requests"], data["positions"], data["aircraft"]

    assert req["scheduled"] == 20 and req["succeeded"] == 19
    assert req["errors"] == {"HTTP 503": 1}
    assert req["sleep_gaps"] == 1 and req["sleep_gap_s"] == 600

    assert ac["surface_excluded"] == 1
    assert ac["unique"] == 3  # jet, helicopter, TIS-B track
    assert ac["unique_visible"] == 2  # jet is 8 miles out
    assert pos["age_s_p95"] > 20  # the jet's 40-second-old positions
    assert pos["airborne_visible_age_s_p95"] == 1  # the jet is outside the visible area

    (heli,) = data["rotorcraft"]
    assert heli["registration"] == "N225LA" and heli["rule"] == "adsb_category"
    assert heli["max_interval_s"] == 60
    assert 0 < heli["continuity"] < 1

    assert data["tisb_duplicates"][0]["tisb_id"] == "~tisb01"
    assert data["tisb_duplicates"][0]["aircraft_id"] == "a1f48f"
    assert data["gaps"]["count"] >= 1

    gate = report.gate(data, config)
    assert gate["requests"].startswith("PASS")
    assert gate["position_age"].startswith("PASS")
    assert gate["helicopter"].startswith("review")

    text = report.render(data, config)
    assert "N225LA" in text and "Possible TIS-B duplicates" in text


def test_gate_untested_without_rotorcraft(config, store):
    sid = store.start_session("adsbfi", "collect", {})
    record(store, sid, T0, [obs(T0, "jet01", category="A3")])
    data = report.build(store, sid, config)
    assert report.gate(data, config)["helicopter"].startswith("untested")
    report.render(data, config)


def test_empty_session_renders(config, store):
    sid = store.start_session("adsbfi", "collect", {})
    data = report.build(store, sid, config)
    assert report.gate(data, config)["requests"] == "n/a"
    report.render(data, config)


def test_parked_aircraft_do_not_fail_age_gate(config, store):
    sid = store.start_session("adsbfi", "collect", {})
    for i in range(10):
        t = T0 + timedelta(seconds=10 * i)
        record(store, sid, t, [
            obs(t, "parked", age=45, category="A3", on_ground=True),
            obs(t, "flying", age=1, category="A1", on_ground=False),
        ])
    data = report.build(store, sid, config)
    assert data["positions"]["age_s_p95"] == 45
    assert report.gate(data, config)["position_age"].startswith("PASS")


def test_sleep_spanning_intervals_excluded(config, store):
    sid = store.start_session("adsbfi", "collect", {})
    for i in range(10):
        t = T0 + timedelta(seconds=10 * i)
        record(store, sid, t, [obs(t, "heli", category="A7")])
    # Host slept for 10 minutes, then the helicopter is seen again
    wake = T0 + timedelta(seconds=90 + 600)
    store.conn.execute(
        "INSERT INTO polls (session_id, kind, requested_at, gap_s) VALUES (?, 'sleep_gap', ?, 600)",
        (sid, wake.isoformat(timespec="milliseconds")),
    )
    store.conn.commit()
    for i in range(5):
        t = wake + timedelta(seconds=10 * i)
        record(store, sid, t, [obs(t, "heli", category="A7")])
    data = report.build(store, sid, config)
    (heli,) = data["rotorcraft"]
    assert heli["sleep_spanning"] == 1
    assert heli["max_interval_s"] == 10 and heli["continuity"] == 1
    assert data["requests"]["awake_share"] < 0.5


def test_tisb_duplicates_only_for_track_files(config, store):
    sid = store.start_session("adsbfi", "collect", {})
    t = T0
    record(store, sid, t, [
        obs(t, "a00001", category="A1", track_deg=220.0, altitude_baro_ft=400),
        obs(t, "a00002", category="A1", track_deg=222.0, altitude_baro_ft=300, position_source="tisb_icao"),
    ])
    assert report.build(store, sid, config)["tisb_duplicates"] == []
