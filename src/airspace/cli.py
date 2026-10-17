"""Command-line interface."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import signal
from pathlib import Path
from typing import Annotated

import httpx
import typer

from airspace import config as cfg
from airspace import report as report_mod
from airspace.collector import Collector
from airspace.enrich import HangarbayRegistry
from airspace.lock import CollectorLock, CollectorLocked
from airspace.models import PollResult, to_iso
from airspace.providers import get_provider
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


def _status_line(result: PollResult) -> str:
    stamp = result.requested_at.strftime("%H:%M:%S")
    if result.ok:
        rejected = f", {len(result.rejections)} rejected" if result.rejections else ""
        return f"{stamp}  {len(result.observations):3d} aircraft{rejected}  {result.latency_ms:.0f} ms"
    return f"{stamp}  ERROR {result.error}"


async def _collect(config: cfg.Config, duration_s: float | None, quiet: bool) -> tuple[int, str]:
    settings = config.collector
    provider = get_provider(settings.provider)
    store = Store(config.storage.data_dir, store_raw=settings.store_raw)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    on_poll = None if quiet else (lambda r: typer.echo(_status_line(r)))
    timeout = httpx.Timeout(settings.timeout_s)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
        collector = Collector(config, provider, store, client, on_poll=on_poll)
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
        f"Collecting from {c.provider} every {c.poll_interval_s:g}s within {config.area.collection_radius_nm:g} NM"
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
    reg = HangarbayRegistry.open_optional(config.enrich.hangarbay_db) if registry else None
    try:
        data = report_mod.build(store, session_id, config, reg)
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
    typer.echo(f"Serving http://127.0.0.1:{port} (API docs at /api/docs). Data: adsb.fi (https://adsb.fi).")
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
