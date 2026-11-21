import asyncio
import copy
import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from airspace.config import Config
from airspace.models import from_iso
from airspace.weather import WeatherService, parse_observation

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "nws_ksmo_latest.json").read_text())
STATIONS_URL = "https://api.weather.gov/gridpoints/LOX/147,44/stations"
KSMO = {
    "geometry": {"type": "Point", "coordinates": [-118.45139, 34.01583]},
    "properties": {"stationIdentifier": "KSMO", "name": "Santa Monica, Santa Monica Municipal Airport"},
}


def nws(routes: dict):
    """MockTransport serving NWS paths; a route value of an int is that HTTP status."""
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        body = routes.get(request.url.path, 404)
        if isinstance(body, int):
            return httpx.Response(body)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler), calls


def routes(**overrides):
    base = {
        "/points/34.0000,-118.4200": {"properties": {"observationStations": STATIONS_URL}},
        "/gridpoints/LOX/147,44/stations": {"features": [KSMO]},
        "/stations/KSMO": KSMO,
        "/stations/KSMO/observations/latest": FIXTURE,
    }
    return {**base, **overrides}


def run(coro):
    return asyncio.run(coro)


async def refresh(service: WeatherService, transport):
    async with httpx.AsyncClient(transport=transport) as client:
        service.client = client
        await service.refresh()


def test_parse_converts_units_and_finds_no_ceiling_under_scattered_clouds():
    obs = parse_observation(FIXTURE["properties"])
    assert obs["observed_at"] == "2026-11-19T23:00:00.000+00:00"
    assert obs["description"] == "Partly Cloudy"
    assert obs["temperature_f"] == 64 and obs["dewpoint_f"] == 54
    assert obs["humidity_pct"] == 68
    assert (obs["wind_direction_deg"], obs["wind_speed_mph"], obs["wind_gust_mph"]) == (230, 15, 21)
    assert obs["visibility_mi"] == 10.0
    assert obs["clouds"][0] == {"amount": "FEW", "base_ft": 2700}
    assert obs["ceiling_ft"] is None  # FEW and SCT layers aren't a ceiling


def test_parse_takes_lowest_broken_layer_as_ceiling_and_drops_rejected_values():
    props = copy.deepcopy(FIXTURE["properties"])
    props["cloudLayers"] = [
        {"amount": "SCT", "base": {"unitCode": "wmoUnit:m", "value": 300}},
        {"amount": "OVC", "base": {"unitCode": "wmoUnit:m", "value": 1500}},
        {"amount": "BKN", "base": {"unitCode": "wmoUnit:m", "value": 600}},
    ]
    props["temperature"]["qualityControl"] = "X"
    props["windSpeed"] = {"unitCode": "wmoUnit:km_h-1", "value": None, "qualityControl": "Z"}
    obs = parse_observation(props)
    assert obs["ceiling_ft"] == 2000
    assert obs["temperature_f"] is None
    assert obs["wind_speed_mph"] is None


def test_parse_reports_no_sky_cover_as_none():
    props = {**FIXTURE["properties"], "cloudLayers": []}
    assert parse_observation(props)["clouds"] is None


def test_finds_nearest_station_from_points(config):
    transport, calls = nws(routes())
    service = WeatherService(config, None)
    assert service.snapshot(from_iso("2026-11-19T23:30:00+00:00"))["status"] == "starting"
    run(refresh(service, transport))
    assert calls == ["/points/34.0000,-118.4200", "/gridpoints/LOX/147,44/stations", "/stations/KSMO/observations/latest"]
    assert service.station["id"] == "KSMO" and 1500 < service.station["distance_m"] < 4000
    snap = service.snapshot(from_iso("2026-11-19T23:30:00+00:00"))
    assert snap["status"] == "ok" and snap["age_s"] == 1800
    assert snap["observation"]["temperature_f"] == 64
    assert snap["source"]["name"] == "National Weather Service"

    run(refresh(service, transport))
    assert calls[-1] == "/stations/KSMO/observations/latest" and len(calls) == 4  # station looked up once


def test_configured_station_skips_points(config):
    config = Config.model_validate({**config.model_dump(), "weather": {"enabled": True, "station": "ksmo"}})
    transport, calls = nws(routes())
    service = WeatherService(config, None)
    run(refresh(service, transport))
    assert calls == ["/stations/KSMO", "/stations/KSMO/observations/latest"]
    assert service.station["name"].startswith("Santa Monica")


def test_failure_keeps_last_observation_and_backs_off(config):
    service = WeatherService(config, None)
    run(refresh(service, nws(routes())[0]))
    run(refresh(service, nws(routes(**{"/stations/KSMO/observations/latest": 503}))[0]))
    assert service.observation is not None and "503" in service.error
    assert service.next_delay() == 60
    run(refresh(service, nws(routes(**{"/stations/KSMO/observations/latest": 503}))[0]))
    assert service.next_delay() == 120
    observed = from_iso(service.observation["observed_at"])
    assert service.snapshot(observed + timedelta(minutes=50))["status"] == "ok"
    assert service.snapshot(observed + timedelta(hours=3))["status"] == "stale"

    run(refresh(service, nws(routes())[0]))
    assert service.error is None and service.next_delay() == config.weather.interval_s


def test_no_station_is_unavailable(config):
    service = WeatherService(config, None)
    run(refresh(service, nws(routes(**{"/points/34.0000,-118.4200": 404}))[0]))
    snap = service.snapshot(from_iso("2026-11-19T23:30:00+00:00"))
    assert snap["status"] == "unavailable" and snap["observation"] is None
    assert "not found" in snap["error"]


def test_station_setting_is_validated(config):
    with pytest.raises(ValueError):
        Config.model_validate({**config.model_dump(), "weather": {"station": "../points"}})
