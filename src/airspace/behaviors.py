"""Flight behaviors inferred from recorded positions: circling, hovering and low flight.

These describe what a track shows, not why an aircraft was there. One function serves
live state, replay, reports and exports, so a behavior on the map matches the record.

Positions are projected to local meters around each segment's first point, which is
accurate to well under a meter over the few kilometers an episode spans.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from airspace.config import Behavior
from airspace.geometry import breaks_between

EARTH_RADIUS_M = 6_371_008.8
KT_PER_MPS = 1.943844
# Steps shorter than this carry no usable direction (GPS jitter while slow)
MIN_STEP_M = 15.0
# Fewest positions for a circling episode
MIN_CIRCLE_POINTS = 6

CIRCLING, HOVERING, LOW = "circling", "hovering", "low"


@dataclass(frozen=True)
class Point:
    at: datetime
    latitude: float
    longitude: float
    altitude_baro_ft: int | None
    ground_speed_kt: float | None
    on_ground: bool | None


@dataclass(frozen=True)
class Episode:
    kind: str
    start: datetime
    end: datetime
    longitude: float  # center
    latitude: float
    radius_m: float  # median distance from the center
    turns: float | None  # circling only: net turn / 360
    altitude_baro_ft: float | None  # median
    points: int

    @property
    def duration_s(self) -> float:
        return (self.end - self.start).total_seconds()


def segments(points: Sequence[Point], gap_s: float) -> list[list[Point]]:
    """Split at coverage gaps and implausible jumps, by the same rule as map trails."""
    out: list[list[Point]] = []
    for p in points:
        if out and not breaks_between(
            out[-1][-1].at, out[-1][-1].latitude, out[-1][-1].longitude, p.at, p.latitude, p.longitude, gap_s
        ):
            out[-1].append(p)
        else:
            out.append([p])
    return out


def _project(points: Sequence[Point]) -> list[tuple[float, float]]:
    lat0, lon0 = math.radians(points[0].latitude), math.radians(points[0].longitude)
    cos0 = math.cos(lat0)
    return [
        ((math.radians(p.longitude) - lon0) * cos0 * EARTH_RADIUS_M, (math.radians(p.latitude) - lat0) * EARTH_RADIUS_M)
        for p in points
    ]


def _unproject(points: Sequence[Point], x: float, y: float) -> tuple[float, float]:
    lat0 = math.radians(points[0].latitude)
    lat = math.degrees(lat0 + y / EARTH_RADIUS_M)
    lon = points[0].longitude + math.degrees(x / (EARTH_RADIUS_M * math.cos(lat0)))
    return lon, lat


def _episode(kind: str, seg: Sequence[Point], xy, i: int, j: int, turns: float | None = None) -> Episode:
    """Episode over seg[i..j] inclusive."""
    pts = xy[i : j + 1]
    cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    lon, lat = _unproject(seg, cx, cy)
    alts = [p.altitude_baro_ft for p in seg[i : j + 1] if p.altitude_baro_ft is not None]
    return Episode(
        kind=kind,
        start=seg[i].at,
        end=seg[j].at,
        longitude=lon,
        latitude=lat,
        radius_m=statistics.median(math.hypot(x - cx, y - cy) for x, y in pts),
        turns=turns,
        altitude_baro_ft=statistics.median(alts) if alts else None,
        points=j - i + 1,
    )


def _max_spread(xy, i: int, j: int) -> float:
    pts = xy[i : j + 1]
    cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    return max(math.hypot(x - cx, y - cy) for x, y in pts)


def _circling(seg: Sequence[Point], xy, params: Behavior) -> list[Episode]:
    """Windows whose net turn reaches a full circle while staying within a bounded radius."""
    # Direction of travel between points far enough apart to have one
    keep = [0]
    for k in range(1, len(seg)):
        if math.hypot(xy[k][0] - xy[keep[-1]][0], xy[k][1] - xy[keep[-1]][1]) >= MIN_STEP_M:
            keep.append(k)
    if len(keep) < 4:
        return []
    headings = [
        math.atan2(xy[b][1] - xy[a][1], xy[b][0] - xy[a][0]) for a, b in zip(keep, keep[1:])
    ]
    # Cumulative signed turn (degrees) at each kept point
    cum = [0.0, 0.0]
    for h0, h1 in zip(headings, headings[1:]):
        d = math.degrees(h1 - h0)
        d = (d + 180) % 360 - 180
        cum.append(cum[-1] + d)

    intervals: list[tuple[int, int]] = []
    if any(p.on_ground for p in seg):
        return []  # taxiing and pattern work on the ground aren't circling
    for a in range(len(keep)):
        for b in range(a + 2, len(keep)):
            if (seg[keep[b]].at - seg[keep[a]].at).total_seconds() > params.circle_window_s:
                break
            # Too far from the start to share an orbit with it: straight-line flight stops here
            if math.dist(xy[keep[a]], xy[keep[b]]) > 2 * params.circle_max_radius_m:
                break
            turn = cum[b] - cum[a]
            if abs(turn) < 360:
                continue
            i, j = keep[a], keep[b]
            if _max_spread(xy, i, j) > params.circle_max_radius_m:
                break
            if j - i + 1 < MIN_CIRCLE_POINTS:
                continue  # a sparse full turn; extend until there are enough positions
            seconds = (seg[j].at - seg[i].at).total_seconds()
            path = sum(math.hypot(xy[k + 1][0] - xy[k][0], xy[k + 1][1] - xy[k][1]) for k in range(i, j))
            if seconds > 0 and path / seconds * KT_PER_MPS >= params.circle_min_speed_kt:
                intervals.append((i, j))
            break
    episodes = []
    position = {k: n for n, k in enumerate(keep)}
    for i, j in _merge_nearby(intervals, seg, xy, params):
        # Largest one-way turn within the episode, so a reversal doesn't cancel earlier orbits
        span = cum[position[i] : position[j] + 1]
        turns = (max(span) - min(span)) / 360
        if turns >= 1:  # a remnant clipped by the previous episode isn't an orbit
            episodes.append(_episode(CIRCLING, seg, xy, i, j, turns=round(turns, 1)))
    return episodes


def _centroid(xy, i: int, j: int) -> tuple[float, float]:
    pts = xy[i : j + 1]
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def _merge_nearby(intervals: list[tuple[int, int]], seg: Sequence[Point], xy, params: Behavior) -> list[tuple[int, int]]:
    """Merge windows that circle the same spot, overlapping or a short gap apart; an orbit that
    moves on starts a new episode."""
    merged: list[tuple[int, int]] = []
    anchor: tuple[float, float] | None = None  # center of the latest window, so slow drift is followed
    for i, j in sorted(intervals):
        if merged and j <= merged[-1][1]:
            continue  # inside an episode already
        center = _centroid(xy, i, j)
        near = anchor is not None and math.dist(center, anchor) <= params.circle_max_radius_m / 2
        close_in_time = merged and (seg[i].at - seg[merged[-1][1]].at).total_seconds() <= params.circle_merge_gap_s
        if merged and near and close_in_time:
            merged[-1] = (merged[-1][0], max(merged[-1][1], j))
        else:
            if merged and i <= merged[-1][1]:
                i = merged[-1][1]  # episodes don't overlap
            merged.append((i, j))
        anchor = center
    return merged


def _runs(seg: Sequence[Point], ok) -> list[tuple[int, int]]:
    runs, start = [], None
    for k, p in enumerate(seg):
        if ok(k, p):
            start = k if start is None else start
        elif start is not None:
            runs.append((start, k - 1))
            start = None
    if start is not None:
        runs.append((start, len(seg) - 1))
    return runs


def _hovering(seg: Sequence[Point], xy, params: Behavior) -> list[Episode]:
    """Airborne and slow, staying within a small drift, for long enough."""

    def slow(k: int, p: Point) -> bool:
        if p.on_ground:
            return False
        if p.ground_speed_kt is not None:
            return p.ground_speed_kt <= params.hover_max_speed_kt
        if k == 0:
            return False
        seconds = (p.at - seg[k - 1].at).total_seconds()
        step = math.hypot(xy[k][0] - xy[k - 1][0], xy[k][1] - xy[k - 1][1])
        return seconds > 0 and step / seconds * KT_PER_MPS <= params.hover_max_speed_kt

    out = []
    for i, j in _runs(seg, slow):
        if (seg[j].at - seg[i].at).total_seconds() >= params.hover_min_s and _max_spread(xy, i, j) <= params.hover_max_drift_m:
            out.append(_episode(HOVERING, seg, xy, i, j))
    return out


def _low(seg: Sequence[Point], xy, params: Behavior) -> list[Episode]:
    """Airborne below the altitude threshold for long enough (barometric, not height above ground)."""

    def low(k: int, p: Point) -> bool:
        return not p.on_ground and p.altitude_baro_ft is not None and p.altitude_baro_ft <= params.low_altitude_ft

    return [
        _episode(LOW, seg, xy, i, j)
        for i, j in _runs(seg, low)
        if (seg[j].at - seg[i].at).total_seconds() >= params.low_min_s
    ]


def detect(points: Sequence[Point], params: Behavior, *, gap_s: float, rotorcraft: bool) -> list[Episode]:
    """Episodes in time order. Hovering and low flight are helicopter behaviors; circling applies to all."""
    episodes: list[Episode] = []
    for seg in segments(sorted(points, key=lambda p: p.at), gap_s):
        if len(seg) < 3:
            continue
        xy = _project(seg)
        episodes += _circling(seg, xy, params)
        if rotorcraft:
            episodes += _hovering(seg, xy, params)
            episodes += _low(seg, xy, params)
    return sorted(episodes, key=lambda e: (e.start, e.kind))


def current(episodes: Sequence[Episode], latest: datetime, params: Behavior) -> list[Episode]:
    """Episodes still under way: ending at, or within the ongoing tolerance of, the latest position."""
    return [e for e in episodes if (latest - e.end).total_seconds() <= params.ongoing_s]
