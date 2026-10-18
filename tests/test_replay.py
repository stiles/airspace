import sqlite3

import pytest

from airspace.replay import replay_state
from airspace.state import StateBuilder
from airspace.storage import Store
from helpers import ErrorKind, at, obs, poll


def scenario():
    """Helicopter crossing the boundary, a jet that drops out, an outage, a cached position and an orbit."""
    polls = []
    for i in range(40):
        t = at(10 * i)
        if 15 <= i < 18:
            polls.append(poll(t, error_kind=ErrorKind.TRANSIENT))
            continue
        observations = [obs(t, "heli", miles=3 + i * 0.08, category="A7", track_deg=0.0)]
        if i < 10:
            observations.append(obs(t, "jet", miles=4, bearing=90, category="A3"))
        observations.append(obs(t, "parked", miles=3, bearing=180, age=30 + 10 * i, on_ground=True))
        # Orbits home at about 250 m, one turn every 100 s, so replay must agree on behaviors too
        observations.append(obs(t, "orbiter", miles=0.155, bearing=(36 * i) % 360, category="A7",
                                ground_speed_kt=30.0, altitude_baro_ft=600))
        polls.append(poll(t, observations))
    return polls


def fingerprint(snapshot):
    return {
        "aircraft": {
            a["id"]: (a["freshness"], round(a["position_age_s"] or -1, 6), a["inside_visible"], a["trail"], a["behaviors"])
            for a in snapshot["aircraft"]
        },
        "last_seen": sorted(a["id"] for a in snapshot["last_seen"]),
        "feed": (snapshot["feed"]["status"], snapshot["feed"]["last_success_at"]),
    }


@pytest.mark.parametrize("seconds", [5, 95, 155, 175, 255, 395])
def test_replay_matches_live_snapshot(config, store, seconds):
    sid = store.start_session("adsbfi", "test", {})
    live = StateBuilder(config)
    moment = at(seconds)
    expected = None
    for p in scenario():
        if (p.received_at or p.requested_at) > moment:
            break
        store.record_poll(sid, p)
        live.ingest(p)
    expected = fingerprint(live.snapshot(moment))
    for p in scenario():
        if (p.received_at or p.requested_at) > moment:
            store.record_poll(sid, p)  # recorded later; replay must not see it

    reader = Store(config.storage.data_dir, read_only=True)
    try:
        replayed = replay_state(reader, sid, moment, config)
    finally:
        reader.close()
    assert fingerprint(replayed) == expected
    assert replayed["mode"] == "replay"


def test_replay_cannot_see_later_records(config, store):
    sid = store.start_session("adsbfi", "test", {})
    store.record_poll(sid, poll(at(0), [obs(at(0), "early")]))
    store.record_poll(sid, poll(at(20), [obs(at(20), "late")]))
    reader = Store(config.storage.data_dir, read_only=True)
    snap = replay_state(reader, sid, at(10), config)
    assert [a["id"] for a in snap["aircraft"]] == ["early"]
    assert snap["feed"]["polls"] == 1


def test_replay_reader_is_read_only(config, store):
    sid = store.start_session("adsbfi", "test", {})
    reader = Store(config.storage.data_dir, read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        reader.conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))


def test_replay_after_long_outage_keeps_true_last_success(config, store):
    sid = store.start_session("adsbfi", "test", {})
    store.record_poll(sid, poll(at(0), [obs(at(0), "a")]))
    for t in range(10, 1000, 10):
        store.record_poll(sid, poll(at(t), error_kind=ErrorKind.TRANSIENT))
    reader = Store(config.storage.data_dir, read_only=True)
    snap = replay_state(reader, sid, at(995), config)
    assert snap["feed"]["status"] == "stale"
    assert snap["feed"]["last_success_at"] == at(0).isoformat(timespec="milliseconds")


def test_replay_opens_after_writer_exits(config, store):
    # A clean writer exit removes -wal and -shm; readers must still open the database
    sid = store.start_session("adsbfi", "test", {})
    store.record_poll(sid, poll(at(0), [obs(at(0), "a")]))
    store.close()
    assert not (config.storage.data_dir / "airspace.sqlite-shm").exists()
    reader = Store(config.storage.data_dir, read_only=True)
    try:
        assert [a["id"] for a in replay_state(reader, sid, at(1), config)["aircraft"]] == ["a"]
        with pytest.raises(sqlite3.OperationalError):
            reader.conn.execute("DELETE FROM sessions")
    finally:
        reader.close()
