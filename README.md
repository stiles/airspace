# Airspace

A local tracker for aircraft flying near a home point, with attention to helicopters and low-altitude traffic. See [SPEC.md](SPEC.md) for the plan.

Milestones 1 and 2 are built: a headless collector, a coverage report, and a local state service and API. The Mapbox display (milestone 3) isn't built yet.

## Setup

```bash
uv sync --extra enrich      # drop --extra enrich to skip FAA registry lookups
uv run airspace init        # prompts for home coordinates; writes airspace.toml (gitignored)
uv run airspace init --demo # or use an approximate West LA demo center
```

## Collect and report

```bash
uv run airspace collect --duration 2h
uv run airspace report --session latest
uv run airspace sessions
```

`collect` polls adsb.fi every 10 seconds, one request at a time, and writes to `data/airspace.sqlite`. With `store_raw = true` it also keeps gzipped raw responses in `data/raw/`. A file lock stops a second collector from writing to the same data directory. Ctrl-C ends the session cleanly.

Collection pauses instead of retrying on non-429 client errors, because adsb.fi counts those toward its rate limit, and after three consecutive schema errors. A 429 waits at least as long as `Retry-After`. Other failures back off exponentially with jitter. When the laptop sleeps, the collector records a gap and resumes without making up the missed polls.

`report` prints the milestone 1 gate (request success, p95 position age, helicopter continuity), plus latency, cached and repeated positions, null-field rates, gaps, possible TIS-B duplicates and storage per hour. Surface vehicles (ADS-B categories C1 through C5) are excluded from the metrics.

## Local API

```bash
uv run airspace serve                     # collector + API on http://127.0.0.1:8000
uv run airspace replay --session latest   # recorded data only, no provider requests
```

`serve` runs one collector inside the API process and takes the same lock as `collect`, so it refuses to start while a `collect` run is active. It binds to `127.0.0.1` with one worker and no auto-reload. Interactive docs are at `/api/docs`.

| Endpoint | Returns |
| --- | --- |
| `GET /api/state` | Versioned snapshot: aircraft sorted by distance with freshness and trail segments, recently lost aircraft, entry/exit events, area, thresholds and feed status |
| `GET /api/aircraft/{id}` | One aircraft with owner/operator and recent positions |
| `GET /api/health` | Collector status, feed status, position-age summary |
| `GET /api/sessions` | Recorded sessions |
| `GET /api/replay?session_id=&at=` | Snapshot rebuilt from records received by `at` (ISO 8601) |

Reading the API never triggers a provider request. The live state and `/api/replay` use the same state builder, and replay reads through a read-only SQLite connection. Coordinates are always `[longitude, latitude]`.

Feed status is one of `starting`, `live`, `delayed` (no success for longer than the fade threshold), `degraded` (latest poll failed), `stale` (no success for longer than the hide threshold) or `paused` (collection stopped on an error that needs attention). An outage shows up as `stale`, never as an empty sky.

## Helicopter classification

The classifier tries these in order:

1. ADS-B emitter category `A7`.
2. FAA registry aircraft type, looked up by ICAO hex in [hangarbay](https://github.com/stiles/hangarbay)'s local DuckDB at `~/.hangarbay/data/publish/registry.duckdb`. Code `6` is rotorcraft and `9` is gyroplane. This covers US-registered aircraft only, and the data is as current as your last `hangar update`.
3. Unknown.

The report records which rule produced each classification.

## Tests

```bash
uv run pytest
```

The tests cover adapter edge cases, geometry, collector failure handling with a fake clock, state, trails and crossings, replay matching the live snapshot at the same moment, and the API against a mock provider.

`tests/fixtures/adsbfi_v3_la.json` is a real adsb.fi v3 response captured October 17, 2026, at the demo center. The `dst` and `dir` fields were removed and owner names redacted.

## Data and terms

Aircraft data comes from [adsb.fi](https://adsb.fi), whose open data is for personal, noncommercial use and requires attribution. Check the terms before any newsroom or commercial use.
