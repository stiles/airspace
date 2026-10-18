import math
from datetime import timedelta

import pytest
from helpers import HOME, T0

from airspace.behaviors import CIRCLING, HOVERING, LOW, Point, current, detect
from airspace.config import Behavior
from airspace.geometry import destination

PARAMS = Behavior()
GAP = 30.0


def point(seconds, bearing, meters, *, center=HOME, alt=600, speed=60.0, on_ground=False):
    lat, lon = destination(*center, bearing, meters)
    return Point(T0 + timedelta(seconds=seconds), lat, lon, alt, speed, on_ground)


def orbit(start_s, turns, *, radius=200.0, step_s=2.0, period_s=60.0, center=HOME, direction=1, **kw):
    n = int(turns * period_s / step_s)
    return [
        point(start_s + k * step_s, (direction * 360 * k * step_s / period_s) % 360, radius, center=center, **kw)
        for k in range(n + 1)
    ]


def line(start_s, seconds, *, step_s=2.0, speed_mps=40.0, bearing=90.0, origin_m=0.0, **kw):
    return [point(start_s + k * step_s, bearing, origin_m + speed_mps * k * step_s, **kw) for k in range(int(seconds / step_s) + 1)]


def kinds(episodes):
    return [e.kind for e in episodes]


def test_orbit_is_one_circling_episode_with_center_radius_and_turns():
    (e,) = detect(orbit(0, 5), PARAMS, gap_s=GAP, rotorcraft=True)
    assert e.kind == CIRCLING and e.duration_s == 300
    assert e.turns == pytest.approx(5, abs=0.2)
    assert e.radius_m == pytest.approx(200, rel=0.05)
    assert math.isclose(e.latitude, HOME[0], abs_tol=1e-4) and math.isclose(e.longitude, HOME[1], abs_tol=1e-4)


def test_sparse_ten_second_orbit_still_counts():
    # About 120 degrees between points: what 10-second polling gave us
    (e,) = detect(orbit(0, 6, step_s=10, period_s=30), PARAMS, gap_s=GAP, rotorcraft=True)
    assert e.kind == CIRCLING and e.turns >= 5


def test_straight_line_and_gentle_turns_are_not_circling():
    assert detect(line(0, 300), PARAMS, gap_s=GAP, rotorcraft=True) == []
    # Half an orbit, then away: a 180-degree turn isn't circling
    half = orbit(0, 0.5)
    assert CIRCLING not in kinds(detect(half, PARAMS, gap_s=GAP, rotorcraft=True))


def test_orbit_wider_than_the_limit_is_not_circling():
    wide = orbit(0, 2, radius=2000, period_s=400)
    assert detect(wide, PARAMS, gap_s=GAP, rotorcraft=True) == []


def test_orbits_over_two_spots_are_two_episodes():
    far = destination(*HOME, 90, 3000)
    track = orbit(0, 3) + line(182, 60, origin_m=200, speed_mps=45) + orbit(244, 3, center=far)
    episodes = [e for e in detect(track, PARAMS, gap_s=GAP, rotorcraft=True) if e.kind == CIRCLING]
    assert len(episodes) == 2
    assert all(e.radius_m < 300 for e in episodes)


def test_reversing_direction_keeps_the_larger_turn_count():
    track = orbit(0, 3) + orbit(182, 1, direction=-1)
    (e,) = [e for e in detect(track, PARAMS, gap_s=GAP, rotorcraft=True) if e.kind == CIRCLING]
    assert e.turns >= 2.9


def test_coverage_gap_splits_episodes():
    track = orbit(0, 2) + orbit(200, 2)  # 80 s without positions
    assert kinds(detect(track, PARAMS, gap_s=GAP, rotorcraft=True)) == [CIRCLING, CIRCLING]


def test_hovering_needs_slow_speed_small_drift_and_time():
    hover = [point(k * 2, 0, 5 * (k % 3), speed=3.0) for k in range(46)]  # 90 s
    (e,) = detect(hover, PARAMS, gap_s=GAP, rotorcraft=True)
    assert e.kind == HOVERING and e.duration_s == 90
    assert detect(hover[:20], PARAMS, gap_s=GAP, rotorcraft=True) == []  # 38 s
    drifting = [point(k * 2, 0, 15 * k, speed=10.0) for k in range(46)]  # slow but moving 675 m
    assert detect(drifting, PARAMS, gap_s=GAP, rotorcraft=True) == []
    # Hovering is a helicopter behavior
    assert detect(hover, PARAMS, gap_s=GAP, rotorcraft=False) == []


def test_hovering_uses_position_when_speed_is_missing():
    hover = [point(k * 2, 0, 3 * (k % 2), speed=None) for k in range(46)]
    assert kinds(detect(hover, PARAMS, gap_s=GAP, rotorcraft=True)) == [HOVERING]


def test_low_flight_for_helicopters_only():
    low = line(0, 40, alt=250)
    assert kinds(detect(low, PARAMS, gap_s=GAP, rotorcraft=True)) == [LOW]
    assert detect(low, PARAMS, gap_s=GAP, rotorcraft=False) == []
    assert detect(line(0, 40, alt=450), PARAMS, gap_s=GAP, rotorcraft=True) == []
    assert detect(line(0, 40, alt=250, on_ground=True), PARAMS, gap_s=GAP, rotorcraft=True) == []


def test_ground_movement_is_not_circling():
    taxi = orbit(0, 3, alt=None, speed=25.0, on_ground=True)
    assert detect(taxi, PARAMS, gap_s=GAP, rotorcraft=False) == []


def test_current_keeps_episodes_ending_near_the_latest_position():
    track = orbit(0, 3) + line(182, 120, origin_m=200, speed_mps=45)
    episodes = detect(track, PARAMS, gap_s=GAP, rotorcraft=True)
    assert current(episodes, track[-1].at, PARAMS) == []
    assert kinds(current(episodes, episodes[0].end + timedelta(seconds=10), PARAMS)) == [CIRCLING]


def _record_orbit(store):
    from helpers import obs, poll

    sid = store.start_session("adsbfi", "test", {})
    for k in range(91):  # three minutes at 2 s, one turn a minute
        t = T0 + timedelta(seconds=2 * k)
        store.record_poll(sid, poll(t, [obs(t, "a2097b", miles=0.124, bearing=(12 * k) % 360, category="A7",
                                            registration="N230LA", ground_speed_kt=40.0, altitude_baro_ft=650)]))
    return sid


def test_report_lists_behaviors(config, store):
    from airspace import report

    sid = _record_orbit(store)
    data = report.build(store, sid, config)
    (b,) = data["behaviors"]
    assert (b["kind"], b["registration"]) == ("circling", "N230LA")
    assert b["turns"] == pytest.approx(3, abs=0.2) and b["distance_mi"] < 0.05
    assert "| circling | N230LA |" in report.render(data, config)


def test_behaviors_export_has_no_home_distance(config, store):
    from airspace import export
    from airspace.replay import RegistryCache

    sid = _record_orbit(store)
    fc = export.to_geojson(store, sid, config, RegistryCache(), geometry="behaviors")
    (f,) = fc["features"]
    assert f["properties"]["behavior"] == "circling" and f["properties"]["registration"] == "N230LA"
    assert "distance" not in str(f["properties"]) and "home" not in fc["metadata"]
    assert "not the reason" in fc["metadata"]["behavior_rules"]


def test_snapshot_reports_current_circling(config):
    from helpers import obs, poll

    from airspace.state import StateBuilder

    builder = StateBuilder(config)
    for k in range(91):
        t = T0 + timedelta(seconds=2 * k)
        builder.ingest(poll(t, [obs(t, "a2097b", miles=0.124, bearing=(12 * k) % 360, category="A7",
                                    ground_speed_kt=40.0, altitude_baro_ft=650)]))
    (a,) = builder.snapshot(T0 + timedelta(seconds=181))["aircraft"]
    (b,) = a["behaviors"]
    assert b["kind"] == "circling" and b["duration_s"] == pytest.approx(180, abs=3) and b["at_least"] is False
    assert b["radius_m"] == pytest.approx(200, rel=0.1)
    # A minute after leaving the orbit it's no longer current
    t = T0 + timedelta(seconds=240)
    builder.ingest(poll(t, [obs(t, "a2097b", miles=2, bearing=90, category="A7")]))
    assert builder.snapshot(t)["aircraft"][0]["behaviors"] == []
