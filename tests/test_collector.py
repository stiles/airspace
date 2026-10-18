import asyncio
import gzip
import json

import httpx
import pytest

from airspace.collector import Clock, Collector
from airspace.models import ErrorKind
from airspace.providers.adsbfi import AdsbFi

EMPTY = json.dumps({"ac": [], "now": 1792261098001}).encode()


class FakeClock:
    """Monotonic and wall time advance together unless a sleep is simulated."""

    def __init__(self):
        self.mono = 1000.0
        self.wall = 1_792_261_000.0
        self.sleeps: list[float] = []
        self.host_sleep_at: float | None = None  # monotonic time at which to simulate host sleep
        self.host_sleep_s = 0.0

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.mono += seconds
        self.wall += seconds
        if self.host_sleep_at is not None and self.mono >= self.host_sleep_at:
            self.wall += self.host_sleep_s  # wall clock jumps; monotonic does not on macOS
            self.host_sleep_at = None

    def clock(self):
        return Clock(monotonic=lambda: self.mono, wall=lambda: self.wall, sleep=self.sleep)


def make_collector(config, store, handler, fake=None, **kw):
    fake = fake or FakeClock()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = Collector(config, AdsbFi(), store, client, clock=fake.clock(), jitter=lambda lo, hi: hi, **kw)
    collector.start_session("test")
    return collector, fake


def responses(*items):
    """Handler returning each item in turn; the last one repeats."""
    calls = []

    def handler(request):
        item = items[min(len(calls), len(items) - 1)]
        calls.append(request)
        if isinstance(item, Exception):
            raise item
        return item

    handler.calls = calls
    return handler


def poll_kinds(store):
    return [r["error_kind"] for r in store.conn.execute("SELECT error_kind FROM polls WHERE kind = 'poll'")]


def test_success_with_fixture(config, store, fixture_bytes):
    collector, _ = make_collector(config, store, responses(httpx.Response(200, content=fixture_bytes)))
    result = asyncio.run(collector.poll_once())
    assert result.ok and len(result.observations) == 54
    assert collector.next_delay(result) == 10
    assert store.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 54
    with gzip.open(store.raw_path(collector.session_id), "rt") as f:
        assert json.loads(f.readline())["status_code"] == 200


def test_empty_response_is_success_not_failure(config, store):
    collector, _ = make_collector(config, store, responses(httpx.Response(200, content=EMPTY)))
    result = asyncio.run(collector.poll_once())
    assert result.ok and result.observations == []


def test_429_honors_retry_after(config, store):
    collector, _ = make_collector(config, store, responses(httpx.Response(429, headers={"Retry-After": "120"})))
    result = asyncio.run(collector.poll_once())
    assert result.error_kind is ErrorKind.RATE_LIMITED
    assert collector.next_delay(result) == 120
    assert collector.paused is None


def test_timeout_backs_off_with_cap(config, store):
    collector, _ = make_collector(config, store, responses(httpx.ReadTimeout("slow")))
    delays = []
    for _ in range(8):
        result = asyncio.run(collector.poll_once())
        assert result.error_kind is ErrorKind.TRANSIENT
        delays.append(collector.next_delay(result))
    assert delays[:4] == [20, 40, 80, 160]
    assert max(delays) == config.collector.max_backoff_s
    assert collector.paused is None


def test_backoff_resets_after_success(config, store):
    collector, _ = make_collector(
        config, store, responses(httpx.Response(503), httpx.Response(503), httpx.Response(200, content=EMPTY))
    )
    delays = [collector.next_delay(asyncio.run(collector.poll_once())) for _ in range(3)]
    assert delays == [20, 40, 10]


def test_client_error_pauses_immediately(config, store):
    handler = responses(httpx.Response(403))
    collector, _ = make_collector(config, store, handler)
    reason = asyncio.run(collector.run(duration_s=3600))
    assert reason.startswith("paused: HTTP 403")
    assert len(handler.calls) == 1


def test_invalid_json_pauses_after_limit(config, store):
    handler = responses(httpx.Response(200, content=b"<html>"))
    collector, _ = make_collector(config, store, handler)
    reason = asyncio.run(collector.run(duration_s=3600))
    assert "3 consecutive schema errors" in reason
    assert len(handler.calls) == 3
    assert poll_kinds(store) == ["schema"] * 3


def test_schedule_is_start_to_start_without_catch_up(config, store):
    collector, fake = make_collector(config, store, responses(httpx.Response(200, content=EMPTY)))
    reason = asyncio.run(collector.run(duration_s=60))
    assert reason == "duration"
    assert len(poll_kinds(store)) == 6  # t = 0, 10, ..., 50
    assert all(s <= 1.0 for s in fake.sleeps)


def test_host_sleep_recorded_without_burst(config, store):
    fake = FakeClock()
    fake.host_sleep_at = fake.mono + 3
    fake.host_sleep_s = 900
    handler = responses(httpx.Response(200, content=EMPTY))
    collector, _ = make_collector(config, store, handler, fake=fake)
    reason = asyncio.run(collector.run(duration_s=930))
    assert reason == "duration"
    gaps = store.conn.execute("SELECT gap_s FROM polls WHERE kind = 'sleep_gap'").fetchall()
    assert len(gaps) == 1 and gaps[0]["gap_s"] == pytest.approx(900)
    # Before sleep: 1 poll. After waking: one immediate poll, then the normal 10s cadence for ~27s
    assert len(handler.calls) <= 5


def test_stop_event(config, store):
    collector, fake = make_collector(config, store, responses(httpx.Response(200, content=EMPTY)))
    stop = asyncio.Event()
    original = fake.sleep

    async def sleep_then_stop(seconds):
        await original(seconds)
        if fake.mono > 1015:
            stop.set()

    collector.clock.sleep = sleep_then_stop
    assert asyncio.run(collector.run(stop=stop)) == "stopped"
    assert len(poll_kinds(store)) == 2


def _fast_config(config, interval=2.0, hold=30.0):
    config.collector.fast_interval_s = interval
    config.collector.fast_hold_s = hold
    return config


def test_fast_polling_while_triggered_then_holds_and_relaxes(config, store):
    # Triggered on polls 3-5 (by count), then quiet
    seen = []

    def trigger(result):
        seen.append(len(seen) + 1)
        return 3 <= len(seen) <= 5

    collector, fake = make_collector(_fast_config(config), store, responses(httpx.Response(200, content=EMPTY)),
                                     fast_trigger=trigger)
    delays = []
    for _ in range(25):
        result = asyncio.run(collector.poll_once())
        delays.append(collector.next_delay(result))
        fake.mono += delays[-1]
    assert delays[:2] == [10, 10]
    assert delays[2:5] == [2, 2, 2]
    # Holds at 2 s for 30 s after the last trigger (15 polls), then back to 10
    assert delays[5:19] == [2] * 14 and delays[19:] == [10] * 6
    fast = [r["fast"] for r in store.conn.execute("SELECT fast FROM polls ORDER BY id")]
    assert fast[:3] == [0, 0, 0] and fast[3] == 1 and fast[-1] == 0


def test_fast_polling_off_by_config_and_on_failure(config, store):
    collector, _ = make_collector(_fast_config(config, interval=0), store, responses(httpx.Response(200, content=EMPTY)),
                                  fast_trigger=lambda r: True)
    assert collector.next_delay(asyncio.run(collector.poll_once())) == 10

    collector, _ = make_collector(_fast_config(config), store,
                                  responses(httpx.Response(200, content=EMPTY), httpx.Response(503)),
                                  fast_trigger=lambda r: True)
    assert collector.next_delay(asyncio.run(collector.poll_once())) == 2
    # A failure drops fast mode and backs off from the normal interval
    assert collector.next_delay(asyncio.run(collector.poll_once())) == 20
    assert collector.fast is False


def test_fast_interval_validated(config):
    from airspace.config import Config

    data = config.model_dump(mode="json")
    data["collector"]["fast_interval_s"] = 0.5
    with pytest.raises(ValueError, match="fast_interval_s"):
        Config.model_validate(data)


def test_fast_trigger_rules(config):
    from helpers import at, obs, poll

    from airspace.catalog import Catalog
    from airspace.collector import make_fast_trigger
    from airspace.replay import RegistryCache

    plain = make_fast_trigger(config)
    t = at(0)
    assert plain(poll(t, [obs(t, "h1", category="A7", miles=2)]))
    assert not plain(poll(t, [obs(t, "h1", category="A7", miles=8)]))  # outside the visible area
    assert not plain(poll(t, [obs(t, "h1", category="A7", miles=2, on_ground=True)]))
    assert not plain(poll(t, [obs(t, "h1", category="A7", miles=2, age=45)]))  # stale position
    assert not plain(poll(t, [obs(t, "j1", category="A3", miles=2)]))

    catalog = Catalog()
    catalog._pattern_row({"pattern": "POLICE", "operator": "", "role": "police", "source": "test"})
    with_catalog = make_fast_trigger(config, RegistryCache(None, catalog=catalog))
    # A fixed-wing police aircraft counts once the catalog knows it
    assert with_catalog(poll(t, [obs(t, "p1", category="A1", miles=2, owner_operator="CITY POLICE DEPT")]))
    assert not with_catalog(poll(t, [obs(t, "p2", category="A1", miles=2, owner_operator="ACME LLC")]))


def test_old_database_gains_fast_column(tmp_path):
    import sqlite3

    from airspace.storage import Store

    data = tmp_path / "old"
    data.mkdir()
    conn = sqlite3.connect(data / "airspace.sqlite")
    conn.execute("CREATE TABLE polls (id INTEGER PRIMARY KEY, session_id INTEGER, kind TEXT, requested_at TEXT)")
    conn.commit()
    conn.close()
    store = Store(data)
    assert "fast" in {r["name"] for r in store.conn.execute("PRAGMA table_info(polls)")}
    store.close()


def test_run_polls_fast_on_the_schedule(config, store):
    collector, _ = make_collector(_fast_config(config), store, responses(httpx.Response(200, content=EMPTY)),
                                  fast_trigger=lambda r: True)
    asyncio.run(collector.run(duration_s=60))
    # First poll at the normal mode, then every 2 s: t = 0, 2, ..., 58
    assert len(poll_kinds(store)) == 30
