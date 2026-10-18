"""Local HTTP API. One process, one collector, bound to 127.0.0.1 by the CLI."""

from __future__ import annotations

import asyncio
import logging
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from airspace.collector import Clock, Collector, make_fast_trigger
from airspace.config import Config
from airspace import export as export_mod
from airspace import procinfo
from airspace.models import PollResult, from_iso, utcnow
from airspace.providers import get_provider
from airspace.providers.base import USER_AGENT
from airspace.replay import RegistryCache, replay_state
from airspace.state import StateBuilder
from airspace.storage import Store

log = logging.getLogger(__name__)

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def frontend_problem(dist: Path = WEB_DIST) -> str | None:
    """A missing dashboard build, or one older than its source (web/dist is built locally,
    so `git pull` doesn't update it)."""
    web = dist.parent
    if not (web / "src").is_dir():
        return None  # an installed package without the frontend source
    built = dist / "index.html"
    rebuild = "run `cd web && npm install && npm run build`"
    if not built.exists():
        return f"the dashboard isn't built; {rebuild}"
    sources = [p for p in (web / "src").rglob("*") if p.is_file()]
    sources += [p for p in (web / "index.html", web / "package.json") if p.exists()]
    if any(p.stat().st_mtime > built.stat().st_mtime for p in sources):
        return f"the dashboard build is older than its source; {rebuild}"
    return None


class LiveService:
    """Owns the collector task and the live state builder."""

    def __init__(self, config: Config, *, transport: httpx.AsyncBaseTransport | None = None, clock: Clock | None = None):
        self.config = config
        self.transport = transport
        self.clock = clock
        self.cache = RegistryCache.from_config(config)
        # DuckDB connections are not safe for concurrent use; keep lookups on one thread
        self._lookup_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="registry")
        self.state = StateBuilder(config, identity=self.cache, mode="live")
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
            self.config, get_provider(settings.provider), self.store, self.client, clock=self.clock, on_poll=self._on_poll,
            fast_trigger=make_fast_trigger(self.config, self.cache),
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
        self.cache.close()
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

    class ReplayRegistry:
        """One registry cache for replay and export requests; lookups serialized (DuckDB isn't thread-safe)."""

        def __init__(self):
            self.cache = RegistryCache.from_config(config)
            self.lock = threading.Lock()

        def close(self):
            self.cache.close()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.replay_registry = ReplayRegistry()
        app.state.live = None
        if mode == "live":
            app.state.live = LiveService(config, transport=transport, clock=clock)
            await app.state.live.start()
        try:
            yield
        finally:
            if app.state.live:
                await app.state.live.shutdown()
            app.state.replay_registry.close()

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
            "fast_polling": bool(service.collector and service.collector.fast),
            "registry_lookups": service.cache.registry is not None,
            "catalog_aircraft": len(service.cache.catalog) if service.cache.catalog else 0,
            "process": procinfo.memory().as_dict(),
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
            shared = app.state.replay_registry
            with shared.lock:
                return replay_state(store, session_id, _parse_at(at), config, shared.cache)
        finally:
            store.close()

    def session_store(session_id: int) -> Store:
        store = reader()
        try:
            store.resolve_session(str(session_id))
        except LookupError as exc:
            store.close()
            raise HTTPException(404, str(exc)) from None
        return store

    @app.get("/api/sessions/{session_id}/timeline")
    def timeline(session_id: int) -> dict:
        """Per-poll outcomes for the replay scrubber. Times are epoch milliseconds (receipt, else request)."""
        store = session_store(session_id)
        try:
            rows = store.conn.execute(
                """SELECT p.kind, COALESCE(p.received_at, p.requested_at) AS t, p.error_kind, p.accepted, p.gap_s,
                          (SELECT COUNT(*) FROM observations o WHERE o.poll_id = p.id AND o.category = 'A7') AS a7
                   FROM polls p WHERE p.session_id = ? ORDER BY p.id""",
                (session_id,),
            ).fetchall()
        finally:
            store.close()

        def ms(value: str) -> int:
            return round(from_iso(value).timestamp() * 1000)

        polls = [r for r in rows if r["kind"] == "poll"]
        return {
            "session_id": session_id,
            "start": ms(polls[0]["t"]) if polls else None,
            "end": ms(polls[-1]["t"]) if polls else None,
            "polls": {
                "t": [ms(r["t"]) for r in polls],
                "ok": [r["error_kind"] is None for r in polls],
                "aircraft": [r["accepted"] for r in polls],
                # ADS-B category A7 only; registry-classified rotorcraft aren't counted here
                "rotorcraft": [r["a7"] for r in polls],
            },
            "gaps": [
                {"kind": r["kind"], "start": ms(r["t"]) - round((r["gap_s"] or 0) * 1000), "end": ms(r["t"])}
                for r in rows
                if r["kind"] != "poll"
            ],
        }

    @app.get("/api/sessions/{session_id}/export")
    def export(
        session_id: int,
        format: str = Query("geojson", pattern="^(geojson|json)$"),
        geometry: str = Query("tracks", pattern="^(tracks|points|behaviors)$"),
        start: str | None = None,
        end: str | None = None,
        aircraft: str | None = Query(None, description="Comma-separated aircraft IDs"),
    ) -> JSONResponse:
        store = session_store(session_id)
        ids = [a.strip().lower() for a in aircraft.split(",") if a.strip()] if aircraft else None
        window = {
            "start": _parse_at(start) if start else None,
            "end": _parse_at(end) if end else None,
            "aircraft": ids,
        }
        shared = app.state.replay_registry
        try:
            with shared.lock:
                if format == "json":
                    body = export_mod.to_json(store, session_id, config, shared.cache, **window)
                else:
                    body = export_mod.to_geojson(store, session_id, config, shared.cache, geometry=geometry, **window)
        finally:
            store.close()
        suffix = "json" if format == "json" else f"{geometry}.geojson"
        scope = f"-{ids[0]}" if ids and len(ids) == 1 else ""
        filename = f"airspace-session-{session_id}{scope}-{suffix}"
        media = "application/geo+json" if format == "geojson" else "application/json"
        return JSONResponse(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")

    return app
