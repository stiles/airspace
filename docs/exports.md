# Exports

Airspace exports recorded observations with their source and timestamp provenance. Exports never include the home coordinates or distances from home, though the positions themselves show the general area.

```bash
uv run airspace export --session 1                                # tracks as GeoJSON
uv run airspace export --session 1 --geometry points              # one point per observation
uv run airspace export --session 1 --format json                  # observations with field definitions
uv run airspace export --session 1 --aircraft a1f0d8 --start 2026-10-17T20:15:00Z --end 2026-10-17T20:45:00Z
uv run airspace export --session 1 --format flighttracer --aircraft a1f0d8 -o n224la/
```

The same exports (except FlightTracer) are available at `GET /api/sessions/{id}/export?format=geojson|json&geometry=tracks|points&aircraft=&start=&end=`. The dashboard links to them from the replay bar and the aircraft detail panel.

`start` and `end` filter on `received_at`, the local receipt time. `aircraft` takes provider aircraft IDs (ICAO hex, case-insensitive).

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
| `coverage_gaps` | Failed polls and host sleep in the range. Missing aircraft during a gap are a coverage problem, not an empty sky |

## JSON (`--format json`)

`{"metadata": ..., "fields": ..., "observations": [...]}`. Each observation has the normalized fields below, plus `classification`, `classification_rule` and the operator fields. `fields` holds these definitions in machine-readable form.

| Field | Meaning |
| --- | --- |
| `aircraft_id` | ICAO address in lowercase hex. A leading `~` marks a non-ICAO address such as a TIS-B track file |
| `received_at` | UTC time the local collector received the response |
| `provider_at` | UTC response time reported by the provider |
| `position_at` | `provider_at` minus the provider's reported position age. Null if unknown |
| `position_time_basis` | `derived_age`, `explicit` or `unknown` |
| `new_position` | False for repeated (cached) or out-of-order positions |
| `latitude`, `longitude` | WGS84 degrees |
| `altitude_baro_ft` | Barometric altitude in feet; not height above ground. Null on the ground |
| `altitude_geom_ft` | GNSS altitude in feet |
| `on_ground` | True, false or null |
| `ground_speed_kt`, `track_deg`, `vertical_rate_fpm` | Movement; track is not necessarily heading |
| `callsign`, `registration`, `aircraft_type`, `aircraft_description`, `category` | Identification; null when unknown |
| `position_source` | `adsb_icao`, `adsr_icao`, `mlat`, `tisb_trackfile` and so on |
| `owner_operator` | As reported, except null when the FAA registry lists individuals as owners (role `private`) |
| `squawk`, `emergency` | As reported |
| `classification`, `classification_rule` | `rotorcraft`, `gyroplane`, `surface`, `aircraft` or `unknown`, and the rule that produced it (`adsb_category`, `faa_registry`, `none`) |
| `operator`, `operator_short`, `role`, `role_rule` | From the operator catalog: e.g. `LAPD Air Support Division`, `LAPD`, `police`, and how it matched (`catalog_hex`, `catalog_registration`, `catalog_callsign`, `owner_pattern`, `registry_owner_type`). Null when unidentified; `operator` is null for private owners |

## GeoJSON tracks (default)

One feature per continuous track segment per aircraft, built from new positions only, ordered by `position_at`. A segment ends at a gap longer than the trail gap threshold (30 seconds by default) or an implied speed above 800 knots. Gaps are never joined into one line. A segment with a single position is a Point.

Properties: `aircraft_id`, `segment`, `registration`, `callsigns`, `aircraft_type`, `aircraft_description`, `classification`, `classification_rule`, `operator`, `operator_short`, `role`, `role_rule`, `start`, `end`, `points`, `coordinate_times` and `altitudes_baro_ft`. The last two are parallel to the coordinates; `coordinate_times` follows the togeojson `coordTimes` convention. There is also `position_sources`.

## GeoJSON points

One Point per observation, including repeated positions (`new_position: false`), with every field above as properties.

## FlightTracer run folder

`--format flighttracer` writes a folder that [FlightTracer](https://github.com/stiles/flight-tracer)'s `render` command reads (checked against v0.4.2):

- `trace.csv` holds one aircraft's airborne new positions, in FlightTracer's column order. `altitude` is barometric. `leg_id` is 1 and `leg_detection` is `unmarked`, because Airspace doesn't detect separate flights; FlightTracer dashes long gaps itself. `is_stale` marks positions older than the fade threshold (20 seconds) when received.
- `airspace.json` holds the provenance metadata.

```bash
flight-tracer render n224la/
```

FlightTracer currently hardcodes "Source: ADS-B Exchange" on its images. For adsb.fi data that credit is wrong, and adsb.fi's terms require citing adsb.fi. Correct the source line before using a rendered image anywhere.

## GeoJSON behaviors

`airspace export --geometry behaviors` (or `geometry=behaviors` on the API) writes one Point per circling, hovering or low-flight episode, at its center. Properties: `aircraft_id`, `registration`, `callsigns`, `classification`, `operator`, `operator_short`, `role`, `behavior`, `start`, `end`, `duration_s`, `turns` (circling), `radius_m` (median distance from the center), `altitude_baro_ft` (median) and `points`. The metadata's `behavior_rules` states the thresholds used. Behaviors are inferred from positions and describe the track, not the reason for it; there are no distances from home.
