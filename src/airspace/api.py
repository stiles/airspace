"""Local HTTP API. One process, one collector, bound to 127.0.0.1 by the CLI."""

from __future__ import annotations

import asyncio
import logging
import statistics
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from airspace.collector import Clock, Collector
from airspace.config import Config
from airspace.enrich import HangarbayRegistry
from airspace.models import PollResult, utcnow
from airspace.providers import get_provider
from airspace.providers.base import USER_AGENT
from airspace.replay import RegistryCache, replay_state
from airspace.state import StateBuilder
from airspace.storage import Store

log = logging.getLogger(__name__)

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class LiveService:
    """Owns the collector task and the live state builder."""

    def __init__(self, config: Config, *, transport: httpx.AsyncBaseTransport | None = None, clock: Clock | None = None):
        self.config = config
        self.transport = transport
        self.clock = clock
        registry = HangarbayRegistry.open_optional(config.enrich.hangarbay_db)
        self.cache = RegistryCache(registry)
        # DuckDB connections are not safe for concurrent use; keep lookups on one thread
        self._lookup_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="registry")
        self.state = StateBuilder(config, classifier=self.cache.classify, mode="live")
        self.store: Store | None = None
        self.collector: Collector | None = None
        self.task: asyncio.Task | None = None
        self.stop = asyncio.Event()
        self.end_reason: str | None = None
        self._pending: set[asyncio.Future] = set()

    def _on_poll(self, result: PollResult) -> None:
        self.state.ingest(result)
        unresolved = self.cache.unresolved({o.aircraft_id for o in result.observations})
        if unresolved:
            future = asyncio.get_running_loop().run_in_executor(self._lookup_executor, self.cache.resolve, unresolved)
            self._pending.add(future)
            future.add_done_callback(self._pending.discard)

    async def start(self) -> None:
        settings = self.config.collector
        self.store = Store(self.config.storage.data_dir, store_raw=settings.store_raw)
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_s), headers={"User-Agent": USER_AGENT}, transport=self.transport
        )
        self.collector = Collector(
            self.config, get_provider(settings.provider), self.store, self.client, clock=self.clock, on_poll=self._on_poll
        )
        self.session_id = self.collector.start_session("serve")
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            self.end_reason = await self.collector.run(stop=self.stop)
        except Exception as exc:  # surface crashes as a paused feed rather than silent staleness
            log.exception("collector crashed")
            self.end_reason = f"crashed: {exc}"
        if self.end_reason != "stopped":
            self.state.feed.paused = self.end_reason

    async def shutdown(self) -> None:
        self.stop.set()
        if self.task:
            await self.task
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
        self._lookup_executor.shutdown(wait=True)
        if self.cache.registry:
            self.cache.registry.close()
        await self.client.aclose()
        self.store.end_session(self.session_id, self.end_reason or "stopped")
        self.store.close()


def _parse_at(value: str) -> datetime:
    # An unencoded "+00:00" in a query string arrives as " 00:00"
    value = value.strip().replace(" ", "+").replace("Z", "+00:00")
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"invalid timestamp {value!r}; use ISO 8601") from None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def create_app(
    config: Config,
    *,
    mode: str = "live",
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Clock | None = None,
    now=utcnow,
) -> FastAPI:
    """`mode` is 'live' (collector running) or 'replay' (recorded data only, no provider requests)."""
    if mode not in ("live", "replay"):
        raise ValueError(mode)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.live = None
        if mode == "live":
            app.state.live = LiveService(config, transport=transport, clock=clock)
            await app.state.live.start()
        try:
            yield
        finally:
            if app.state.live:
                await app.state.live.shutdown()

    app = FastAPI(title="Airspace", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")

    def live() -> LiveService:
        if app.state.live is None:
            raise HTTPException(409, "live acquisition is disabled in replay mode; use /api/replay")
        return app.state.live

    def reader() -> Store:
        try:
            return Store(config.storage.data_dir, read_only=True)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from None

    # Live endpoints are async so they read state on the event loop, between polls
    @app.get("/api/state")
    async def state() -> dict:
        service = live()
        snapshot = service.state.snapshot(now())
        snapshot["session_id"] = service.session_id
        return snapshot

    @app.get("/api/aircraft/{aircraft_id}")
    async def aircraft(aircraft_id: str) -> dict:
        detail = live().state.aircraft_detail(aircraft_id.lower(), now())
        if detail is None:
            raise HTTPException(404, f"{aircraft_id} not in current state")
        return detail

    @app.get("/api/health")
    async def health() -> dict:
        service = live()
        snapshot = service.state.snapshot(now())
        ages = [a["position_age_s"] for a in snapshot["aircraft"] if a["position_age_s"] is not None]
        return {
            "mode": mode,
            "session_id": service.session_id,
            "collector_running": service.task is not None and not service.task.done(),
            "end_reason": service.end_reason,
            "feed": snapshot["feed"],
            "aircraft_shown": len(snapshot["aircraft"]),
            "position_age_s": {
                "median": statistics.median(ages) if ages else None,
                "max": max(ages) if ages else None,
            },
            "registry_lookups": service.cache.registry is not None,
        }

    @app.get("/api/sessions")
    def sessions() -> list[dict]:
        store = reader()
        try:
            rows = store.sessions()
            result = []
            for r in rows:
                start, end = store.session_bounds(r["id"])
                result.append(
                    {
                        "id": r["id"],
                        "provider": r["provider"],
                        "mode": r["mode"],
                        "started_at": r["started_at"],
                        "ended_at": r["ended_at"],
                        "end_reason": r["end_reason"],
                        "polls": r["polls"],
                        "first_poll_at": start.isoformat() if start else None,
                        "last_poll_at": end.isoformat() if end else None,
                    }
                )
            return result
        finally:
            store.close()

    @app.get("/api/replay")
    def replay(session_id: int, at: str = Query(..., description="ISO 8601 receipt time")) -> dict:
        store = reader()
        try:
            try:
                store.resolve_session(str(session_id))
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from None
            # A fresh builder and read-only connection; live state is untouched
            cache = RegistryCache(HangarbayRegistry.open_optional(config.enrich.hangarbay_db))
            try:
                return replay_state(store, session_id, _parse_at(at), config, cache)
            finally:
                if cache.registry:
                    cache.registry.close()
        finally:
            store.close()

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")

    return app
