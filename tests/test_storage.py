from datetime import UTC, datetime, timedelta

from airspace.lock import CollectorLock, CollectorLocked
from airspace.models import Observation, PollResult, PositionTimeBasis

T0 = datetime(2026, 10, 17, 18, 0, tzinfo=UTC)


def obs(aircraft_id, position_at, **kw):
    return Observation(
        provider="adsbfi", aircraft_id=aircraft_id, received_at=T0, provider_at=T0, position_at=position_at,
        position_time_basis=PositionTimeBasis.DERIVED_AGE if position_at else PositionTimeBasis.UNKNOWN,
        latitude=34.0, longitude=-118.42, **kw,
    )


def poll(*observations):
    return PollResult(provider="adsbfi", requested_at=T0, received_at=T0, latency_ms=50, status_code=200,
                      provider_at=T0, observations=list(observations))


def test_new_position_flag(store):
    sid = store.start_session("adsbfi", "test", {})
    t1, t2 = T0, T0 + timedelta(seconds=5)
    store.record_poll(sid, poll(obs("a", t1), obs("b", None)))
    store.record_poll(sid, poll(obs("a", t1)))        # cached/duplicate position
    store.record_poll(sid, poll(obs("a", t2)))
    store.record_poll(sid, poll(obs("a", t1)))        # out of order
    flags = [r["new_position"] for r in store.conn.execute(
        "SELECT new_position FROM observations WHERE aircraft_id = 'a' ORDER BY id")]
    assert flags == [1, 0, 1, 0]
    # Unknown position time is stored but never counted as a new position
    assert store.conn.execute("SELECT new_position FROM observations WHERE aircraft_id = 'b'").fetchone()[0] == 0


def test_booleans_and_nulls_round_trip(store):
    sid = store.start_session("adsbfi", "test", {})
    store.record_poll(sid, poll(obs("g", T0, on_ground=True), obs("u", T0)))
    rows = {r["aircraft_id"]: r for r in store.conn.execute("SELECT aircraft_id, on_ground FROM observations")}
    assert rows["g"]["on_ground"] == 1 and rows["u"]["on_ground"] is None


def test_wal_mode(store):
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_resolve_latest(store):
    first = store.start_session("adsbfi", "test", {})
    second = store.start_session("adsbfi", "test", {})
    assert store.resolve_session("latest") == second
    assert store.resolve_session(str(first)) == first


def test_lock_prevents_second_collector(tmp_path):
    import pytest

    with CollectorLock(tmp_path):
        with pytest.raises(CollectorLocked):
            with CollectorLock(tmp_path):
                pass
    with CollectorLock(tmp_path):
        pass
