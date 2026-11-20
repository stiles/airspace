"""Boundaries drawn as faint outlines on the map, such as city limits."""

from __future__ import annotations

import json
from pathlib import Path

LINEAR = {"Polygon", "MultiPolygon", "LineString", "MultiLineString"}


class OutlineError(ValueError):
    pass


def _positions(coords):
    if coords and isinstance(coords[0], (int, float)):
        yield coords
        return
    for part in coords:
        yield from _positions(part)


def load(paths: list[Path]) -> dict:
    """Merge outline files into one FeatureCollection with only each feature's name."""
    features = []
    for path in paths:
        try:
            data = json.loads(Path(path).expanduser().read_text())
        except (OSError, ValueError) as exc:
            raise OutlineError(f"{path}: {exc}") from None
        kind = data.get("type") if isinstance(data, dict) else None
        if kind == "FeatureCollection":
            items = data.get("features") or []
        elif kind == "Feature":
            items = [data]
        else:
            items = [{"geometry": data, "properties": {}}]
        for i, item in enumerate(items):
            geometry = item.get("geometry") or {}
            if geometry.get("type") not in LINEAR:
                raise OutlineError(f"{path}: feature {i} is {geometry.get('type')}; outlines must be polygons or lines")
            for lon, lat, *_ in _positions(geometry.get("coordinates") or []):
                if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                    raise OutlineError(f"{path}: coordinates must be longitude/latitude (EPSG:4326); reproject it")
            name = (item.get("properties") or {}).get("name")
            features.append({"type": "Feature", "geometry": geometry, "properties": {"name": name}})
    return {"type": "FeatureCollection", "features": features}
