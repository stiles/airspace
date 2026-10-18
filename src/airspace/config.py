"""Local configuration. The file holds the home point and stays out of Git."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from airspace.geometry import METERS_PER_MILE, METERS_PER_NM

DEFAULT_PATH = Path("airspace.toml")

# Approximate West LA point for fixtures and screenshots; not anyone's address
DEMO_LATITUDE = 34.00
DEMO_LONGITUDE = -118.42


class Home(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: str = "Home"


class Area(BaseModel):
    visible_radius_mi: float = Field(default=5.0, gt=0)
    collection_radius_nm: float = Field(default=10.0, gt=0)
    # Optional preset, e.g. 10000; None shows every altitude
    max_altitude_baro_ft: int | None = None

    @model_validator(mode="after")
    def visible_fits_collection(self) -> Area:
        if self.visible_radius_mi * METERS_PER_MILE > self.collection_radius_nm * METERS_PER_NM:
            raise ValueError(
                f"visible radius ({self.visible_radius_mi} mi) exceeds collection radius "
                f"({self.collection_radius_nm} NM); expand the collection radius"
            )
        return self


class Collector(BaseModel):
    provider: str = "adsbfi"
    poll_interval_s: float = Field(default=10.0, ge=1.0)
    timeout_s: float = Field(default=8.0, gt=0)
    max_backoff_s: float = Field(default=300.0, gt=0)
    # Consecutive schema errors before collection pauses
    schema_error_limit: int = Field(default=3, ge=1)
    # Gzipped raw responses, for diagnosing the provider; about 1 MB/hour
    store_raw: bool = False
    # Poll this often while a helicopter or public-safety/news aircraft is airborne in the
    # visible area, and for fast_hold_s after; 0 turns fast polling off. adsb.fi allows 1 request/s.
    fast_interval_s: float = Field(default=2.0, ge=0)
    fast_hold_s: float = Field(default=60.0, ge=0)
    # How far out an aircraft triggers fast polling; unset means the visible area. Up to the
    # collection radius (about 11.5 mi at 10 NM): wider costs more requests.
    fast_radius_mi: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def timeout_below_interval(self) -> Collector:
        if self.timeout_s >= self.poll_interval_s:
            raise ValueError("timeout_s must be shorter than poll_interval_s")
        if self.fast_interval_s and not 1.0 <= self.fast_interval_s <= self.poll_interval_s:
            raise ValueError("fast_interval_s must be 0 (off) or between 1 and poll_interval_s")
        return self


class Freshness(BaseModel):
    fade_s: float = 20.0
    hide_s: float = 60.0
    trail_gap_s: float = 30.0
    trail_duration_s: float = 300.0


class Behavior(BaseModel):
    """Thresholds for inferred behaviors (see behaviors.py). Defaults tuned on LA recordings."""

    # Circling: a full net turn within the window, staying within the radius, faster than hovering
    circle_window_s: float = Field(default=300.0, gt=0)
    circle_max_radius_m: float = Field(default=1500.0, gt=0)
    circle_min_speed_kt: float = Field(default=20.0, ge=0)
    # Orbits over the same spot separated by up to this long are one episode
    circle_merge_gap_s: float = Field(default=120.0, ge=0)
    # Hovering (helicopters): at or below this ground speed for this long, within this drift
    hover_max_speed_kt: float = Field(default=15.0, ge=0)
    hover_min_s: float = Field(default=60.0, gt=0)
    hover_max_drift_m: float = Field(default=200.0, gt=0)
    # Low flight (helicopters): barometric altitude at or below this, for this long. LAPD orbits
    # mostly at 400-800 ft; barometric altitude is above sea level, so hills need terrain (later)
    low_altitude_ft: float = 300.0
    low_min_s: float = Field(default=20.0, gt=0)
    # An episode is current if it ends within this of the aircraft's latest position
    ongoing_s: float = Field(default=30.0, ge=0)
    # Position history kept per aircraft for live detection; longer episodes report this much
    history_s: float = Field(default=900.0, gt=0)


class Storage(BaseModel):
    data_dir: Path = Path("data")
    # Recorded polls and observations older than this are pruned hourly; 0 keeps everything
    retention_days: int = Field(default=7, ge=0)


class Enrich(BaseModel):
    hangarbay_db: Path | None = Path("~/.hangarbay/data/publish/registry.duckdb")
    # Operator and role catalogs: CSV files or directories of them (see catalogs/)
    catalogs: list[Path] = [Path("catalogs")]


class Config(BaseModel):
    home: Home
    area: Area = Area()
    collector: Collector = Collector()
    freshness: Freshness = Freshness()
    behavior: Behavior = Behavior()
    storage: Storage = Storage()
    enrich: Enrich = Enrich()

    @model_validator(mode="after")
    def collection_within_provider_limit(self) -> Config:
        from airspace.providers import get_provider

        provider = get_provider(self.collector.provider)
        if self.area.collection_radius_nm > provider.max_radius_nm:
            raise ValueError(f"{provider.name} allows at most {provider.max_radius_nm} NM")
        if self.fast_radius_m > self.collection_radius_m + 1:
            raise ValueError(
                f"fast_radius_mi ({self.collector.fast_radius_mi}) exceeds the collection radius "
                f"({self.collection_radius_m / METERS_PER_MILE:.1f} mi)"
            )
        return self

    @property
    def visible_radius_m(self) -> float:
        return self.area.visible_radius_mi * METERS_PER_MILE

    @property
    def collection_radius_m(self) -> float:
        return self.area.collection_radius_nm * METERS_PER_NM

    @property
    def fast_radius_m(self) -> float:
        if self.collector.fast_radius_mi is None:
            return self.visible_radius_m
        return self.collector.fast_radius_mi * METERS_PER_MILE


def config_path(path: Path | None = None) -> Path:
    return path or Path(os.environ.get("AIRSPACE_CONFIG", DEFAULT_PATH))


def load(path: Path | None = None) -> Config:
    path = config_path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run `airspace init` first")
    with path.open("rb") as f:
        return Config.model_validate(tomllib.load(f))


TEMPLATE = """\
# Airspace local configuration. Keep this file out of Git.

[home]
latitude = {latitude}
longitude = {longitude}
label = "{label}"

[area]
visible_radius_mi = 5.0
collection_radius_nm = 10.0
# max_altitude_baro_ft = 10000

[collector]
provider = "adsbfi"
poll_interval_s = 10.0
timeout_s = 8.0
max_backoff_s = 300.0
schema_error_limit = 3
# Keep gzipped raw responses, about 1 MB/hour (for diagnosing the provider)
store_raw = false
# Faster polling while a helicopter or public-safety/news aircraft is in the visible area; 0 is off
fast_interval_s = 2.0
fast_hold_s = 60.0
# Trigger fast polling farther out, up to the collection radius (about 11.5 mi); more requests
# fast_radius_mi = 11.5

[freshness]
fade_s = 20.0
hide_s = 60.0
trail_gap_s = 30.0
trail_duration_s = 300.0

[storage]
data_dir = "data"
# Polls and observations older than this are pruned hourly; 0 keeps everything
retention_days = 7

[enrich]
# hangarbay's FAA registry; delete this line to disable lookups
hangarbay_db = "~/.hangarbay/data/publish/registry.duckdb"
# Operator and role catalogs: CSV files or directories of them
catalogs = ["catalogs"]
"""


def render(latitude: float, longitude: float, label: str) -> str:
    text = TEMPLATE.format(latitude=latitude, longitude=longitude, label=label.replace('"', ""))
    Config.model_validate(tomllib.loads(text))
    return text
