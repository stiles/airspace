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
