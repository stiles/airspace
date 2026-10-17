"""Reconstruct state at a recorded receipt time with the live state builder."""

from __future__ import annotations

from datetime import datetime, timedelta

from airspace.classify import Classification, classify
from airspace.config import Config
from airspace.state import StateBuilder
from airspace.storage import Store


class RegistryCache:
    """Per-aircraft FAA registry results, looked up once per ID outside the polling loop."""

    def __init__(self, registry=None):
        self.registry = registry
        self.records: dict[str, dict | None] = {}

    def unresolved(self, aircraft_ids) -> list[str]:
        if self.registry is None:
            return []
        return [aid for aid in aircraft_ids if aid not in self.records]

    def resolve(self, aircraft_ids: list[str]) -> None:
        if not aircraft_ids or self.registry is None:
            return
        found = self.registry.lookup(aircraft_ids)
        for aid in aircraft_ids:
            self.records[aid] = found.get(aid)

    def classify(self, aircraft_id: str, category: str | None) -> Classification:
        return classify(category, self.records.get(aircraft_id))


def lookback(config: Config) -> timedelta:
    """History needed to rebuild trails, freshness and crossings at a moment."""
    f = config.freshness
    return timedelta(seconds=max(f.trail_duration_s, f.hide_s) + f.trail_gap_s)


def replay_state(store: Store, session_id: int, at: datetime, config: Config, cache: RegistryCache | None = None) -> dict:
    """Snapshot as the live builder would have produced it at `at`, using only records received by then."""
    cache = cache or RegistryCache()
    builder = StateBuilder(config, classifier=cache.classify, mode="replay")
    polls = store.polls_between(session_id, at - lookback(config), at)
    for poll in polls:
        builder.ingest(poll)
    # The window can start mid-outage; take the true last success from the full record
    builder.feed.last_success_at = store.last_success_before(session_id, at)
    cache.resolve(cache.unresolved(builder.tracks))
    snapshot = builder.snapshot(at)
    start, end = store.session_bounds(session_id)
    snapshot["replay"] = {
        "session_id": session_id,
        "at": at.isoformat(),
        "session_start": start.isoformat() if start else None,
        "session_end": end.isoformat() if end else None,
    }
    return snapshot
