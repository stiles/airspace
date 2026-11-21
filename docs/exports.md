# Exports

Airspace exports recorded observations with their source and timestamps. The export omits the configured home point and distances from home. Aircraft positions still reveal where they flew.

## Download an export

In the expanded dashboard, open **Export** in the replay bar for the selected session's tracks, points or JSON observations. Select a current aircraft on the map or in the list to download its tracks from the detail panel. Both paths use the available records for that session regardless of the replay playhead, display filters or map view.

Use `airspace sessions` to find a session ID, or `--session latest` to use the newest session, including one still recording. The CLI defaults to `latest` and writes GeoJSON tracks unless you choose a different format.

```bash
uv run airspace sessions
uv run airspace export --session latest -o tracks.geojson
uv run airspace export --session 1 --format json \
  --aircraft a1f0d8 --aircraft a1f48f \
  --start 2026-10-17T20:15:00Z --end 2026-10-17T20:45:00Z -o observations.json
uv run airspace export --session latest --geometry behaviors -o behaviors.geojson
```

`--aircraft` accepts case-insensitive provider aircraft IDs, usually ICAO hex addresses. Repeat it to include multiple aircraft. `--start` and `--end` are inclusive bounds on `received_at`. Include a timezone in each timestamp, such as `Z` for UTC. The CLI writes to a file named for the session and format by default. An existing output file is replaced.

For scripted downloads, `GET /api/sessions/{id}/export` takes a numeric session ID. It accepts `format=geojson` or `format=json`, `geometry=tracks`, `geometry=points` or `geometry=behaviors` for GeoJSON, and optional `start`, `end` and comma-separated `aircraft` query parameters. Use the CLI for a [FlightTracer run folder](#flighttracer-run-folder).

```bash
curl --fail --get 'http://127.0.0.1:8000/api/sessions/1/export' \
  --data-urlencode 'geometry=points' \
  --data-urlencode 'aircraft=a1f0d8,a1f48f' \
  -o points.geojson
```

## Metadata

Every export carries a `metadata` object. In GeoJSON it's a foreign member, which GeoJSON readers ignore.

| Key | Contents |
| --- | --- |
| `generator`, `export_schema_version`, `generated_at` | Airspace version, export schema version (currently 1), creation time |
| `source` | Provider, name, URL and terms. adsb.fi data is for personal, noncommercial use and must cite adsb.fi |
| `session` | Session ID, mode, start and end, end reason, first and last poll |
| `collection` | Poll interval, collection radius and visible radius. `home` is always `"omitted from exports"` |
| `range`, `filters` | The requested time range and aircraft |
| `timestamps` | How `received_at` and `position_at` are defined |
| `coverage_gaps` | Failed polls and host sleep logged in the requested range |

Aircraft classifications and operator matches use the catalog and FAA registry available at export time. Check `coverage_gaps` before treating an interval without positions as evidence about traffic.

## JSON (`--format json`)

The top-level keys are `metadata`, `fields` and `observations`. The `fields` object provides field definitions for the observations.

| Field | Meaning |
| --- | --- |
| `aircraft_id` | ICAO address in lowercase hex. A leading `~` marks a non-ICAO address such as a TIS-B track file |
| `received_at` | UTC time the local collector received the response |
| `provider_at` | UTC response time reported by the provider |
| `position_at` | Position time. For adsb.fi, `provider_at` minus the reported position age. Null when unknown |
| `position_time_basis` | `derived_age`, `explicit` or `unknown` |
| `time_flag` | `future_position` if the provider reported a position too far in the future. Otherwise null |
| `new_position` | False for repeated (cached) or out-of-order positions |
| `latitude`, `longitude` | WGS84 degrees |
| `altitude_baro_ft` | Barometric altitude in feet above sea level. Null on the ground or when missing |
| `altitude_geom_ft` | GNSS altitude in feet |
| `on_ground` | True, false or null |
| `ground_speed_kt`, `track_deg`, `vertical_rate_fpm` | Ground speed in knots, true ground track in degrees and vertical rate in feet per minute |
| `callsign`, `registration`, `aircraft_type`, `aircraft_description`, `category` | Identification. Null when unknown |
| `position_source` | `adsb_icao`, `adsr_icao`, `mlat`, `tisb_trackfile` and so on |
| `owner_operator` | As reported, except null when the FAA registry lists individuals as owners (role `private`) |
| `squawk`, `emergency` | As reported |
| `classification`, `classification_rule` | `rotorcraft`, `gyroplane`, `surface`, `aircraft` or `unknown`, and the rule that produced it (`adsb_category`, `faa_registry`, `none`) |
| `operator`, `operator_short`, `role`, `role_rule` | Operator name, short label, role and identification rule. Rules include `catalog_hex`, `catalog_registration`, `catalog_callsign`, `owner_pattern` and `registry_owner_type`. `operator` is null for private owners |

## GeoJSON tracks (default)

One feature per continuous track segment per aircraft. Tracks use new positions with a known `position_at`, in timestamp order. A new segment begins after a gap longer than `freshness.trail_gap_s` (30 seconds by default) or an implied speed above 800 knots. A segment with one position is a Point.

Each feature has a one-based `segment` number, aircraft and operator fields, `callsigns` and `position_sources`. `start` and `end` give the first and last position times, and `points` gives the number of positions. The `coordinate_times` and `altitudes_baro_ft` arrays follow the geometry's coordinate order.

## GeoJSON points

One Point per observation, including repeated positions (`new_position: false`). Latitude and longitude go in the `[longitude, latitude]` geometry. The other observation fields go in `properties`.

## FlightTracer run folder

`--format flighttracer` takes exactly one `--aircraft` and writes two files to a folder for [FlightTracer](https://github.com/stiles/flight-tracer).

- `trace.csv` holds new positions with timestamps, excluding points flagged as on the ground. `altitude` is barometric. All rows use `leg_id = 1` and `leg_detection = unmarked`. `is_stale` marks positions older than `freshness.fade_s` when received.
- `airspace.json` holds the provenance metadata.

```bash
uv run airspace export --session 1 --format flighttracer --aircraft a1f0d8 -o n224la/
flight-tracer render n224la/
```

The compatibility check used FlightTracer v0.4.2, which credits ADS-B Exchange on its images. Check the rendered credit against the source in `airspace.json` and credit adsb.fi when rendering its data.

## GeoJSON behaviors

`--geometry behaviors` (or `geometry=behaviors` on the API) writes one Point at the center of each inferred circling, hovering or low-flight episode. Features include aircraft and operator fields, `behavior`, `start`, `end`, `duration_s`, `turns` for circling, `radius_m` (median distance from the center), median `altitude_baro_ft` and `points`.

Airspace detects episodes from the observations in the export window. A time filter can shorten or exclude an episode near the edge of that window. The `behavior_rules` metadata describes the inference thresholds. A behavior describes the shape or altitude of the observed track.
