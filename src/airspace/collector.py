"""Polling loop: one geographic request in flight at a time, on a monotonic schedule."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from airspace.config import Config
from airspace.models import ErrorKind, PollResult
from airspace.providers.base import Provider, fetch
from airspace.storage import Store

log = logging.getLogger(__name__)

# Wall-clock time running ahead of monotonic time by more than this means the host slept
# (on macOS, time.monotonic() does not advance during sleep)
SLEEP_DETECT_S = 5.0
WAIT_STEP_S = 1.0


@dataclass
class Clock:
    monotonic: Callable[[], float] = time.monotonic
    wall: Callable[[], float] = time.time
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


class Collector:
    def __init__(
        self,
        config: Config,
        provider: Provider,
        store: Store,
        client: httpx.AsyncClient,
        *,
        clock: Clock | None = None,
        jitter: Callable[[float, float], float] = random.uniform,
        on_poll: Callable[[PollResult], None] | None = None,
    ):
        self.config = config
        self.provider = provider
        self.store = store
        self.client = client
        self.clock = clock or Clock()
        self.jitter = jitter
        self.on_poll = on_poll

        self.session_id: int | None = None
        self.paused: str | None = None
        self.consecutive_failures = 0
        self.consecutive_schema_errors = 0
        self.last_result: PollResult | None = None

    def start_session(self, mode: str) -> int:
        self.session_id = self.store.start_session(self.provider.name, mode, self.config.model_dump(mode="json"))
        return self.session_id

    async def poll_once(self) -> PollResult:
        home, area = self.config.home, self.config.area
        result = await fetch(self.provider, self.client, home.latitude, home.longitude, area.collection_radius_nm)
        self.store.record_poll(self.session_id, result)
        self.last_result = result
        if self.on_poll:
            self.on_poll(result)
        return result

    def next_delay(self, result: PollResult) -> float:
        """Seconds from this poll's start to the next one. Sets `paused` on unrecoverable errors."""
        settings = self.config.collector
        interval = settings.poll_interval_s
        if result.ok:
            self.consecutive_failures = 0
            self.consecutive_schema_errors = 0
            return interval

        if result.error_kind is ErrorKind.FATAL:
            self.paused = f"{result.error}; client errors count toward the provider's limit"
            return interval
        if result.error_kind is ErrorKind.SCHEMA:
            self.consecutive_schema_errors += 1
            if self.consecutive_schema_errors >= settings.schema_error_limit:
                self.paused = f"{self.consecutive_schema_errors} consecutive schema errors: {result.error}"
                return interval

        self.consecutive_failures += 1
        ceiling = min(settings.max_backoff_s, interval * 2**self.consecutive_failures)
        delay = max(interval, self.jitter(ceiling / 2, ceiling))
        if result.error_kind is ErrorKind.RATE_LIMITED and result.retry_after_s is not None:
            delay = max(delay, result.retry_after_s)
        return delay

    async def run(self, duration_s: float | None = None, stop: asyncio.Event | None = None) -> str:
        """Poll until the duration elapses (wall clock), `stop` is set or collection pauses. Returns the reason."""
        if self.session_id is None:
            raise RuntimeError("start_session() first")
        deadline = self.clock.wall() + duration_s if duration_s is not None else None

        def finished() -> str | None:
            if stop is not None and stop.is_set():
                return "stopped"
            if deadline is not None and self.clock.wall() >= deadline:
                return "duration"
            return None

        while True:
            if reason := finished():
                return reason
            started = self.clock.monotonic()
            result = await self.poll_once()
            delay = self.next_delay(result)
            if self.paused:
                log.error("collection paused: %s", self.paused)
                return f"paused: {self.paused}"
            if not result.ok:
                log.warning("poll failed (%s); next attempt in %.0fs", result.error, delay)

            due = started + delay
            while (remaining := due - self.clock.monotonic()) > 0:
                if reason := finished():
                    return reason
                mono_before, wall_before = self.clock.monotonic(), self.clock.wall()
                await self.clock.sleep(min(WAIT_STEP_S, remaining))
                slept = (self.clock.wall() - wall_before) - (self.clock.monotonic() - mono_before)
                if slept > SLEEP_DETECT_S:
                    # Record the gap and poll right away; no catch-up burst for missed polls
                    log.warning("host sleep detected (%.0fs); resuming", slept)
                    self.store.record_gap(self.session_id, "sleep_gap", slept)
                    break
