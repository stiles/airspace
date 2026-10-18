"""Geodesic distances on the WGS84 ellipsoid. Inputs are always (latitude, longitude) degrees."""

from __future__ import annotations

from datetime import datetime

from pyproj import Geod

METERS_PER_MILE = 1609.344
METERS_PER_NM = 1852.0
# Implied speed between consecutive positions above this splits a trail
MAX_IMPLIED_SPEED_KT = 800.0

_GEOD = Geod(ellps="WGS84")


def _check(latitude: float, longitude: float) -> None:
    if not -90 <= latitude <= 90:
        raise ValueError(f"latitude {latitude} out of range; are coordinates reversed?")
    if not -180 <= longitude <= 180:
        raise ValueError(f"longitude {longitude} out of range")


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    _check(lat1, lon1)
    _check(lat2, lon2)
    _, _, meters = _GEOD.inv(lon1, lat1, lon2, lat2)
    return meters


def bearing_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    """Initial bearing in degrees [0, 360) and distance in meters from point 1 to point 2."""
    _check(lat1, lon1)
    _check(lat2, lon2)
    azimuth, _, meters = _GEOD.inv(lon1, lat1, lon2, lat2)
    return azimuth % 360, meters


def within_radius(center_lat: float, center_lon: float, lat: float, lon: float, radius_m: float) -> bool:
    """Boundary points count as inside."""
    return distance_m(center_lat, center_lon, lat, lon) <= radius_m


def destination(lat: float, lon: float, bearing_deg: float, meters: float) -> tuple[float, float]:
    """Point reached from (lat, lon) along a bearing; returns (latitude, longitude)."""
    _check(lat, lon)
    dest_lon, dest_lat, _ = _GEOD.fwd(lon, lat, bearing_deg, meters)
    return dest_lat, dest_lon


def breaks_between(
    a_at: datetime, a_lat: float, a_lon: float, b_at: datetime, b_lat: float, b_lon: float, trail_gap_s: float
) -> bool:
    """A coverage gap or an implausible jump separates two consecutive positions."""
    seconds = (b_at - a_at).total_seconds()
    if seconds > trail_gap_s:
        return True
    if seconds <= 0:
        return False
    _, meters = bearing_distance(a_lat, a_lon, b_lat, b_lon)
    return meters / METERS_PER_NM / (seconds / 3600) > MAX_IMPLIED_SPEED_KT
