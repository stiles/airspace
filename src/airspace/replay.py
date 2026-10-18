"""Reconstruct state at a recorded receipt time with the live state builder."""

from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import datetime, timedelta

from airspace.catalog import Catalog, Operator
from airspace.classify import GYROPLANE, ROTORCRAFT, Classification, classify
from airspace.config import Config
from airspace.enrich import HangarbayRegistry
from airspace.state import StateBuilder
from airspace.storage import Store


# Enough for several days of distinct aircraft near LA (about 600 overnight); older entries
# are looked up again if they come back
MAX_REGISTRY_RECORDS = 5000


class RegistryCache:
    """Per-aircraft FAA registry results, looked up once per ID outside the polling loop,
    plus the operator catalog.

    Least recently used entries are dropped past `max_records` (None keeps all), so a long run stays bounded.
    """

    def __init__(self, registry=None, max_records: int | None = MAX_REGISTRY_RECORDS, catalog: Catalog | None = None):
        self.registry = registry
        self.max_records = max_records
        self.catalog = catalog
        self.records: OrderedDict[str, dict | None] = OrderedDict()
        # Lookups resolve on a worker thread while the event loop classifies
        self._lock = threading.Lock()

    def unresolved(self, aircraft_ids) -> list[str]:
        if self.registry is None:
            return []
        with self._lock:
            return [aid for aid in aircraft_ids if aid not in self.records]

    def resolve(self, aircraft_ids: list[str]) -> None:
        if not aircraft_ids or self.registry is None:
            return
        found = self.registry.lookup(aircraft_ids)
        with self._lock:
            for aid in aircraft_ids:
                self.records[aid] = found.get(aid)
                self.records.move_to_end(aid)
            while self.max_records is not None and len(self.records) > self.max_records:
                self.records.popitem(last=False)

    def classify(self, aircraft_id: str, category: str | None) -> Classification:
        with self._lock:
            record = self.records.get(aircraft_id)
            if aircraft_id in self.records:
                self.records.move_to_end(aircraft_id)
        return classify(category, record)

    @classmethod
    def from_config(cls, config: Config, **kw) -> RegistryCache:
        """Open the FAA registry if available and load the catalogs (raises CatalogError on bad files)."""
        registry = HangarbayRegistry.open_optional(config.enrich.hangarbay_db)
        return cls(registry, catalog=Catalog.load(config.enrich.catalogs), **kw)

    def close(self) -> None:
        if self.registry:
            self.registry.close()

    def operator(
        self, aircraft_id: str, *, kind: str, registration: str | None, callsign: str | None, owner: str | None
    ) -> Operator | None:
        """Catalog match using provider fields and, when looked up, the registry's owner."""
        if self.catalog is None:
            return None
        with self._lock:
            record = self.records.get(aircraft_id)
        return self.catalog.match(
            aircraft_id=aircraft_id,
            registration=registration or (f"N{record['n_number']}" if record and record.get("n_number") else None),
            callsign=callsign,
            owners=(owner, record.get("owner_name") if record else None),
            rotorcraft=kind in (ROTORCRAFT, GYROPLANE),
            owner_type=str(record["owner_type"]) if record and record.get("owner_type") else None,
        )


def lookback(config: Config) -> timedelta:
    """History needed to rebuild trails, freshness and crossings at a moment."""
    f = config.freshness
    return timedelta(seconds=max(f.trail_duration_s, f.hide_s, config.behavior.history_s) + f.trail_gap_s)


def replay_state(store: Store, session_id: int, at: datetime, config: Config, cache: RegistryCache | None = None) -> dict:
    """Snapshot as the live builder would have produced it at `at`, using only records received by then."""
    cache = cache or RegistryCache()
    builder = StateBuilder(config, identity=cache, mode="replay")
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
