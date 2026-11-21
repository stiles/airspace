# Configuration and detection

`uv run airspace init` writes a starter `airspace.toml`. Edit it in place as settings change. Commands use `--config` first, then `AIRSPACE_CONFIG`, then `airspace.toml` in the working directory. Run from the repository root if you use the default relative paths for data and catalogs.

Restart `airspace serve`, `collect` or `replay` after changing TOML settings. Rebuild the dashboard after changing `web/.env.local` or updating frontend source.

## Home, range and display

| Setting | Default | Controls |
| --- | --- | --- |
| `home.latitude`, `home.longitude` | Set by `init` | Home point in decimal degrees |
| `home.label`, `home.title` | `Home`, `Airspace` | Home label and the page header and browser title |
| `area.collection_radius_nm` | 10 | Radius the provider searches, in nautical miles |
| `area.visible_radius_mi` | 5 | Home ring and default list radius, in statute miles. It must fit inside the collection radius |
| `display.view_radius_mi` | Unset | Camera radius in statute miles. When omitted, the map frames the home radius plus 8% |
| `display.show_la_boundary`, `display.outlines` | `false`, `[]` | Turn on the bundled LA boundary or add GeoJSON line or polygon files in longitude, latitude order |
| `display.text_scale` | 1 | Header, sidebar, legend and weather text scale from 0.75 to 3. Try 1.5 on a monitor across the room |
| `display.tv_display` | `false` | Show the live map, aircraft list, weather and legend on an unattended monitor. Hides playback, layout, filter and map controls |
| `display.show_filters` | `true` | Set to `false` to hide the controls while keeping the configured starting filters |

For a 10-mile list and matching home framing, use these values in your existing sections.

```toml
[area]
visible_radius_mi = 10
collection_radius_nm = 10

[display]
view_radius_mi = 10
```

The collector still searches 10 nautical miles, about 11.5 statute miles. Zoom and pan affect the camera only. Traffic outside the home ring remains on the map when it meets the display filters.

Set `VITE_MAPBOX_STYLE=mapbox://styles/…` in `web/.env.local` to use a different style. Airspace recolors classic styles into its radar palette. Mapbox Standard styles keep their own colors.

## Filters, polling and storage

| Section | Settings and defaults |
| --- | --- |
| `[filters]` | `max_altitude_baro_ft` unset, `unknown_altitude = true`, `on_ground = false`, `surface_vehicles = false`, `list_visible_only = true`. These filter the display. Set `list_visible_only = false` to include the full collection area in the list |
| `[collector]` | `provider = "adsbfi"`, `poll_interval_s = 10`, `timeout_s = 8`, `max_backoff_s = 300`, `schema_error_limit = 3`, `store_raw = false`. Enable `store_raw` to keep gzipped responses for diagnostics |
| `[freshness]` | `fade_s = 20`, `hide_s = 60`, `trail_gap_s = 30`, `trail_duration_s = 300` |
| `[storage]` | `data_dir = "data"`, `retention_days = 7`. Set retention to `0` to keep recordings indefinitely |
| `[weather]` | `enabled = true`, `station` unset, `interval_s = 600`. Airspace uses the nearest station the NWS lists for home, or the station you set, for example `KSMO` |
| `[enrich]` | `catalogs = ["catalogs"]`. The default `hangarbay_db` is `~/.hangarbay/data/publish/registry.duckdb` |

Airspace switches to `collector.fast_interval_s` (2 seconds by default) when a fresh, airborne rotorcraft, gyroplane or cataloged police, fire, medical or news aircraft is inside the trigger radius. The default trigger radius is the home radius. Set `fast_radius_mi` to a different distance up to the collection limit, or set `fast_interval_s = 0` to turn it off. After the last trigger, fast polling holds for `fast_hold_s` (60 seconds). Failed requests exit fast mode and follow the normal retry schedule. The report includes requests per hour and the share of fast polls.

The collector prunes expired polls at startup and hourly. It removes sessions that pruning empties and their raw response files. SQLite reuses deleted space. To shrink the default database, stop the collector and run `sqlite3 data/airspace.sqlite VACUUM`. Use the coverage report to estimate storage growth for your own traffic and cadence.

## Aircraft identification

ADS-B emitter category `A7` identifies rotorcraft. Airspace also looks up US registrations by ICAO address when both DuckDB and a local [hangarbay registry](https://github.com/stiles/hangarbay) are available. Install the optional dependency with `uv sync --extra enrich`. FAA aircraft type codes `6` and `9` identify rotorcraft and gyroplanes. ADS-B categories `C1` through `C5` describe surface vehicles or obstacles.

Airspace identifies operators by ICAO hex first, then registration, then a callsign prefix for helicopters, then an owner-name pattern. Aircraft whose FAA registry records identify individual owners appear as private, and their names are withheld from the dashboard and exports.

Each aircraft row needs a registration or a six-digit ICAO hex, a role, and a source. Valid roles are `police`, `fire`, `medical`, `news`, `military`, `tour`, `utility`, `training` and `private`. Use `checked` for the confirmation date. Leave it empty for an inferred identification. `operator` is required except for `private`. `operator_short` supplies a compact display name, for example `LAPD`. `callsign_prefix` accepts two to four letters. [Los Angeles catalog](../catalogs/los-angeles.csv)

Operator-pattern files use a case-insensitive regular expression in `pattern`, a role and a source. A blank `operator` uses the matched owner name. Run `uv run airspace catalog` after editing either kind of file. [Operator patterns](../catalogs/operators.csv)

## Inferred behavior

These rules describe aircraft movement. All thresholds are configurable in `[behavior]`.

| Behavior | Default rule |
| --- | --- |
| Circling | A full net turn within 300 s, within 1,500 m of the window's center and at a ground speed of at least 20 kt. Nearby orbits within 120 s can merge into one episode |
| Hovering | An airborne helicopter at 15 kt or less for 60 s with no more than 200 m of drift |
| Low | An airborne helicopter at or below 300 ft barometric for 20 s. Barometric altitude measures above sea level |

The dashboard and replay look back 15 minutes by default (`behavior.history_s`). A duration such as “15+ min” indicates that the episode reaches the start of the detection window. The report and [behavior export](exports.md#geojson-behaviors) can detect episodes over the full session.
