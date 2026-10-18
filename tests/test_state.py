import pytest

from airspace.state import StateBuilder
from helpers import ErrorKind, at, obs, poll


@pytest.fixture
def builder(config):
    return StateBuilder(config)


def shown(snapshot):
    return {a["id"]: a for a in snapshot["aircraft"]}


def test_duplicate_and_out_of_order_positions_ignored(builder):
    builder.ingest(poll(at(0), [obs(at(0), "a", age=1)]))
    builder.ingest(poll(at(10), [obs(at(10), "a", age=11)]))  # same position time, cached
    builder.ingest(poll(at(20), [obs(at(20), "a", age=1, miles=2.2)]))
    builder.ingest(poll(at(30), [obs(at(30), "a", age=25)]))  # older than latest
    assert len(builder.tracks["a"].points) == 2
    assert builder.tracks["a"].latest.position_at == at(19)


def test_freshness_ages_from_local_receipt(builder):
    builder.ingest(poll(at(0), [obs(at(0), "a", age=2)]))
    assert shown(builder.snapshot(at(5)))["a"]["position_age_s"] == pytest.approx(7)
    assert shown(builder.snapshot(at(5)))["a"]["freshness"] == "fresh"
    assert shown(builder.snapshot(at(25)))["a"]["freshness"] == "fading"
    snap = builder.snapshot(at(70))
    assert "a" not in shown(snap)
    assert snap["last_seen"][0]["id"] == "a"


def test_unknown_position_time(builder):
    builder.ingest(poll(at(0), [obs(at(0), "u", age=None)]))
    item = shown(builder.snapshot(at(1)))["u"]
    assert item["freshness"] == "unknown" and item["position_age_s"] is None
    assert len(builder.tracks["u"].points) == 0
    assert "u" not in shown(builder.snapshot(at(61)))


def test_disappearance_is_not_an_exit(builder):
    builder.ingest(poll(at(0), [obs(at(0), "a", miles=4.5)]))
    builder.ingest(poll(at(10), [obs(at(10), "a", miles=4.8)]))
    for t in (20, 30, 40):
        builder.ingest(poll(at(t), []))
    assert list(builder.events) == []
    assert shown(builder.snapshot(at(45)))["a"]["freshness"] == "fading"


def test_exit_needs_consecutive_fresh_positions(builder):
    builder.ingest(poll(at(0), [obs(at(0), "a", miles=4.8)]))
    builder.ingest(poll(at(10), [obs(at(10), "a", miles=5.2)]))
    (event,) = builder.events
    assert event["type"] == "exited" and event["aircraft_id"] == "a"


def test_no_exit_across_gap_or_stale_position(builder):
    builder.ingest(poll(at(0), [obs(at(0), "gap", miles=4.8), obs(at(0), "stale", miles=4.8)]))
    builder.ingest(poll(at(60), [obs(at(60), "gap", miles=5.3)]))
    builder.ingest(poll(at(10), [obs(at(10), "stale", miles=5.2, age=25)]))
    assert list(builder.events) == []


def test_entry_event(builder):
    builder.ingest(poll(at(0), [obs(at(0), "a", miles=5.3)]))
    builder.ingest(poll(at(10), [obs(at(10), "a", miles=4.9)]))
    assert builder.events[-1]["type"] == "entered"


def test_trail_splits_on_gap_and_jump(builder):
    times_miles = [(0, 1.0), (10, 1.2), (20, 1.4), (80, 1.6), (90, 1.8), (100, 9.0), (110, 9.1)]
    for t, miles in times_miles:
        builder.ingest(poll(at(t), [obs(at(t), "a", miles=miles)]))
    segments = shown(builder.snapshot(at(110)))["a"]["trail"]
    # [0-20], gap, [80-90], 7 miles in 10s is implausible, [100-110]
    assert [len(s) for s in segments] == [3, 2, 2]


def test_trail_coordinates_are_lon_lat(builder):
    builder.ingest(poll(at(0), [obs(at(0), "a")]))
    builder.ingest(poll(at(10), [obs(at(10), "a", miles=2.1)]))
    lon, lat = shown(builder.snapshot(at(10)))["a"]["trail"][0][0]
    assert -119 < lon < -118 and 33 < lat < 35


def test_trail_limited_to_duration(builder, config):
    for t in range(0, 600, 10):
        builder.ingest(poll(at(t), [obs(at(t), "a", miles=1 + t / 1000)]))
    points = sum(len(s) for s in shown(builder.snapshot(at(590)))["a"]["trail"])
    assert points <= config.freshness.trail_duration_s / 10 + 1


def test_memory_bounded_with_turnover(builder):
    for i in range(2000):
        t = at(10 * i)
        builder.ingest(poll(t, [obs(t, f"x{i}"), obs(t, f"y{i}")]))
    # Retention is max(trail duration, hide threshold) = 300s, i.e. ~31 polls of two new aircraft each
    assert len(builder.tracks) <= 64


def test_feed_status_transitions(builder):
    f = builder.config.freshness
    assert builder.feed.status(at(0), f.fade_s, f.hide_s) == "starting"
    builder.ingest(poll(at(0), [obs(at(0), "a")]))
    assert builder.snapshot(at(5))["feed"]["status"] == "live"
    builder.ingest(poll(at(10), error_kind=ErrorKind.TRANSIENT))
    snap = builder.snapshot(at(11))
    assert snap["feed"]["status"] == "degraded" and snap["feed"]["last_error"].startswith("timeout")
    assert builder.snapshot(at(61))["feed"]["status"] == "stale"
    builder.ingest(poll(at(70), []))
    assert builder.snapshot(at(71))["feed"]["status"] == "live"
    assert builder.snapshot(at(95))["feed"]["status"] == "delayed"  # e.g. laptop just woke
    builder.feed.paused = "HTTP 403"
    assert builder.snapshot(at(71))["feed"]["status"] == "paused"


def test_outage_never_looks_like_empty_sky(builder):
    builder.ingest(poll(at(0), [obs(at(0), "a")]))
    for t in range(10, 200, 10):
        builder.ingest(poll(at(t), error_kind=ErrorKind.TRANSIENT))
    snap = builder.snapshot(at(200))
    assert snap["aircraft"] == []
    assert snap["feed"]["status"] == "stale"


def test_sorted_by_distance_with_classification(builder):
    builder.ingest(poll(at(0), [obs(at(0), "far", miles=4, category="A3"), obs(at(0), "heli", miles=1, category="A7")]))
    snap = builder.snapshot(at(1))
    assert [a["id"] for a in snap["aircraft"]] == ["heli", "far"]
    assert snap["aircraft"][0]["class"] == "rotorcraft"
    assert snap["aircraft"][0]["in_latest_response"] is True


def test_snapshot_carries_page_title(config):
    assert StateBuilder(config).snapshot(at(0))["area"]["home"]["title"] == "Airspace"
    config.home.title = "Jim's Airspace"
    assert StateBuilder(config).snapshot(at(0))["area"]["home"]["title"] == "Jim's Airspace"
