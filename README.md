# Airspace

A local tracker for aircraft flying near a home point, with attention to helicopters and low-altitude traffic. See [SPEC.md](SPEC.md) for the plan.

![Airspace dashboard: a dark map of West Los Angeles with mile rings, aircraft symbols and trails, an LAPD helicopter in amber, and a list of nearby aircraft](img/airspace_example.png)

Milestones 1 through 4 are built: a headless collector, a coverage report, a local state service and API, a Mapbox dashboard, and replay with exports. Milestone 5, an eight-hour soak test and a decision on continuous operation, comes next.

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

## Dashboard

The dashboard is Svelte 5, TypeScript, Vite and Mapbox GL JS, in `web/`. It needs a public Mapbox token (`pk.`), ideally restricted to `http://127.0.0.1:*`. Put it in `web/.env.local`, which Git ignores:

```bash
VITE_MAPBOX_TOKEN=pk....
# Optional. The default style belongs to the stiles Mapbox account, so other accounts should set this:
VITE_MAPBOX_STYLE=mapbox://styles/mapbox/dark-v11
```

Vite builds the token into the bundle, and `web/dist/` is gitignored too. Then:

```bash
cd web && npm install && npm run build && cd ..
uv run airspace serve        # dashboard at http://127.0.0.1:8000
```

For frontend work, run `npm run dev` in `web/` while `airspace serve` is running. The dev server at http://127.0.0.1:5173 forwards `/api` to port 8000.

**Replay:** use the Live/Replay switch in the header, or run `airspace replay` for recorded data only. The timeline bar shows every poll: green when it succeeded, red when it failed, hatched while the collector was asleep, with an amber strip when an ADS-B category A7 helicopter was in the feed. Drag or click to seek. The controls are play/pause (space), previous/next poll (← →), 1×–60× speed and a session picker. The URL keeps `?session=&at=`, so a moment can be bookmarked. Replay reads only records received by the playhead time.

**Exports:** tracks, points or observations for a session from the replay bar, or one aircraft's tracks from the detail panel. See [docs/exports.md](docs/exports.md), which also covers the FlightTracer run-folder export.

What the dashboard does:

- **Map:** north-up dark map (Mapbox Standard or classic style; overlays set emissive strength so night lighting doesn't dim them) with mile rings, the visible boundary and the collection limit. Symbols are rotated by ground track. Helicopters get an amber rotor symbol only when the classifier says rotorcraft, and aircraft without a track get an unrotated symbol. Labels show altitude in hundreds of feet (`015` means 1,500 ft barometric).
- **Updates:** the browser reads `/api/state` every two seconds. That never triggers provider requests. Map layers are created once; each update only replaces source data.
- **Filters:** altitude presets, unknown altitude (shown by default), on-ground aircraft and surface vehicles (hidden by default, to keep LAX traffic out of the way), and list-only-within-visible-radius. Filters run in the browser.
- **Freshness:** positions keep aging on the browser's clock between updates. If the local server stops responding, markers fade and then disappear instead of freezing.
- **Feed status:** any outage, upstream or local, turns the header red and grays out the map. It never reads as an empty sky. Replay shows the recorded feed status, so a recorded outage looks like one.

```bash
cd web && npm test && npm run check
```

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
