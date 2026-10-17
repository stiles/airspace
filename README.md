# Airspace

A local tracker for aircraft flying near a home point, with attention to helicopters and low-altitude traffic. See [SPEC.md](SPEC.md) for the plan.

Milestone 1 (in progress) is a headless collector and a coverage report. There is no map yet.

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

`tests/fixtures/adsbfi_v3_la.json` is a real adsb.fi v3 response captured October 17, 2026, at the demo center. The `dst` and `dir` fields were removed and owner names redacted.

## Data and terms

Aircraft data comes from [adsb.fi](https://adsb.fi), whose open data is for personal, noncommercial use and requires attribution. Check the terms before any newsroom or commercial use.
