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
    store_raw: bool = True

    @model_validator(mode="after")
    def timeout_below_interval(self) -> Collector:
        if self.timeout_s >= self.poll_interval_s:
            raise ValueError("timeout_s must be shorter than poll_interval_s")
        return self


class Freshness(BaseModel):
    fade_s: float = 20.0
    hide_s: float = 60.0
    trail_gap_s: float = 30.0
    trail_duration_s: float = 300.0


class Storage(BaseModel):
    data_dir: Path = Path("data")
    retention_days: int = 7


class Enrich(BaseModel):
    hangarbay_db: Path | None = Path("~/.hangarbay/data/publish/registry.duckdb")


class Config(BaseModel):
    home: Home
    area: Area = Area()
    collector: Collector = Collector()
    freshness: Freshness = Freshness()
    storage: Storage = Storage()
    enrich: Enrich = Enrich()

    @model_validator(mode="after")
    def collection_within_provider_limit(self) -> Config:
        from airspace.providers import get_provider

        provider = get_provider(self.collector.provider)
        if self.area.collection_radius_nm > provider.max_radius_nm:
            raise ValueError(f"{provider.name} allows at most {provider.max_radius_nm} NM")
        return self

    @property
    def visible_radius_m(self) -> float:
        return self.area.visible_radius_mi * METERS_PER_MILE

    @property
    def collection_radius_m(self) -> float:
        return self.area.collection_radius_nm * METERS_PER_NM


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
# Keep gzipped raw responses (diagnostic trials only)
store_raw = true

[freshness]
fade_s = 20.0
hide_s = 60.0
trail_gap_s = 30.0
trail_duration_s = 300.0

[storage]
data_dir = "data"
retention_days = 7

[enrich]
# hangarbay's FAA registry; delete this line to disable lookups
hangarbay_db = "~/.hangarbay/data/publish/registry.duckdb"
"""


def render(latitude: float, longitude: float, label: str) -> str:
    text = TEMPLATE.format(latitude=latitude, longitude=longitude, label=label.replace('"', ""))
    Config.model_validate(tomllib.loads(text))
    return text
