from datetime import UTC, datetime, timedelta

from airspace.geometry import METERS_PER_MILE, destination
from airspace.models import ErrorKind, Observation, PollResult, PositionTimeBasis

T0 = datetime(2026, 10, 17, 18, 0, tzinfo=UTC)
HOME = (34.0, -118.42)


def obs(t, aircraft_id, *, miles=2.0, bearing=0.0, age=1.0, **kw):
    lat, lon = destination(*HOME, bearing, miles * METERS_PER_MILE)
    known = age is not None
    return Observation(
        provider="adsbfi", aircraft_id=aircraft_id, received_at=t, provider_at=t,
        position_at=t - timedelta(seconds=age) if known else None,
        position_time_basis=PositionTimeBasis.DERIVED_AGE if known else PositionTimeBasis.UNKNOWN,
        latitude=lat, longitude=lon, **kw,
    )


def poll(t, observations=(), error_kind=None):
    return PollResult(
        provider="adsbfi", requested_at=t, received_at=None if error_kind else t, latency_ms=100,
        status_code=None if error_kind else 200, provider_at=None if error_kind else t,
        observations=list(observations), error_kind=error_kind,
        error="timeout: ReadTimeout" if error_kind else None,
    )


def at(seconds):
    return T0 + timedelta(seconds=seconds)


__all__ = ["T0", "HOME", "obs", "poll", "at", "ErrorKind"]
