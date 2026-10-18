"""Exports with source and timestamp provenance. See docs/exports.md.

Exports never include the home coordinates or distances from home.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from airspace import __version__
from airspace.config import Config
from airspace.models import from_iso, to_iso, utcnow
from airspace.replay import RegistryCache
from airspace.state import ATTRIBUTION, breaks_between
from airspace.storage import OBSERVATION_COLUMNS, Store

EXPORT_SCHEMA_VERSION = 1

TERMS = {"adsbfi": "Personal, noncommercial use only; cite adsb.fi with a link to https://adsb.fi"}

FIELDS = {
    "aircraft_id": "Provider aircraft identifier, normally the ICAO 24-bit address in lowercase hex. "
    "A leading '~' marks a non-ICAO address such as a TIS-B track file.",
    "received_at": "UTC time the local collector received the response.",
    "provider_at": "UTC response time reported by the provider.",
    "position_at": "UTC position time: provider_at minus the provider's reported position age. Null if unknown.",
    "position_time_basis": "'derived_age', 'explicit' or 'unknown'.",
    "new_position": "True when position_at advanced past this aircraft's previous position in the session; "
    "false for repeated (cached) or out-of-order positions.",
    "latitude": "WGS84 degrees.",
    "longitude": "WGS84 degrees.",
    "altitude_baro_ft": "Barometric altitude in feet; not height above ground. Null on the ground or when not reported.",
    "altitude_geom_ft": "GNSS (geometric) altitude in feet.",
    "on_ground": "True, false or null (unknown).",
    "ground_speed_kt": "Ground speed in knots.",
    "track_deg": "Ground track in degrees true; not necessarily heading.",
    "vertical_rate_fpm": "Barometric vertical rate in feet per minute, or geometric when barometric is absent.",
    "callsign": "Broadcast callsign, whitespace stripped.",
    "registration": "Registration from the provider's aircraft database.",
    "aircraft_type": "ICAO type designator from the provider's aircraft database.",
    "aircraft_description": "Type description from the provider's aircraft database.",
    "category": "ADS-B emitter category (A7 = rotorcraft; C1-C5 = surface vehicles and obstacles).",
    "position_source": "Provider position source, e.g. adsb_icao, adsr_icao, mlat, tisb_trackfile.",
    "owner_operator": "Owner or operator from the provider's aircraft database. Null when the FAA registry lists "
    "individuals as owners (role 'private').",
    "squawk": "Transponder code.",
    "emergency": "Reported emergency status.",
    "classification": "rotorcraft, gyroplane, surface, aircraft or unknown.",
    "classification_rule": "Source of the classification: adsb_category, faa_registry or none.",
    "operator": "Operator from the Airspace catalog (e.g. 'LAPD Air Support Division'). Null when unknown or private.",
    "operator_short": "Short operator name for labels (e.g. 'LAPD').",
    "role": "police, fire, medical, news, military, tour, utility, training or private; null when unknown.",
    "role_rule": "How the operator was identified: catalog_hex, catalog_registration, catalog_callsign, "
    "owner_pattern (registered owner's name) or registry_owner_type (private).",
}

EXPORT_COLUMNS = [c for c in OBSERVATION_COLUMNS if c != "provider"]


def _rows(
    store: Store, session_id: int, start: datetime | None, end: datetime | None, aircraft: list[str] | None
) -> list[dict]:
    where = ["session_id = ?"]
    params: list = [session_id]
    if start is not None:
        where.append("received_at >= ?")
        params.append(to_iso(start))
    if end is not None:
        where.append("received_at <= ?")
        params.append(to_iso(end))
    if aircraft:
        where.append(f"aircraft_id IN ({', '.join('?' * len(aircraft))})")
        params.extend(a.lower() for a in aircraft)
    sql = f"SELECT * FROM observations WHERE {' AND '.join(where)} ORDER BY received_at, id"
    rows = []
    for r in store.conn.execute(sql, params):
        row = {c: r[c] for c in EXPORT_COLUMNS}
        row["new_position"] = bool(r["new_position"])
        if row["on_ground"] is not None:
            row["on_ground"] = bool(row["on_ground"])
        rows.append(row)
    return rows


def _classify(rows: list[dict], cache: RegistryCache) -> None:
    categories: dict[str, str | None] = {}
    for r in rows:
        categories.setdefault(r["aircraft_id"], None)
        if r["category"]:
            categories[r["aircraft_id"]] = r["category"]
    cache.resolve(cache.unresolved(categories))
    # Identify each aircraft once, from its latest identifying fields
    latest: dict[str, dict] = {}
    for r in rows:
        prev = latest.setdefault(r["aircraft_id"], {})
        for key in ("registration", "callsign", "owner_operator"):
            if r[key]:
                prev[key] = r[key]
    operators = {}
    for aid, fields in latest.items():
        kind = cache.classify(aid, categories[aid]).kind
        operators[aid] = cache.operator(
            aid, kind=kind, registration=fields.get("registration"), callsign=fields.get("callsign"),
            owner=fields.get("owner_operator"),
        )  # fmt: skip
    for r in rows:
        c = cache.classify(r["aircraft_id"], categories[r["aircraft_id"]])
        r["classification"], r["classification_rule"] = c.kind, c.rule
        op = operators[r["aircraft_id"]]
        r["operator"] = op.name if op else None
        r["operator_short"] = op.short if op else None
        r["role"] = op.role if op else None
        r["role_rule"] = op.rule if op else None
        if op and op.private:
            r["owner_operator"] = None


def _coverage_gaps(store: Store, session_id: int, start: datetime | None, end: datetime | None) -> list[dict]:
    """Failed polls and host sleep in the range, so consumers don't mistake an outage for an empty sky."""
    gaps = []
    for p in store.conn.execute(
        "SELECT kind, requested_at, received_at, error_kind, error, gap_s FROM polls WHERE session_id = ? "
        "AND (kind = 'sleep_gap' OR error_kind IS NOT NULL) ORDER BY id",
        (session_id,),
    ):
        at = from_iso(p["received_at"] or p["requested_at"])
        if (start and at < start) or (end and at > end):
            continue
        if p["kind"] == "sleep_gap":
            gaps.append({"type": "host_sleep", "end": to_iso(at), "duration_s": p["gap_s"]})
        else:
            gaps.append({"type": "failed_poll", "at": to_iso(at), "error_kind": p["error_kind"], "error": p["error"]})
    return gaps


def provenance(
    store: Store,
    session_id: int,
    config: Config,
    *,
    start: datetime | None,
    end: datetime | None,
    aircraft: list[str] | None,
    extra: dict | None = None,
) -> dict:
    session = store.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    provider = session["provider"]
    # The session's own config snapshot, minus the home point
    recorded = json.loads(session["config_json"] or "{}")
    collector = recorded.get("collector", {})
    area = recorded.get("area", {})
    first, last = store.session_bounds(session_id)
    return {
        "generator": f"airspace {__version__}",
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "generated_at": to_iso(utcnow()),
        "source": {"provider": provider, **ATTRIBUTION.get(provider, {}), "terms": TERMS.get(provider)},
        "session": {
            "id": session_id,
            "mode": session["mode"],
            "started_at": session["started_at"],
            "ended_at": session["ended_at"],
            "end_reason": session["end_reason"],
            "first_poll_at": to_iso(first),
            "last_poll_at": to_iso(last),
        },
        "collection": {
            "poll_interval_s": collector.get("poll_interval_s"),
            "collection_radius_nm": area.get("collection_radius_nm"),
            "visible_radius_mi": area.get("visible_radius_mi"),
            "home": "omitted from exports",
        },
        "range": {"start": to_iso(start), "end": to_iso(end), "time_field": "received_at"},
        "filters": {"aircraft": aircraft or None},
        "timestamps": "UTC ISO 8601. received_at is local receipt; position_at is the provider's response time "
        "minus its reported position age.",
        "coverage_gaps": _coverage_gaps(store, session_id, start, end),
        **(extra or {}),
    }


def to_json(
    store: Store, session_id: int, config: Config, cache: RegistryCache, *, start=None, end=None, aircraft=None
) -> dict:
    rows = _rows(store, session_id, start, end, aircraft)
    _classify(rows, cache)
    return {
        "metadata": provenance(store, session_id, config, start=start, end=end, aircraft=aircraft),
        "fields": FIELDS,
        "observations": rows,
    }


def _tracks(rows: list[dict], trail_gap_s: float) -> list[dict]:
    """Track segments per aircraft from new positions, split by the same rule as map trails."""
    by_aircraft: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["new_position"] and r["position_at"]:
            by_aircraft[r["aircraft_id"]].append(r)
    features = []
    for aircraft_id, points in by_aircraft.items():
        points.sort(key=lambda r: r["position_at"])
        segments: list[list[dict]] = []
        for p in points:
            if segments:
                q = segments[-1][-1]
                if not breaks_between(
                    from_iso(q["position_at"]), q["latitude"], q["longitude"],
                    from_iso(p["position_at"]), p["latitude"], p["longitude"], trail_gap_s,
                ):  # fmt: skip
                    segments[-1].append(p)
                    continue
            segments.append([p])
        for index, segment in enumerate(segments, start=1):
            last = segment[-1]
            coords = [[p["longitude"], p["latitude"]] for p in segment]
            geometry = (
                {"type": "LineString", "coordinates": coords}
                if len(coords) > 1
                else {"type": "Point", "coordinates": coords[0]}
            )
            features.append(
                {
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": {
                        "aircraft_id": aircraft_id,
                        "segment": index,
                        "registration": last["registration"],
                        "callsigns": sorted({p["callsign"] for p in segment if p["callsign"]}),
                        "aircraft_type": last["aircraft_type"],
                        "aircraft_description": last["aircraft_description"],
                        "classification": last["classification"],
                        "classification_rule": last["classification_rule"],
                        "operator": last["operator"],
                        "operator_short": last["operator_short"],
                        "role": last["role"],
                        "role_rule": last["role_rule"],
                        "start": segment[0]["position_at"],
                        "end": last["position_at"],
                        "points": len(segment),
                        # Parallel to coordinates (the togeojson coordTimes convention)
                        "coordinate_times": [p["position_at"] for p in segment],
                        "altitudes_baro_ft": [p["altitude_baro_ft"] for p in segment],
                        "position_sources": sorted({p["position_source"] for p in segment if p["position_source"]}),
                    },
                }
            )
    return features


def to_geojson(
    store: Store,
    session_id: int,
    config: Config,
    cache: RegistryCache,
    *,
    geometry: str = "tracks",
    start=None,
    end=None,
    aircraft=None,
) -> dict:
    if geometry not in ("tracks", "points"):
        raise ValueError("geometry must be 'tracks' or 'points'")
    rows = _rows(store, session_id, start, end, aircraft)
    _classify(rows, cache)
    trail_gap_s = config.freshness.trail_gap_s
    if geometry == "tracks":
        features = _tracks(rows, trail_gap_s)
        extra = {
            "geometry": "tracks",
            "track_rules": f"New positions only, ordered by position_at; a segment ends at a gap longer than "
            f"{trail_gap_s:g}s or an implied speed above 800 kt. Gaps are never joined.",
        }
    else:
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["longitude"], r["latitude"]]},
                "properties": {k: v for k, v in r.items() if k not in ("latitude", "longitude")},
            }
            for r in rows
        ]
        extra = {"geometry": "points", "fields": FIELDS}
    return {
        "type": "FeatureCollection",
        # Foreign member; GeoJSON readers ignore it
        "metadata": provenance(store, session_id, config, start=start, end=end, aircraft=aircraft, extra=extra),
        "features": features,
    }


# FlightTracer reads trace.csv from a run folder (flight_tracer/saved.py, checked at v0.4.2)
FLIGHTTRACER_COLUMNS = [
    "point_time_utc", "lat", "lon", "altitude", "altitude_is_geometric", "ground_speed", "indicated_airspeed",
    "track", "vertical_rate", "vertical_rate_is_geometric", "roll", "icao", "registration", "model", "desc",
    "owner_op", "call_sign", "squawk", "emergency", "leg_id", "flight_leg", "leg_detection", "position_source",
    "is_stale",
]  # fmt: skip


def to_flighttracer(
    store: Store,
    session_id: int,
    config: Config,
    folder: Path,
    aircraft_id: str,
    *,
    start=None,
    end=None,
    cache: RegistryCache | None = None,
) -> dict:
    """Write a FlightTracer run folder for one aircraft; `flight-tracer render <folder>` draws it.

    Airborne new positions only (FlightTracer drops ground points by default). One leg: Airspace
    does not detect flights, so leg_detection is 'unmarked'; FlightTracer dashes long gaps itself.
    """
    aircraft_id = aircraft_id.lower()
    rows = [
        r
        for r in _rows(store, session_id, start, end, [aircraft_id])
        if r["new_position"] and r["position_at"] and r["on_ground"] is not True
    ]
    if not rows:
        raise LookupError(f"no airborne positions for {aircraft_id} in session {session_id}")
    # Hides private owners' names; without a cache, owner_op stays as the provider sent it
    if cache is not None:
        _classify(rows, cache)
    rows.sort(key=lambda r: r["position_at"])
    callsign = next((r["callsign"] for r in reversed(rows) if r["callsign"]), None) or "UNKNOWN"
    fade_s = config.freshness.fade_s

    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "trace.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FLIGHTTRACER_COLUMNS)
        writer.writeheader()
        for r in rows:
            age = (from_iso(r["provider_at"]) - from_iso(r["position_at"])).total_seconds() if r["provider_at"] else None
            writer.writerow(
                {
                    "point_time_utc": r["position_at"],
                    "lat": r["latitude"],
                    "lon": r["longitude"],
                    "altitude": r["altitude_baro_ft"],
                    "altitude_is_geometric": False,
                    "ground_speed": r["ground_speed_kt"],
                    "track": r["track_deg"],
                    "vertical_rate": r["vertical_rate_fpm"],
                    "vertical_rate_is_geometric": False,
                    "icao": aircraft_id,
                    "registration": r["registration"],
                    "model": r["aircraft_type"],
                    "desc": r["aircraft_description"],
                    "owner_op": r["owner_operator"],
                    "call_sign": r["callsign"] or callsign,
                    "squawk": r["squawk"],
                    "emergency": r["emergency"],
                    "leg_id": 1,
                    "flight_leg": f"{callsign}_leg1",
                    "leg_detection": "unmarked",
                    "position_source": r["position_source"],
                    # Position older than the fade threshold when received
                    "is_stale": age is not None and age > fade_s,
                }
            )
    meta = provenance(
        store, session_id, config, start=start, end=end, aircraft=[aircraft_id],
        extra={"format": "flighttracer run folder (trace.csv)", "points": len(rows)},
    )  # fmt: skip
    (folder / "airspace.json").write_text(json.dumps(meta, indent=2))
    return meta
