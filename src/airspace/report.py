"""Coverage report for a recorded session (milestone 1 gate)."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from airspace import classify as cls
from airspace.config import Config
from airspace.geometry import METERS_PER_MILE, METERS_PER_NM, distance_m
from airspace.models import from_iso
from airspace.storage import Store

NULLABLE_FIELDS = [
    "position_at", "altitude_baro_ft", "altitude_geom_ft", "on_ground", "ground_speed_kt", "track_deg",
    "vertical_rate_fpm", "callsign", "registration", "aircraft_type", "category", "position_source",
]  # fmt: skip

# Thresholds for flagging a TIS-B track file that may shadow an ADS-B aircraft
TISB_MAX_DISTANCE_M = 0.5 * METERS_PER_NM
TISB_MAX_TRACK_DIFF = 5.0
TISB_MAX_ALT_DIFF_FT = 300

SUCCESS_TARGET = 0.95
CONTINUITY_NOTE = "share of intervals between new positions no longer than the trail gap threshold"


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[round(q * 100) - 1]


def _seconds(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None:
        return None
    return (later - earlier).total_seconds()


def _track_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def build(store: Store, session_id: int, config: Config, registry: cls.Registry | None = None) -> dict:
    conn = store.conn
    session = dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone())
    polls = [dict(r) for r in conn.execute("SELECT * FROM polls WHERE session_id = ? ORDER BY id", (session_id,))]
    rows = [
        dict(r)
        for r in conn.execute("SELECT * FROM observations WHERE session_id = ? ORDER BY poll_id", (session_id,))
    ]
    rejected = conn.execute(
        "SELECT reason, COUNT(*) n FROM rejections JOIN polls ON polls.id = rejections.poll_id "
        "WHERE session_id = ? GROUP BY reason ORDER BY n DESC",
        (session_id,),
    ).fetchall()

    home = config.home
    for r in rows:
        for key in ("received_at", "provider_at", "position_at"):
            r[key] = from_iso(r[key])
        r["distance_m"] = distance_m(home.latitude, home.longitude, r["latitude"], r["longitude"])
        r["visible"] = r["distance_m"] <= config.visible_radius_m

    # Classify each aircraft once using its most common reported category
    categories: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        categories[r["aircraft_id"]][r["category"]] += 1
    top_category = {
        aid: next((c for c, _ in counts.most_common() if c is not None), None) for aid, counts in categories.items()
    }
    classes = cls.classify_many(top_category, registry)
    for r in rows:
        r["class"] = classes[r["aircraft_id"]]

    airborne_rows = [r for r in rows if r["class"].kind != cls.SURFACE]

    return {
        "session": session,
        "requests": _requests(polls),
        "positions": _positions(airborne_rows, config),
        "aircraft": _aircraft(rows, airborne_rows, classes),
        "rotorcraft": _rotorcraft(airborne_rows, config),
        "null_rates": {
            f: (sum(r[f] is None for r in airborne_rows) / len(airborne_rows) if airborne_rows else None)
            for f in NULLABLE_FIELDS
        },
        "rejections": {r["reason"]: r["n"] for r in rejected},
        "gaps": _gaps(airborne_rows, config),
        "tisb_duplicates": _tisb_duplicates(airborne_rows),
        "storage": _storage(store, session_id, polls, len(rows)),
        "registry": registry is not None,
    }


def _requests(polls: list[dict]) -> dict:
    scheduled = [p for p in polls if p["kind"] == "poll"]
    ok = [p for p in scheduled if p["error_kind"] is None]
    latencies = [p["latency_ms"] for p in ok if p["latency_ms"] is not None]
    provider_times = [p["provider_at"] for p in ok]
    repeated = sum(1 for a, b in zip(provider_times, provider_times[1:]) if a == b)
    gaps = [p for p in polls if p["kind"] == "sleep_gap"]
    first = from_iso(scheduled[0]["requested_at"]) if scheduled else None
    last = from_iso(scheduled[-1]["requested_at"]) if scheduled else None
    return {
        "scheduled": len(scheduled),
        "succeeded": len(ok),
        "success_rate": len(ok) / len(scheduled) if scheduled else None,
        "errors": dict(Counter(p["error"] for p in scheduled if p["error_kind"])),
        "latency_ms_median": _quantile(latencies, 0.5),
        "latency_ms_p95": _quantile(latencies, 0.95),
        "repeated_provider_time": repeated,
        "empty_responses": sum(1 for p in ok if p["accepted"] == 0 and p["rejected"] == 0),
        "sleep_gaps": len(gaps),
        "sleep_gap_s": sum(p["gap_s"] or 0 for p in gaps),
        "span_s": _seconds(last, first),
    }


def _positions(rows: list[dict], config: Config) -> dict:
    ages = [a for r in rows if (a := _seconds(r["provider_at"], r["position_at"])) is not None]
    visible_ages = [a for r in rows if r["visible"] and (a := _seconds(r["provider_at"], r["position_at"])) is not None]
    # Parked aircraft report positions rarely; the gate looks at airborne (or unknown) traffic in the visible area
    airborne_visible_ages = [
        a
        for r in rows
        if r["visible"] and r["on_ground"] != 1 and (a := _seconds(r["provider_at"], r["position_at"])) is not None
    ]
    by_source: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r["visible"] and r["on_ground"] != 1 and (a := _seconds(r["provider_at"], r["position_at"])) is not None:
            by_source[r["position_source"] or "unknown"].append(a)
    lags = [lag for r in rows if (lag := _seconds(r["received_at"], r["provider_at"])) is not None]
    return {
        "observations": len(rows),
        "visible_observations": sum(r["visible"] for r in rows),
        "age_s_median": _quantile(ages, 0.5),
        "age_s_p95": _quantile(ages, 0.95),
        "visible_age_s_p95": _quantile(visible_ages, 0.95),
        "airborne_visible_observations": len(airborne_visible_ages),
        "airborne_visible_age_s_median": _quantile(airborne_visible_ages, 0.5),
        "airborne_visible_age_s_p95": _quantile(airborne_visible_ages, 0.95),
        "airborne_visible_age_by_source": {
            src: {"observations": len(v), "p95_s": _quantile(v, 0.95)} for src, v in sorted(by_source.items())
        },
        "over_fade_share": sum(a > config.freshness.fade_s for a in ages) / len(ages) if ages else None,
        "receipt_lag_s_median": _quantile(lags, 0.5),
        "receipt_lag_s_p95": _quantile(lags, 0.95),
        "repeated_position_share": sum(not r["new_position"] for r in rows) / len(rows) if rows else None,
        "unknown_time": sum(r["position_at"] is None for r in rows),
        "time_flags": dict(Counter(r["time_flag"] for r in rows if r["time_flag"])),
    }


def _aircraft(rows: list[dict], airborne_rows: list[dict], classes: dict[str, cls.Classification]) -> dict:
    airborne_ids = {r["aircraft_id"] for r in airborne_rows}
    return {
        "unique": len(airborne_ids),
        "unique_visible": len({r["aircraft_id"] for r in airborne_rows if r["visible"]}),
        "surface_excluded": len({r["aircraft_id"] for r in rows}) - len(airborne_ids),
        "by_class": dict(Counter(f"{c.kind} ({c.rule})" for aid, c in classes.items() if aid in airborne_ids)),
    }


def _by_aircraft(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        grouped[r["aircraft_id"]].append(r)
    return grouped


def _new_position_intervals(obs: list[dict]) -> list[float]:
    times = sorted(r["position_at"] for r in obs if r["new_position"] and r["position_at"] is not None)
    return [(b - a).total_seconds() for a, b in zip(times, times[1:])]


def _rotorcraft(rows: list[dict], config: Config) -> list[dict]:
    result = []
    for aid, obs in _by_aircraft(rows).items():
        c = obs[0]["class"]
        if c.kind not in (cls.ROTORCRAFT, cls.GYROPLANE):
            continue
        intervals = _new_position_intervals(obs)
        last = obs[-1]
        result.append(
            {
                "aircraft_id": aid,
                "registration": last["registration"],
                "callsign": last["callsign"],
                "type": last["aircraft_description"] or last["aircraft_type"],
                "class": c.kind,
                "rule": c.rule,
                "observations": len(obs),
                "new_positions": sum(r["new_position"] for r in obs),
                "first_seen": min(r["received_at"] for r in obs),
                "last_seen": max(r["received_at"] for r in obs),
                "max_interval_s": max(intervals) if intervals else None,
                "continuity": (
                    sum(i <= config.freshness.trail_gap_s for i in intervals) / len(intervals) if intervals else None
                ),
                "min_distance_mi": min(r["distance_m"] for r in obs) / METERS_PER_MILE,
                "visible": any(r["visible"] for r in obs),
            }
        )
    return sorted(result, key=lambda r: -r["observations"])


def _gaps(rows: list[dict], config: Config) -> dict:
    threshold = config.freshness.trail_gap_s
    gap_lengths = []
    aircraft_with_gaps = 0
    for obs in _by_aircraft(rows).values():
        gaps = [i for i in _new_position_intervals(obs) if i > threshold]
        gap_lengths.extend(gaps)
        aircraft_with_gaps += bool(gaps)
    return {
        "threshold_s": threshold,
        "count": len(gap_lengths),
        "aircraft_with_gaps": aircraft_with_gaps,
        "median_s": _quantile(gap_lengths, 0.5),
        "max_s": max(gap_lengths) if gap_lengths else None,
    }


def _tisb_duplicates(rows: list[dict]) -> list[dict]:
    by_poll: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_poll[r["poll_id"]].append(r)
    pairs: Counter = Counter()
    for obs in by_poll.values():
        tisb = [r for r in obs if (r["position_source"] or "").startswith("tisb")]
        others = [r for r in obs if not (r["position_source"] or "").startswith("tisb")]
        for t in tisb:
            for o in others:
                if t["track_deg"] is None or o["track_deg"] is None:
                    continue
                if _track_diff(t["track_deg"], o["track_deg"]) > TISB_MAX_TRACK_DIFF:
                    continue
                if (
                    t["altitude_baro_ft"] is not None
                    and o["altitude_baro_ft"] is not None
                    and abs(t["altitude_baro_ft"] - o["altitude_baro_ft"]) > TISB_MAX_ALT_DIFF_FT
                ):
                    continue
                if distance_m(t["latitude"], t["longitude"], o["latitude"], o["longitude"]) > TISB_MAX_DISTANCE_M:
                    continue
                pairs[(t["aircraft_id"], o["aircraft_id"], o["registration"])] += 1
    return [{"tisb_id": t, "aircraft_id": a, "registration": reg, "polls": n} for (t, a, reg), n in pairs.most_common()]


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _storage(store: Store, session_id: int, polls: list[dict], session_rows: int) -> dict:
    total_rows = store.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    db_bytes = sum(_file_size(Path(f"{store.db_path}{suffix}")) for suffix in ("", "-wal"))
    # Attribute database size to this session by its share of observation rows
    session_db_bytes = db_bytes * session_rows / total_rows if total_rows else 0
    raw_bytes = _file_size(store.raw_path(session_id))
    span = _requests(polls)["span_s"]
    hours = span / 3600 if span else None
    return {
        "db_bytes_estimate": session_db_bytes,
        "raw_bytes": raw_bytes,
        "db_bytes_per_hour": session_db_bytes / hours if hours else None,
        "raw_bytes_per_hour": raw_bytes / hours if hours else None,
    }


def gate(report: dict, config: Config) -> dict[str, str]:
    """Milestone 1 project targets; not provider guarantees."""
    req, pos = report["requests"], report["positions"]
    success = req["success_rate"]
    p95 = pos["airborne_visible_age_s_p95"]
    rotorcraft = report["rotorcraft"]
    if not rotorcraft:
        heli = "untested: no rotorcraft observed in this window"
    else:
        best = max(rotorcraft, key=lambda r: r["continuity"] or 0)
        cont = f"{best['continuity']:.0%}" if best["continuity"] is not None else "n/a"
        heli = (
            f"review: {len(rotorcraft)} rotorcraft; best continuity {cont} "
            f"({best['registration'] or best['aircraft_id']}, {best['new_positions']} positions)"
        )
    return {
        "requests": (
            "n/a" if success is None
            else f"{'PASS' if success >= SUCCESS_TARGET else 'FAIL'}: {success:.1%} succeeded (target 95%)"
        ),
        "position_age": (
            "n/a" if p95 is None
            else f"{'PASS' if p95 <= config.freshness.fade_s else 'FAIL'}: "
            f"airborne p95 in visible area {p95:.1f}s (target {config.freshness.fade_s:g}s)"
        ),
        "helicopter": heli,
    }  # fmt: skip


def _fmt(value, spec: str = "", suffix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value:{spec}}{suffix}"


def _fmt_time(value: datetime | None) -> str:
    return value.strftime("%H:%M:%SZ") if value else "n/a"


def render(report: dict, config: Config) -> str:
    s, req, pos, ac = report["session"], report["requests"], report["positions"], report["aircraft"]
    lines = [
        f"# Coverage report: session {s['id']}",
        "",
        f"Provider {s['provider']} ({s['mode']}), started {s['started_at']}, "
        f"ended {s['ended_at'] or 'still running or interrupted'}"
        + (f" ({s['end_reason']})" if s["end_reason"] else "")
        + f". Span {_fmt(req['span_s'] and req['span_s'] / 60, '.0f', ' min')}.",
        "",
        "## Gate (project targets, not provider guarantees)",
        "",
        *(f"- {k.replace('_', ' ').capitalize()}: {v}" for k, v in gate(report, config).items()),
        "",
        "## Requests",
        "",
        f"- Scheduled {req['scheduled']}, succeeded {req['succeeded']} ({_fmt(req['success_rate'], '.1%')})",
        f"- Latency median {_fmt(req['latency_ms_median'], '.0f', ' ms')}, p95 {_fmt(req['latency_ms_p95'], '.0f', ' ms')}",
        f"- Responses repeating the previous provider time (possible cache): {req['repeated_provider_time']}",
        f"- Successful responses with no aircraft: {req['empty_responses']}",
        f"- Host sleep gaps: {req['sleep_gaps']} ({req['sleep_gap_s']:.0f}s total)",
    ]
    for error, n in req["errors"].items():
        lines.append(f"- Error `{error}`: {n}")

    lines += [
        "",
        "## Positions (surface vehicles excluded)",
        "",
        f"- Observations {pos['observations']:,}, inside visible area {pos['visible_observations']:,}",
        f"- Position age at response: median {_fmt(pos['age_s_median'], '.1f', 's')}, "
        f"p95 {_fmt(pos['age_s_p95'], '.1f', 's')} (visible area p95 {_fmt(pos['visible_age_s_p95'], '.1f', 's')})",
        f"- Airborne in visible area ({pos['airborne_visible_observations']:,} observations): "
        f"median {_fmt(pos['airborne_visible_age_s_median'], '.1f', 's')}, "
        f"p95 {_fmt(pos['airborne_visible_age_s_p95'], '.1f', 's')}",
        "- By position source: "
        + (
            "; ".join(
                f"{src} {v['observations']} obs, p95 {_fmt(v['p95_s'], '.1f', 's')}"
                for src, v in pos["airborne_visible_age_by_source"].items()
            )
            or "n/a"
        ),
        f"- Share older than fade threshold ({config.freshness.fade_s:g}s): {_fmt(pos['over_fade_share'], '.1%')}",
        f"- Local receipt minus provider time: median {_fmt(pos['receipt_lag_s_median'], '.1f', 's')}, "
        f"p95 {_fmt(pos['receipt_lag_s_p95'], '.1f', 's')} (includes clock skew)",
        f"- Observations repeating a known position: {_fmt(pos['repeated_position_share'], '.1%')}",
        f"- Unknown position time: {pos['unknown_time']}; time flags: {pos['time_flags'] or 'none'}",
        "",
        "## Aircraft",
        "",
        f"- Unique {ac['unique']}, inside visible area {ac['unique_visible']}; surface vehicles excluded {ac['surface_excluded']}",
        f"- FAA registry lookups: {'on' if report['registry'] else 'off (hangarbay database or duckdb unavailable)'}",
    ]
    lines += [f"- {k}: {v}" for k, v in sorted(ac["by_class"].items(), key=lambda kv: -kv[1])]

    lines += ["", "## Rotorcraft", ""]
    if report["rotorcraft"]:
        lines += [
            f"Continuity is the {CONTINUITY_NOTE} ({config.freshness.trail_gap_s:g}s).",
            "",
            "| ID | Registration | Callsign | Type | Rule | Positions | Seen (UTC) | Max interval | Continuity | Closest | Visible |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for r in report["rotorcraft"]:
            lines.append(
                f"| {r['aircraft_id']} | {r['registration'] or ''} | {r['callsign'] or ''} | {r['type'] or ''} "
                f"| {r['rule']} | {r['new_positions']} | {_fmt_time(r['first_seen'])}–{_fmt_time(r['last_seen'])} "
                f"| {_fmt(r['max_interval_s'], '.0f', 's')} | {_fmt(r['continuity'], '.0%')} "
                f"| {r['min_distance_mi']:.1f} mi | {'yes' if r['visible'] else 'no'} |"
            )
    else:
        lines.append("None observed.")

    gaps = report["gaps"]
    lines += [
        "",
        "## Observation gaps",
        "",
        f"Intervals between new positions longer than {gaps['threshold_s']:g}s, including exits and re-entries: "
        f"{gaps['count']} across {gaps['aircraft_with_gaps']} aircraft "
        f"(median {_fmt(gaps['median_s'], '.0f', 's')}, max {_fmt(gaps['max_s'], '.0f', 's')}).",
        "",
        "## Possible TIS-B duplicates",
        "",
    ]
    if report["tisb_duplicates"]:
        lines += [
            f"- {d['tisb_id']} ↔ {d['aircraft_id']} ({d['registration'] or 'no registration'}): {d['polls']} poll{'s' if d['polls'] != 1 else ''}"
            for d in report["tisb_duplicates"][:10]
        ]
    else:
        lines.append("None flagged.")

    lines += ["", "## Null field rates", ""]
    lines += [f"- {k}: {_fmt(v, '.1%')}" for k, v in report["null_rates"].items()]
    if report["rejections"]:
        lines += ["", "## Rejected records", ""]
        lines += [f"- {reason}: {n}" for reason, n in report["rejections"].items()]

    st = report["storage"]
    mb = 1024 * 1024
    lines += [
        "",
        "## Storage",
        "",
        f"- SQLite (estimated share): {st['db_bytes_estimate'] / mb:.1f} MB, "
        f"{_fmt(st['db_bytes_per_hour'] and st['db_bytes_per_hour'] / mb, '.1f', ' MB')}/hour",
        f"- Raw responses (gzip): {st['raw_bytes'] / mb:.1f} MB, "
        f"{_fmt(st['raw_bytes_per_hour'] and st['raw_bytes_per_hour'] / mb, '.1f', ' MB')}/hour",
        "",
        "Visible or audible aircraft missing from the feed are an informal check; note them separately.",
    ]
    return "\n".join(lines) + "\n"
