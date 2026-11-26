import json
from pathlib import Path

import pytest

from airspace.outlines import OutlineError, load

REPO = Path(__file__).resolve().parents[1]


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def test_repo_boundary_loads():
    fc = load([REPO / "boundaries" / "los-angeles.geojson"])
    assert len(fc["features"]) == 1
    feature = fc["features"][0]
    assert feature["properties"] == {"name": "LA city limits"}
    assert feature["geometry"]["type"] == "Polygon"


def test_merges_files_and_bare_geometries(tmp_path):
    line = {"type": "LineString", "coordinates": [[-118.5, 34.0], [-118.4, 34.1]]}
    a = write(tmp_path, "a.geojson", line)
    b = write(tmp_path, "b.geojson", {"type": "Feature", "properties": {"name": "B", "extra": 1}, "geometry": line})
    fc = load([a, b])
    assert [f["properties"]["name"] for f in fc["features"]] == [None, "B"]
    assert load([]) == {"type": "FeatureCollection", "features": []}


def test_rejects_points_projected_coordinates_and_bad_files(tmp_path):
    point = write(tmp_path, "p.geojson", {"type": "Point", "coordinates": [-118.4, 34.0]})
    with pytest.raises(OutlineError, match="polygons or lines"):
        load([point])
    # Web Mercator meters, a common mistake with Census and county files
    mercator = write(tmp_path, "m.geojson", {"type": "LineString", "coordinates": [[-13182000, 4028000], [-13181000, 4029000]]})
    with pytest.raises(OutlineError, match="EPSG:4326"):
        load([mercator])
    with pytest.raises(OutlineError, match="missing.geojson"):
        load([tmp_path / "missing.geojson"])
