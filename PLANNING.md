# Airspace roadmap

Airspace tracks aircraft near home, with an emphasis on helicopters and low-altitude traffic. This page tracks upcoming work and design constraints. The [README](README.md) covers setup and usage. The [planning archive](PLANNING-ARCHIVE.md) has the original spec, benchmarks, and implementation decisions.

## Built

Milestones 1–7 cover local collection, the dashboard, replay and exports, continuous operation, operator classification, and behavior detection.

- One collector records normalized observations and poll outcomes in SQLite. FastAPI serves live state, replay, and the Svelte dashboard on loopback. The collector can switch to faster polling for nearby helicopters and selected operator roles. [Collector](src/airspace/collector.py) · [API](src/airspace/api.py)
- Live and replay use the same state builder. The dashboard shows feed freshness, aircraft trails, and current circling or hovering under **Activity**. Select an activity to frame it on the map. [State builder](src/airspace/state.py) · [Activity](web/src/components/Activity.svelte)
- Exports carry source and timing provenance. Raspberry Pi setup and a kiosk installer support continuous collection and a full-screen dashboard. [Exports](docs/exports.md) · [Pi setup](docs/pi-setup.md) · [Kiosk guide](docs/raspberry-pi-kiosk.md)

## Roadmap

### 8. Save behavior events and add optional alerts

Milestone 8 records distinct behavior episodes with their sessions and shows them in a dashboard feed and on the replay timeline. The current **Activity** view shows what is happening at the selected moment. The event log adds a history of those episodes.

Add optional alerts with role and distance rules, a quiet period, and deduplication for long episodes. Start with macOS notifications and consider webhooks later.

### Event mode for stories

| Milestone | Work |
| --- | --- |
| 9. Event recordings | Record a named circle or polygon in a separate session and data directory, with notes and role filters. Keep the home tracker running independently. |
| 10. Licensed provider | Evaluate Flightradar24 as a second adapter. Confirm permissions for storage, publishing and attribution, and measure polling credit costs. Assess historical tracks for backfill. Record the provider in each session's provenance. |
| 11. Story exports | Export tracks, inferred events, repeated passes and trips between configured locations. Produce GeoJSON, CSV and rollups by aircraft or role. Validate any claim about a drop with an independent source. |

### Installation and catalogs for other cities

| Milestone | Work |
| --- | --- |
| 12. Packaging | Support `uv tool install`, guide users through choosing a home area and catalog, and ship built dashboard assets. Add provider configuration when another adapter is supported. |
| 13. Community catalogs | Document contributions, require sources and organization names, and validate catalogs in CI. Cover continuous operation on macOS and Linux. |

### Open decisions

- Choose a terrain source before offering height above ground. The current low-flight rule uses barometric altitude above sea level.
- Decide whether an events digest adds enough beyond live alerts and replay.
- Assess which catalog information belongs in hangarbay and whether a local receiver improves coverage enough to justify the installation.

## Design constraints

- Keep provider requests independent of browser polling and filters. Record failed polls and sleep gaps explicitly. An outage needs a visible status even when there are no markers to draw.
- Preserve observation times and source metadata. Replay only data available at the requested time and split trails across coverage gaps.
- Label inferred behaviors as descriptions of the track. Aircraft type, operator and observed movement cannot establish why it was there.
- Confirm provider permissions before using data for stories. Carry attribution through the dashboard, reports and exports.
