import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
from helpers import obs
from test_collector import EMPTY, make_collector, responses

from airspace import report
from airspace.collector import MAINTENANCE_S
from airspace.models import PollResult, Rejection
from airspace.procinfo import Memory, memory
from airspace.replay import RegistryCache

DAY = timedelta(days=1)
# Matches FakeClock's starting wall time, so collector cutoffs line up with these records
NOW = datetime.fromtimestamp(1_792_261_000.0, UTC)


def record(store, sid, t, n=2, rejected=False):
    store.record_poll(sid, PollResult(
        provider="adsbfi", requested_at=t, received_at=t, latency_ms=100, status_code=200, provider_at=t,
        observations=[obs(t, f"ac{i:04d}") for i in range(n)],
        rejections=[Rejection(reason="bad", raw={"hex": "x"})] if rejected else [],
        raw_body=b"{}",
    ))


def counts(store):
    q = lambda table: store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return {t: q(t) for t in ("sessions", "polls", "observations", "rejections")}


def test_prune_removes_old_records_and_emptied_sessions(store):
    old = store.start_session("adsbfi", "collect", {})
    for i in range(5):
        record(store, old, NOW - 10 * DAY + timedelta(seconds=10 * i), rejected=i == 0)
    store.end_session(old, "duration")
    spanning = store.start_session("adsbfi", "serve", {})
    record(store, spanning, NOW - 8 * DAY)
    record(store, spanning, NOW - 6 * DAY)
    assert store.raw_path(old).exists()

    result = store.prune(NOW - 7 * DAY)

    assert (result.polls, result.observations, result.sessions) == (6, 12, [old])
    assert counts(store) == {"sessions": 1, "polls": 1, "observations": 2, "rejections": 0}
    # A session with recent polls survives and keeps its raw file
    assert not store.raw_path(old).exists()
    assert store.raw_path(spanning).exists()


def test_prune_batches_and_keeps_the_active_session(store, monkeypatch):
    monkeypatch.setattr("airspace.storage.PRUNE_BATCH_POLLS", 3)
    sid = store.start_session("adsbfi", "serve", {})
    for i in range(10):
        record(store, sid, NOW - 9 * DAY + timedelta(seconds=10 * i))
    result = store.prune(NOW - 7 * DAY, keep_session=sid)
    assert (result.polls, result.observations, result.sessions) == (10, 20, [])
    assert counts(store)["sessions"] == 1


def test_prune_with_nothing_old_is_a_no_op(store):
    sid = store.start_session("adsbfi", "serve", {})
    record(store, sid, NOW)
    result = store.prune(NOW - 7 * DAY)
    assert (result.polls, result.observations, result.sessions) == (0, 0, [])
    assert counts(store)["polls"] == 1


def test_collector_prunes_on_start_then_hourly_and_records_memory(config, store):
    old = store.start_session("adsbfi", "collect", {})
    record(store, old, NOW - 10 * DAY)
    store.end_session(old, "duration")

    readings = iter([Memory(100 * 2**20, 300 * 2**20), Memory(104 * 2**20, 310 * 2**20)])
    collector, _ = make_collector(
        config, store, responses(httpx.Response(200, content=EMPTY)), memory=lambda: next(readings)
    )
    asyncio.run(collector.run(duration_s=MAINTENANCE_S + 30))

    assert store.conn.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (old,)).fetchone()[0] == 0
    stats = store.process_stats(collector.session_id)
    assert [r["footprint_bytes"] for r in stats] == [100 * 2**20, 104 * 2**20]
    assert counts(store)["polls"] > 300  # collection carried on throughout


def test_retention_zero_keeps_everything(config, store):
    config.storage.retention_days = 0
    old = store.start_session("adsbfi", "collect", {})
    record(store, old, NOW - 400 * DAY)
    store.end_session(old, "duration")
    collector, _ = make_collector(config, store, responses(httpx.Response(200, content=EMPTY)))
    asyncio.run(collector.run(duration_s=20))
    assert counts(store)["sessions"] == 2


def test_maintenance_failure_never_stops_collection(config, store):
    def broken():
        raise RuntimeError("no memory reading")

    collector, _ = make_collector(config, store, responses(httpx.Response(200, content=EMPTY)), memory=broken)
    assert asyncio.run(collector.run(duration_s=60)) == "duration"
    assert counts(store)["polls"] == 6


def test_report_shows_memory_growth(config, store):
    sid = store.start_session("adsbfi", "serve", {})
    record(store, sid, NOW)
    for hour, mb in enumerate([140, 143, 146]):
        store.conn.execute(
            "INSERT INTO process_stats (session_id, at, footprint_bytes, rss_bytes) VALUES (?, ?, ?, ?)",
            (sid, (NOW + timedelta(hours=hour)).isoformat(), mb * 2**20, 2 * mb * 2**20),
        )
    data = report.build(store, sid, config)
    assert data["process"]["peak_bytes"] == 146 * 2**20
    assert data["process"]["growth_bytes_per_hour"] == 3 * 2**20
    text = report.render(data, config)
    assert "3 hourly readings: first 140.0 MB, last 146.0 MB, peak 146.0 MB" in text
    assert "Growth: +3.00 MB/hour" in text


def test_registry_cache_drops_least_recently_used():
    class Registry:
        def __init__(self):
            self.looked_up = []

        def lookup(self, ids):
            self.looked_up += ids
            return {aid: {"type": "6"} for aid in ids}

    registry = Registry()
    cache = RegistryCache(registry, max_records=3)
    cache.resolve(["a", "b", "c"])
    cache.classify("a", None)  # a is now the most recent
    cache.resolve(["d"])
    assert list(cache.records) == ["c", "a", "d"]
    assert cache.unresolved(["a", "b"]) == ["b"]


def test_memory_reading_is_plausible():
    m = memory()
    if m.rss_bytes is not None:  # macOS and Linux
        assert 1 * 2**20 < m.rss_bytes < 4 * 2**30
        assert json.dumps(m.as_dict())
