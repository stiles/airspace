"""Command-line interface."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import signal
from datetime import datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer

from airspace import config as cfg
from airspace import report as report_mod
from airspace.catalog import Catalog, CatalogError
from airspace.collector import Collector, make_fast_trigger
from airspace.enrich import HangarbayRegistry
from airspace.lock import CollectorLock, CollectorLocked
from airspace.models import PollResult, to_iso
from airspace.providers import get_provider
from airspace.replay import RegistryCache
from airspace.providers.base import USER_AGENT
from airspace.storage import Store

app = typer.Typer(no_args_is_help=True, add_completion=False, help="What is flying near home right now.")

ConfigOption = Annotated[Path | None, typer.Option("--config", "-c", help="Config file (default airspace.toml)")]


def parse_duration(text: str) -> float:
    """'2h', '90m', '1h30m', '45s' -> seconds."""
    parts = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?", text.strip())
    if not parts or not any(parts.groups()):
        raise typer.BadParameter(f"can't parse duration {text!r}; use forms like 2h, 90m or 1h30m")
    h, m, s = (float(g) if g else 0.0 for g in parts.groups())
    return h * 3600 + m * 60 + s


def _load(config_file: Path | None) -> cfg.Config:
    try:
        return cfg.load(config_file)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from None
    except ValueError as exc:
        typer.echo(f"Invalid configuration:\n{exc}", err=True)
        raise typer.Exit(2) from None


def _catalog(config: cfg.Config) -> Catalog:
    try:
        return Catalog.load(config.enrich.catalogs)
    except CatalogError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None


@app.command()
def init(
    config_file: ConfigOption = None,
    demo: Annotated[bool, typer.Option(help="Use the approximate West LA demo center")] = False,
    force: Annotated[bool, typer.Option(help="Overwrite an existing config")] = False,
) -> None:
    """Write a configuration file with your home coordinates."""
    path = cfg.config_path(config_file)
    if path.exists() and not force:
        typer.echo(f"{path} already exists; use --force to overwrite.", err=True)
        raise typer.Exit(1)
    if demo:
        lat, lon, label = cfg.DEMO_LATITUDE, cfg.DEMO_LONGITUDE, "West LA demo (approximate)"
    else:
        typer.echo("Enter the home point in decimal degrees. It stays in this local file, outside Git.")
        lat = typer.prompt("Latitude", type=float)
        lon = typer.prompt("Longitude", type=float)
        label = typer.prompt("Label", default="Home")
    try:
        text = cfg.render(lat, lon, label)
    except ValueError as exc:
        typer.echo(f"Invalid coordinates:\n{exc}", err=True)
        raise typer.Exit(2) from None
    path.write_text(text)
    path.chmod(0o600)
    typer.echo(f"Wrote {path}")


def _status_line(result: PollResult, fast: bool = False) -> str:
    stamp = result.requested_at.strftime("%H:%M:%S")
    if result.ok:
        rejected = f", {len(result.rejections)} rejected" if result.rejections else ""
        return f"{stamp}  {len(result.observations):3d} aircraft{rejected}  {result.latency_ms:.0f} ms{'  fast' if fast else ''}"
    return f"{stamp}  ERROR {result.error}"


async def _collect(config: cfg.Config, duration_s: float | None, quiet: bool) -> tuple[int, str]:
    settings = config.collector
    provider = get_provider(settings.provider)
    store = Store(config.storage.data_dir, store_raw=settings.store_raw)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    timeout = httpx.Timeout(settings.timeout_s)
    # Headless: the catalog without registry lookups (category A7 still counts as a helicopter)
    identity = RegistryCache(None, catalog=_catalog(config))
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
        collector: Collector | None = None
        on_poll = None if quiet else (lambda r: typer.echo(_status_line(r, bool(collector and collector.fast))))
        collector = Collector(
            config, provider, store, client, on_poll=on_poll, fast_trigger=make_fast_trigger(config, identity)
        )
        session_id = collector.start_session("collect")
        reason = "error"
        try:
            reason = await collector.run(duration_s=duration_s, stop=stop)
        finally:
            store.end_session(session_id, reason)
            store.close()
    return session_id, reason


@app.command()
def collect(
    config_file: ConfigOption = None,
    duration: Annotated[str | None, typer.Option(help="Stop after this long (wall clock), e.g. 2h")] = None,
    quiet: Annotated[bool, typer.Option(help="Suppress per-poll status lines")] = False,
) -> None:
    """Record nearby aircraft headlessly until the duration ends or Ctrl-C."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = _load(config_file)
    duration_s = parse_duration(duration) if duration else None
    c = config.collector
    typer.echo(
        f"Collecting from {c.provider} every {c.poll_interval_s:g}s"
        + (f" ({c.fast_interval_s:g}s with a helicopter nearby)" if c.fast_interval_s else "")
        + f" within {config.area.collection_radius_nm:g} NM"
        + (f" for {duration}" if duration else "")
        + ". Data: adsb.fi (https://adsb.fi), personal noncommercial use."
    )
    try:
        with CollectorLock(config.storage.data_dir):
            session_id, reason = asyncio.run(_collect(config, duration_s, quiet))
    except CollectorLocked as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"Session {session_id} ended: {reason}. Run `airspace report --session {session_id}`.")
    if reason.startswith("paused"):
        raise typer.Exit(3)


@app.command()
def report(
    config_file: ConfigOption = None,
    session: Annotated[str, typer.Option(help="Session ID or 'latest'")] = "latest",
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON instead of Markdown")] = False,
    registry: Annotated[bool, typer.Option(help="Look up FAA registry via hangarbay when available")] = True,
) -> None:
    """Summarize coverage for a recorded session."""
    config = _load(config_file)
    store = Store(config.storage.data_dir)
    try:
        session_id = store.resolve_session(session)
    except (LookupError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    catalog = _catalog(config)
    reg = HangarbayRegistry.open_optional(config.enrich.hangarbay_db) if registry else None
    try:
        data = report_mod.build(store, session_id, config, reg, catalog=catalog)
    finally:
        if reg:
            reg.close()
        store.close()
    if as_json:
        data["gate"] = report_mod.gate(data, config)
        typer.echo(json.dumps(data, default=lambda o: getattr(o, "__dict__", None) or str(o), indent=2))
    else:
        typer.echo(report_mod.render(data, config), nl=False)


@app.command()
def sessions(config_file: ConfigOption = None) -> None:
    """List recorded sessions."""
    config = _load(config_file)
    store = Store(config.storage.data_dir)
    rows = store.sessions()
    store.close()
    if not rows:
        typer.echo("No sessions recorded.")
        return
    for r in rows:
        typer.echo(
            f"{r['id']:>4}  {r['started_at']}  {r['provider']:<8} {r['mode']:<8} {r['polls']:>6} polls  "
            f"{r['end_reason'] or 'open'}"
        )


def _url_time(value) -> str:
    return to_iso(value).replace("+00:00", "Z") if value else ""


PortOption = Annotated[int, typer.Option(help="Port on 127.0.0.1")]


def _serve(config: cfg.Config, mode: str, port: int) -> None:
    import uvicorn

    from airspace.api import create_app

    # One worker and no auto-reload, so a second collector can never start
    uvicorn.run(create_app(config, mode=mode), host="127.0.0.1", port=port, workers=1, reload=False)


@app.command()
def serve(config_file: ConfigOption = None, port: PortOption = 8000) -> None:
    """Run the collector and local API/dashboard together."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = _load(config_file)
    catalog = _catalog(config)
    typer.echo(f"Serving http://127.0.0.1:{port} (API docs at /api/docs). Data: adsb.fi (https://adsb.fi).")
    typer.echo(f"Catalog: {len(catalog)} aircraft, {len(catalog.patterns)} owner patterns.")
    try:
        with CollectorLock(config.storage.data_dir):
            _serve(config, "live", port)
    except CollectorLocked as exc:
        typer.echo(f"{exc}. Stop it first, or use `airspace replay` to browse recordings.", err=True)
        raise typer.Exit(1) from None


@app.command()
def replay(
    config_file: ConfigOption = None,
    port: PortOption = 8000,
    session: Annotated[str, typer.Option(help="Session ID or 'latest' (shown at startup)")] = "latest",
) -> None:
    """Serve recorded sessions with live acquisition disabled."""
    config = _load(config_file)
    _catalog(config)
    try:
        store = Store(config.storage.data_dir, read_only=True)
        session_id = store.resolve_session(session)
        start, end = store.session_bounds(session_id)
        store.close()
    except (FileNotFoundError, LookupError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(
        f"Replay mode, no provider requests. Session {session_id} spans {start} to {end}.\n"
        f"Try http://127.0.0.1:{port}/api/replay?session_id={session_id}&at={_url_time(end)}"
    )
    _serve(config, "replay", port)


@app.command()
def export(
    config_file: ConfigOption = None,
    session: Annotated[str, typer.Option(help="Session ID or 'latest'")] = "latest",
    format: Annotated[str, typer.Option(help="geojson, json or flighttracer")] = "geojson",
    geometry: Annotated[str, typer.Option(help="For geojson: tracks, points or behaviors")] = "tracks",
    aircraft: Annotated[list[str] | None, typer.Option(help="Aircraft ID (ICAO hex); repeatable")] = None,
    start: Annotated[str | None, typer.Option(help="ISO 8601 receipt time")] = None,
    end: Annotated[str | None, typer.Option(help="ISO 8601 receipt time")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="File, or folder for flighttracer")] = None,
) -> None:
    """Export recorded data with provenance (home coordinates are never included)."""
    from airspace import export as export_mod

    config = _load(config_file)
    if format not in ("geojson", "json", "flighttracer"):
        raise typer.BadParameter("format must be geojson, json or flighttracer")
    try:
        store = Store(config.storage.data_dir, read_only=True)
        session_id = store.resolve_session(session)
    except (FileNotFoundError, LookupError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    window = {
        "start": datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None,
        "end": datetime.fromisoformat(end.replace("Z", "+00:00")) if end else None,
    }
    try:
        cache = RegistryCache.from_config(config)
    except CatalogError as exc:
        store.close()
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    try:
        if format == "flighttracer":
            if not aircraft or len(aircraft) != 1:
                raise typer.BadParameter("flighttracer export needs exactly one --aircraft")
            folder = output or Path(f"airspace-{aircraft[0].lower()}-session-{session_id}")
            try:
                meta = export_mod.to_flighttracer(store, session_id, config, folder, aircraft[0], cache=cache, **window)
            except LookupError as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(1) from None
            typer.echo(f"Wrote {meta['points']} positions to {folder}/trace.csv. Run: flight-tracer render {folder}")
            return
        if format == "json":
            body = export_mod.to_json(store, session_id, config, cache, aircraft=aircraft, **window)
            count = len(body["observations"])
        else:
            body = export_mod.to_geojson(
                store, session_id, config, cache, geometry=geometry, aircraft=aircraft, **window
            )
            count = len(body["features"])
    finally:
        cache.close()
        store.close()
    suffix = "json" if format == "json" else f"{geometry}.geojson"
    path = output or Path(f"airspace-session-{session_id}-{suffix}")
    path.write_text(json.dumps(body, indent=1))
    typer.echo(f"Wrote {count} {'observations' if format == 'json' else 'features'} to {path}")


@app.command()
def catalog(config_file: ConfigOption = None) -> None:
    """Check the operator catalogs and compare them with the FAA registry when available."""
    from collections import Counter

    config = _load(config_file)
    cat = _catalog(config)
    typer.echo(f"{len(cat)} aircraft and {len(cat.patterns)} owner patterns from {len(cat.files)} files:")
    for file in cat.files:
        typer.echo(f"  {file}")
    by_role = Counter(op.role for _, _, op in cat.rows)
    typer.echo("Roles: " + ", ".join(f"{role} {n}" for role, n in by_role.most_common()))
    typer.echo(f"Unconfirmed identifications (no checked date): {sum(1 for *_, op in cat.rows if not op.checked)}")

    reg = HangarbayRegistry.open_optional(config.enrich.hangarbay_db)
    if reg is None:
        typer.echo("FAA registry not available; skipped the hex and registration check.")
        return
    try:
        found = reg.lookup([h for h, _, _ in cat.rows if h])
    finally:
        reg.close()
    problems = []
    for hex_id, registration, _ in cat.rows:
        if not hex_id:
            continue
        record = found.get(hex_id)
        label = f"{registration or '?'} ({hex_id})"
        if record is None:
            problems.append(f"{label}: hex not in the FAA registry (foreign, military or a typo?)")
        elif record.get("is_deregistered"):
            problems.append(f"{label}: deregistered")
        elif registration and f"N{record['n_number']}" != registration:
            problems.append(f"{label}: the registry has this hex as N{record['n_number']}")
    if problems:
        typer.echo(f"{len(problems)} registry problems:")
        for p in problems:
            typer.echo(f"  {p}")
        raise typer.Exit(1)
    typer.echo("Every hex matches its registration in the FAA registry.")
