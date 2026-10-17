"""Aircraft classification from documented fields only; never from speed or flight pattern."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

ROTORCRAFT = "rotorcraft"
GYROPLANE = "gyroplane"
SURFACE = "surface"  # ADS-B categories C1-C5: ground vehicles and obstacles
AIRCRAFT = "aircraft"  # a non-rotorcraft emitter category; type otherwise unspecified
UNKNOWN = "unknown"

SURFACE_CATEGORIES = {"C1", "C2", "C3", "C4", "C5"}

# FAA registry TYPE AIRCRAFT codes
FAA_TYPES = {"6": ROTORCRAFT, "9": GYROPLANE}


@dataclass(frozen=True)
class Classification:
    kind: str
    rule: str  # which documented source produced it


class Registry(Protocol):
    def lookup(self, aircraft_ids: list[str]) -> dict[str, dict]: ...


def classify(category: str | None, registry_record: dict | None = None) -> Classification:
    """Emitter category A7 first, then the FAA registry, then unknown."""
    if category == "A7":
        return Classification(ROTORCRAFT, "adsb_category")
    if category in SURFACE_CATEGORIES:
        return Classification(SURFACE, "adsb_category")
    if registry_record and (kind := FAA_TYPES.get(str(registry_record.get("faa_type")))):
        return Classification(kind, "faa_registry")
    if category and category[0] in "AB" and category not in ("A0", "B0"):
        return Classification(AIRCRAFT, "adsb_category")
    return Classification(UNKNOWN, "none")


def classify_many(categories: dict[str, str | None], registry: Registry | None = None) -> dict[str, Classification]:
    """Classify aircraft by ID, consulting the registry only for those not settled by category."""
    needs_lookup = [aid for aid, cat in categories.items() if cat != "A7" and cat not in SURFACE_CATEGORIES]
    records = registry.lookup(needs_lookup) if registry and needs_lookup else {}
    return {aid: classify(cat, records.get(aid)) for aid, cat in categories.items()}
