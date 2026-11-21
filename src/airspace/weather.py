"""Current conditions at home from the National Weather Service (api.weather.gov)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx

from airspace.config import Config
from airspace.geometry import METERS_PER_MILE, distance_m
from airspace.models import from_iso, to_iso, utcnow

log = logging.getLogger(__name__)

NWS_URL = "https://api.weather.gov"
SOURCE = {"name": "National Weather Service", "url": "https://www.weather.gov"}
# Stations report about hourly; past two hours the station has stopped reporting
STALE_S = 2 * 3600
CEILING = {"BKN", "OVC", "VV"}
FEET_PER_METER = 3.28084
# QC flag X: the value failed NWS quality control
REJECTED = {"X"}
SPEED_TO_MPH = {"km_h-1": 1 / 1.609344, "m_s-1": 2.236936, "kt": 1.150779}


class WeatherError(Exception):
    pass


def _value(prop: dict | None) -> tuple[float | None, str]:
    if not prop or prop.get("value") is None or prop.get("qualityControl") in REJECTED:
        return None, ""
    return float(prop["value"]), str(prop.get("unitCode", "")).split(":")[-1]


def _fahrenheit(prop: dict | None) -> float | None:
    value, unit = _value(prop)
    if value is None:
        return None
    return value * 9 / 5 + 32 if unit == "degC" else value


def _mph(prop: dict | None) -> float | None:
    value, unit = _value(prop)
    if value is None:
        return None
    if unit not in SPEED_TO_MPH:
        raise WeatherError(f"unexpected wind unit {unit!r}")
    return value * SPEED_TO_MPH[unit]


def _round(value: float | None, digits: int = 0) -> float | None:
    return None if value is None else round(value, digits)


def parse_observation(props: dict) -> dict:
    """One observation's properties, in °F, mph, miles and feet. Missing values stay None."""
    if not props.get("timestamp"):
        raise WeatherError("observation has no timestamp")
    clouds = []
    for layer in props.get("cloudLayers") or []:
        base, _ = _value(layer.get("base"))
        clouds.append({"amount": layer.get("amount"), "base_ft": _round(base * FEET_PER_METER if base is not None else None, -2)})
    ceilings = [c["base_ft"] for c in clouds if c["amount"] in CEILING and c["base_ft"] is not None]
    direction, _ = _value(props.get("windDirection"))
    visibility, _ = _value(props.get("visibility"))
    humidity, _ = _value(props.get("relativeHumidity"))
    return {
        "observed_at": to_iso(from_iso(props["timestamp"])),
        "description": props.get("textDescription") or None,
        "temperature_f": _round(_fahrenheit(props.get("temperature"))),
        "dewpoint_f": _round(_fahrenheit(props.get("dewpoint"))),
        "humidity_pct": _round(humidity),
        "wind_direction_deg": _round(direction),
        "wind_speed_mph": _round(_mph(props.get("windSpeed"))),
        "wind_gust_mph": _round(_mph(props.get("windGust"))),
        "visibility_mi": _round(visibility / METERS_PER_MILE if visibility is not None else None, 1),
        "ceiling_ft": min(ceilings) if ceilings else None,
        # None when the station didn't report sky cover, [] never
        "clouds": clouds or None,
    }


class WeatherService:
    """Polls one station's latest observation; never raises out of `run`."""

    def __init__(self, config: Config, client: httpx.AsyncClient, *, now=utcnow):
        self.config = config
        self.client = client
        self.now = now
        self.station: dict | None = None
        self.observation: dict | None = None
        self.checked_at: datetime | None = None
        self.error: str | None = None
        self.failures = 0

    async def _get(self, path_or_url: str) -> dict:
        url = path_or_url if path_or_url.startswith("http") else f"{NWS_URL}{path_or_url}"
        response = await self.client.get(url, headers={"Accept": "application/geo+json"}, follow_redirects=True)
        if response.status_code == 404:
            raise WeatherError(f"not found: {url}")
        response.raise_for_status()
        return response.json()

    def _station(self, feature: dict) -> dict:
        props = feature.get("properties") or {}
        lon, lat = (feature.get("geometry") or {}).get("coordinates", (None, None))[:2]
        home = self.config.home
        return {
            "id": props["stationIdentifier"],
            "name": props.get("name"),
            "distance_m": round(distance_m(home.latitude, home.longitude, lat, lon)) if lat is not None else None,
        }

    async def find_station(self) -> dict:
        if self.config.weather.station:
            return self._station(await self._get(f"/stations/{self.config.weather.station.upper()}"))
        home = self.config.home
        # NWS redirects points with more than four decimals
        point = await self._get(f"/points/{home.latitude:.4f},{home.longitude:.4f}")
        stations_url = (point.get("properties") or {}).get("observationStations")
        if not stations_url:
            raise WeatherError("NWS lists no observation stations for the home point")
        stations = (await self._get(stations_url)).get("features") or []
        if not stations:
            raise WeatherError("NWS lists no observation stations for the home point")
        # Sorted nearest first by NWS
        return self._station(stations[0])

    async def refresh(self) -> None:
        try:
            if self.station is None:
                self.station = await self.find_station()
                log.info("weather station %s (%s)", self.station["id"], self.station["name"])
            body = await self._get(f"/stations/{self.station['id']}/observations/latest")
            self.observation = parse_observation(body.get("properties") or {})
            self.error = None
            self.failures = 0
        except (httpx.HTTPError, WeatherError, KeyError, TypeError, ValueError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.failures += 1
            log.warning("weather refresh failed: %s", self.error)
        self.checked_at = self.now()

    def next_delay(self) -> float:
        interval = self.config.weather.interval_s
        if not self.failures:
            return interval
        return min(interval, 60.0 * 2 ** (self.failures - 1))

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.refresh()
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.next_delay())
            except TimeoutError:
                pass

    def snapshot(self, now: datetime) -> dict:
        obs = self.observation
        age = (now - from_iso(obs["observed_at"])).total_seconds() if obs else None
        if obs and age is not None and age <= STALE_S:
            status = "ok"
        elif obs:
            status = "stale"
        elif self.checked_at is None:
            status = "starting"
        else:
            status = "unavailable"
        return {
            "status": status,
            "source": SOURCE,
            "station": self.station,
            "observation": obs,
            "age_s": age,
            "checked_at": to_iso(self.checked_at) if self.checked_at else None,
            "error": self.error,
        }
