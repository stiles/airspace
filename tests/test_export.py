import csv
import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from airspace import export
from airspace.api import create_app
from airspace.cli import app as cli
from airspace.replay import RegistryCache
from helpers import ErrorKind, at, obs, poll


@pytest.fixture
def recorded(config, store):
    sid = store.start_session("adsbfi", "collect", config.model_dump(mode="json"))
    for i in range(12):
        t = at(10 * i)
        observations = [obs(t, "heli", miles=1 + i * 0.05, category="A7", registration="N225LA",
                            callsign="N225LA", altitude_baro_ft=1000 + i, track_deg=90.0, owner_operator="LAPD")]
        if i < 3:
            observations.append(obs(t, "jet", miles=4, bearing=180, category="A3"))
        if 6 <= i < 9:
            observations = []  # the helicopter drops out for 30s, then reappears: a gap
        observations.append(obs(t, "veh", category="C2", on_ground=True))
        store.record_poll(sid, poll(t, observations))
    store.record_poll(sid, poll(at(125), error_kind=ErrorKind.TRANSIENT))
    return sid


def test_json_export_provenance(config, store, recorded):
    body = export.to_json(store, recorded, config, RegistryCache())
    meta = body["metadata"]
    assert meta["source"]["name"] == "adsb.fi" and "noncommercial" in meta["source"]["terms"]
    assert meta["session"]["id"] == recorded
    assert meta["collection"]["home"] == "omitted from exports"
    assert meta["coverage_gaps"] == [
        {"type": "failed_poll", "at": at(125).isoformat(timespec="milliseconds"), "error_kind": "transient",
         "error": "timeout: ReadTimeout"}
    ]
    assert "position_at" in body["fields"]
    # The session's config snapshot holds the home point; exports must not carry it or distances from it
    text = json.dumps(body)
    assert '"home": {' not in text and '"label": "Home"' not in text and "distance" not in text
    assert "latitude" not in json.dumps(meta)
    heli = [o for o in body["observations"] if o["aircraft_id"] == "heli"]
    assert heli[0]["classification"] == "rotorcraft" and heli[0]["classification_rule"] == "adsb_category"
    assert all(set(o) >= {"received_at", "position_at", "position_time_basis", "new_position"} for o in heli)


def test_geojson_tracks_split_on_gaps(config, store, recorded):
    fc = export.to_geojson(store, recorded, config, RegistryCache(), aircraft=["heli"])
    segments = [f for f in fc["features"] if f["properties"]["aircraft_id"] == "heli"]
    assert [s["properties"]["segment"] for s in segments] == [1, 2]
    first = segments[0]
    assert first["geometry"]["type"] == "LineString"
    assert len(first["properties"]["coordinate_times"]) == len(first["geometry"]["coordinates"])
    lon, lat = first["geometry"]["coordinates"][0]
    assert -119 < lon < -118 and 33 < lat < 35
    assert fc["metadata"]["geometry"] == "tracks" and "never joined" in fc["metadata"]["track_rules"]


def test_geojson_points_and_filters(config, store, recorded):
    fc = export.to_geojson(store, recorded, config, RegistryCache(), geometry="points",
                           start=at(0), end=at(20), aircraft=["JET"])
    assert {f["properties"]["aircraft_id"] for f in fc["features"]} == {"jet"}
    assert len(fc["features"]) == 3
    assert "latitude" not in fc["features"][0]["properties"]
    with pytest.raises(ValueError):
        export.to_geojson(store, recorded, config, RegistryCache(), geometry="lines")


def test_flighttracer_folder(config, store, recorded, tmp_path):
    folder = tmp_path / "ft"
    meta = export.to_flighttracer(store, recorded, config, folder, "HELI")
    with (folder / "trace.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == export.FLIGHTTRACER_COLUMNS
    assert len(rows) == meta["points"] == 9  # new airborne positions only
    assert rows[0]["icao"] == "heli" and rows[0]["flight_leg"] == "N225LA_leg1"
    assert rows[0]["leg_detection"] == "unmarked" and rows[0]["altitude"] == "1000"
    times = [r["point_time_utc"] for r in rows]
    assert times == sorted(times)
    assert json.loads((folder / "airspace.json").read_text())["source"]["name"] == "adsb.fi"
    with pytest.raises(LookupError):
        export.to_flighttracer(store, recorded, config, tmp_path / "none", "veh")  # ground only


def test_api_timeline_and_export(config, store, recorded):
    with TestClient(create_app(config, mode="replay")) as client:
        tl = client.get(f"/api/sessions/{recorded}/timeline").json()
        assert len(tl["polls"]["t"]) == 13
        assert tl["polls"]["ok"][-1] is False
        assert tl["polls"]["rotorcraft"][0] == 1 and tl["polls"]["rotorcraft"][7] == 0
        assert tl["start"] < tl["end"]
        assert client.get("/api/sessions/99/timeline").status_code == 404

        r = client.get(f"/api/sessions/{recorded}/export", params={"aircraft": "heli"})
        assert r.headers["content-type"].startswith("application/geo+json")
        assert f'airspace-session-{recorded}-heli-tracks.geojson' in r.headers["content-disposition"]
        assert r.json()["type"] == "FeatureCollection"
        r = client.get(f"/api/sessions/{recorded}/export", params={"format": "json", "end": at(5).isoformat()})
        assert len(r.json()["observations"]) == 3  # heli, jet, vehicle at t=0
        assert client.get(f"/api/sessions/{recorded}/export", params={"format": "csv"}).status_code == 422


def test_cli_export(config, store, recorded, tmp_path, monkeypatch):
    path = tmp_path / "airspace.toml"
    path.write_text(f'[home]\nlatitude = 34.0\nlongitude = -118.42\n[storage]\ndata_dir = "{config.storage.data_dir}"\n'
                    '[enrich]\n')
    out = tmp_path / "out.geojson"
    result = CliRunner().invoke(cli, ["export", "-c", str(path), "--session", str(recorded), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["metadata"]["session"]["id"] == recorded
    folder = tmp_path / "ft"
    result = CliRunner().invoke(cli, ["export", "-c", str(path), "--format", "flighttracer", "--aircraft", "heli",
                                      "-o", str(folder)])
    assert result.exit_code == 0 and (folder / "trace.csv").exists()
    result = CliRunner().invoke(cli, ["export", "-c", str(path), "--format", "flighttracer"])
    assert result.exit_code != 0
