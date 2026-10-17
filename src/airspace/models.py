"""Normalized records shared by providers, collection, storage and reporting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_iso(dt: datetime | None) -> str | None:
    """Stored timestamps are UTC ISO 8601 with millisecond precision."""
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat(timespec="milliseconds")


def from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class PositionTimeBasis(StrEnum):
    EXPLICIT = "explicit"
    DERIVED_AGE = "derived_age"
    UNKNOWN = "unknown"


class Observation(BaseModel):
    """One aircraft record from one poll, in explicit units."""

    model_config = ConfigDict(frozen=True)

    provider: str
    aircraft_id: str
    received_at: datetime
    provider_at: datetime | None
    position_at: datetime | None
    position_time_basis: PositionTimeBasis
    # Set when a timestamp is in the future or otherwise inconsistent
    time_flag: str | None = None

    latitude: float
    longitude: float
    altitude_baro_ft: int | None = None
    altitude_geom_ft: int | None = None
    on_ground: bool | None = None

    ground_speed_kt: float | None = None
    track_deg: float | None = None
    vertical_rate_fpm: int | None = None

    callsign: str | None = None
    registration: str | None = None
    aircraft_type: str | None = None
    aircraft_description: str | None = None
    category: str | None = None
    position_source: str | None = None
    owner_operator: str | None = None
    squawk: str | None = None
    emergency: str | None = None

    @property
    def position_age_s(self) -> float | None:
        """Position age relative to the provider's response time."""
        if self.position_at is None or self.provider_at is None:
            return None
        return (self.provider_at - self.position_at).total_seconds()


@dataclass(frozen=True)
class Rejection:
    reason: str
    raw: object


class ErrorKind(StrEnum):
    TRANSIENT = "transient"  # timeouts, connection errors, 5xx
    RATE_LIMITED = "rate_limited"  # 429
    SCHEMA = "schema"  # unparseable or unexpected top-level response
    FATAL = "fatal"  # other 4xx; retrying risks an IP restriction


class SchemaError(ValueError):
    """The top-level response did not match the adapter's expectations."""


@dataclass
class ParsedResponse:
    provider_at: datetime | None
    observations: list[Observation] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)


@dataclass
class PollResult:
    provider: str
    requested_at: datetime
    received_at: datetime | None
    latency_ms: float | None
    status_code: int | None = None
    provider_at: datetime | None = None
    observations: list[Observation] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    error_kind: ErrorKind | None = None
    error: str | None = None
    retry_after_s: float | None = None
    raw_body: bytes | None = None

    @property
    def ok(self) -> bool:
        return self.error_kind is None
