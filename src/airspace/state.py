"""Current aircraft state, trails, freshness and feed status.

The same builder serves live collection and replay: feed it polls in order,
then ask for a snapshot at a given receipt time. It never makes requests and
never invents positions.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from airspace import behaviors
from airspace.classify import GYROPLANE, ROTORCRAFT, Classification, classify
from airspace.config import Config
from airspace.geometry import MAX_IMPLIED_SPEED_KT, bearing_distance, breaks_between  # noqa: F401 (re-exported)
from airspace.models import Observation, PollResult, to_iso

SNAPSHOT_VERSION = 1

MAX_EVENTS = 100

ATTRIBUTION = {"adsbfi": {"name": "adsb.fi", "url": "https://adsb.fi"}}


@dataclass
class TrailPoint:
    position_at: datetime
    latitude: float
    longitude: float
    altitude_baro_ft: int | None
    inside: bool
    # Position age at receipt was within the fade threshold
    fresh: bool
    ground_speed_kt: float | None = None
    on_ground: bool | None = None


@dataclass
class Track:
    aircraft_id: str
    latest: Observation  # newest position (or newest receipt when position time is unknown)
    last_received_at: datetime
    points: deque[TrailPoint] = field(default_factory=deque)
    # (newest point time, rotorcraft, now) -> current episodes; detection reruns only on change
    behavior_cache: tuple | None = None


@dataclass
class FeedTracker:
    polls: int = 0
    successes: int = 0
    consecutive_failures: int = 0
    last_poll_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    paused: str | None = None

    def ingest(self, result: PollResult) -> None:
        at = result.received_at or result.requested_at
        self.polls += 1
        self.last_poll_at = at
        if result.ok:
            self.successes += 1
            self.consecutive_failures = 0
            self.last_success_at = at
        else:
            self.consecutive_failures += 1
            self.last_error = result.error
            self.last_error_at = at

    def status(self, now: datetime, fade_s: float, hide_s: float) -> str:
        if self.paused:
            return "paused"
        if self.polls == 0:
            return "starting"
        if self.last_success_at is None or (now - self.last_success_at).total_seconds() > hide_s:
            return "stale"
        if self.consecutive_failures:
            return "degraded"
        if (now - self.last_success_at).total_seconds() > fade_s:
            return "delayed"
        return "live"

    def to_dict(self, now: datetime, fade_s: float, hide_s: float) -> dict:
        return {
            "status": self.status(now, fade_s, hide_s),
            "polls": self.polls,
            "successes": self.successes,
            "consecutive_failures": self.consecutive_failures,
            "last_poll_at": to_iso(self.last_poll_at),
            "last_success_at": to_iso(self.last_success_at),
            "last_success_age_s": (now - self.last_success_at).total_seconds() if self.last_success_at else None,
            "last_error": self.last_error,
            "last_error_at": to_iso(self.last_error_at),
            "paused": self.paused,
        }


def position_age_s(obs: Observation, now: datetime) -> float | None:
    """Age at `now`: age reported at the response plus time since local receipt.

    Measuring from local receipt avoids mixing the provider's clock with ours.
    """
    if obs.position_at is None:
        return None
    if obs.provider_at is None:
        return (now - obs.position_at).total_seconds()
    return (obs.provider_at - obs.position_at).total_seconds() + (now - obs.received_at).total_seconds()


class StateBuilder:
    def __init__(self, config: Config, *, identity=None, mode: str = "live"):
        self.config = config
        self.mode = mode
        self.provider = config.collector.provider
        # A RegistryCache (classify and operator); without one, category-only rules and no operators
        self.identity = identity
        self.tracks: dict[str, Track] = {}
        self.feed = FeedTracker()
        self.events: deque[dict] = deque(maxlen=MAX_EVENTS)
        self.sequence = 0

    @property
    def _retention(self) -> timedelta:
        f = self.config.freshness
        return timedelta(seconds=max(f.trail_duration_s, f.hide_s))

    def _inside(self, obs: Observation) -> bool:
        _, meters = bearing_distance(self.config.home.latitude, self.config.home.longitude, obs.latitude, obs.longitude)
        return meters <= self.config.visible_radius_m

    def ingest(self, result: PollResult) -> None:
        self.sequence += 1
        self.feed.ingest(result)
        for obs in result.observations:
            self._ingest_observation(obs)
        if result.received_at:
            self._prune(result.received_at)

    def _ingest_observation(self, obs: Observation) -> None:
        track = self.tracks.get(obs.aircraft_id)
        if track is None:
            track = self.tracks[obs.aircraft_id] = Track(obs.aircraft_id, obs, obs.received_at)
            if obs.position_at is None:
                return
        else:
            track.last_received_at = max(track.last_received_at, obs.received_at)
            latest_at = track.latest.position_at
            if obs.position_at is None:
                # Unknown position time: keep the newest receipt, but never add it to the trail
                if latest_at is None:
                    track.latest = obs
                return
            if latest_at is not None and obs.position_at <= latest_at:
                return  # duplicate or out-of-order position
            track.latest = obs

        age = obs.position_age_s
        point = TrailPoint(
            position_at=obs.position_at,
            latitude=obs.latitude,
            longitude=obs.longitude,
            altitude_baro_ft=obs.altitude_baro_ft,
            inside=self._inside(obs),
            fresh=age is not None and age <= self.config.freshness.fade_s,
            ground_speed_kt=obs.ground_speed_kt,
            on_ground=obs.on_ground,
        )
        previous = track.points[-1] if track.points else None
        track.points.append(point)
        if previous is not None:
            self._detect_crossing(track, previous, point)

    def _detect_crossing(self, track: Track, previous: TrailPoint, point: TrailPoint) -> None:
        """Entry/exit only between consecutive fresh positions with no gap between them."""
        if previous.inside == point.inside or not (previous.fresh and point.fresh):
            return
        if self._breaks(previous, point):
            return
        latest = track.latest
        self.events.append(
            {
                "type": "entered" if point.inside else "exited",
                "aircraft_id": track.aircraft_id,
                "registration": latest.registration,
                "callsign": latest.callsign,
                "at": to_iso(point.position_at),
            }
        )

    def _breaks(self, a: TrailPoint, b: TrailPoint) -> bool:
        return breaks_between(
            a.position_at, a.latitude, a.longitude, b.position_at, b.latitude, b.longitude,
            self.config.freshness.trail_gap_s,
        )

    def _prune(self, now: datetime) -> None:
        # Behavior detection needs more history than the drawn trail
        keep_s = max(self.config.freshness.trail_duration_s, self.config.behavior.history_s)
        trail_cutoff = now - timedelta(seconds=keep_s)
        drop_before = now - self._retention
        for aircraft_id in list(self.tracks):
            track = self.tracks[aircraft_id]
            while track.points and track.points[0].position_at < trail_cutoff:
                track.points.popleft()
            if track.last_received_at < drop_before:
                del self.tracks[aircraft_id]

    def _segments(self, track: Track, now: datetime) -> list[list[list[float]]]:
        cutoff = now - timedelta(seconds=self.config.freshness.trail_duration_s)
        points = [p for p in track.points if cutoff <= p.position_at <= now]
        segments: list[list[list[float]]] = []
        previous = None
        for p in points:
            if previous is None or self._breaks(previous, p):
                segments.append([])
            segments[-1].append([p.longitude, p.latitude])
            previous = p
        return [s for s in segments if len(s) > 1]

    def _behaviors(self, track: Track, kind: str, now: datetime) -> list[dict]:
        """Behaviors under way at `now`, from the kept history up to `now`."""
        params = self.config.behavior
        # Exactly the history window before `now`, so live and replay see the same positions
        start = now - timedelta(seconds=params.history_s)
        points = [p for p in track.points if start <= p.position_at <= now]
        if len(points) < 3:
            return []
        rotorcraft = kind in (ROTORCRAFT, GYROPLANE)
        key = (points[-1].position_at, len(points), rotorcraft)
        if track.behavior_cache and track.behavior_cache[0] == key:
            episodes = track.behavior_cache[1]
        else:
            episodes = behaviors.current(
                behaviors.detect(
                    [behaviors.Point(p.position_at, p.latitude, p.longitude, p.altitude_baro_ft, p.ground_speed_kt, p.on_ground)
                     for p in points],
                    params, gap_s=self.config.freshness.trail_gap_s, rotorcraft=rotorcraft,
                ),
                points[-1].position_at, params,
            )  # fmt: skip
            track.behavior_cache = (key, episodes)
        # Began at the window's edge: the episode probably started earlier than we can see
        window_full = (points[0].position_at - start).total_seconds() <= self.config.freshness.trail_gap_s
        history_start = points[0].position_at
        return [
            {
                "kind": e.kind,
                "since": to_iso(e.start),
                "duration_s": e.duration_s,
                # The episode may have begun before the kept history
                "at_least": window_full and e.start <= history_start,
                "center": [e.longitude, e.latitude],
                "radius_m": e.radius_m,
                "turns": e.turns,
                "altitude_baro_ft": e.altitude_baro_ft,
            }
            for e in episodes
        ]

    def _freshness(self, age: float | None, received_age: float) -> str:
        f = self.config.freshness
        if age is None:
            return "unknown" if received_age <= f.hide_s else "hidden"
        if age > f.hide_s:
            return "hidden"
        if age > f.fade_s:
            return "fading"
        return "fresh"

    def _aircraft(self, track: Track, now: datetime) -> dict:
        obs = track.latest
        home = self.config.home
        bearing, meters = bearing_distance(home.latitude, home.longitude, obs.latitude, obs.longitude)
        age = position_age_s(obs, now)
        received_age = (now - track.last_received_at).total_seconds()
        if self.identity is not None:
            classification: Classification = self.identity.classify(track.aircraft_id, obs.category)
            operator = self.identity.operator(
                track.aircraft_id, kind=classification.kind, registration=obs.registration,
                callsign=obs.callsign, owner=obs.owner_operator,
            )  # fmt: skip
        else:
            classification, operator = classify(obs.category), None
        return {
            "id": track.aircraft_id,
            "registration": obs.registration,
            "callsign": obs.callsign,
            "aircraft_type": obs.aircraft_type,
            "description": obs.aircraft_description,
            "category": obs.category,
            "class": classification.kind,
            "class_rule": classification.rule,
            "operator": operator.name if operator else None,
            "operator_short": operator.short if operator else None,
            "role": operator.role if operator else None,
            "role_rule": operator.rule if operator else None,
            "role_source": operator.source if operator else None,
            "role_checked": operator.checked if operator else None,
            "type_note": operator.note if operator else None,
            "_operator": operator,
            "position_source": obs.position_source,
            "longitude": obs.longitude,
            "latitude": obs.latitude,
            "altitude_baro_ft": obs.altitude_baro_ft,
            "altitude_geom_ft": obs.altitude_geom_ft,
            "on_ground": obs.on_ground,
            "ground_speed_kt": obs.ground_speed_kt,
            "track_deg": obs.track_deg,
            "vertical_rate_fpm": obs.vertical_rate_fpm,
            "squawk": obs.squawk,
            "emergency": obs.emergency,
            "position_at": to_iso(obs.position_at),
            "position_time_basis": obs.position_time_basis.value,
            "time_flag": obs.time_flag,
            "position_age_s": age,
            "freshness": self._freshness(age, received_age),
            "last_received_at": to_iso(track.last_received_at),
            "in_latest_response": track.last_received_at == self.feed.last_success_at,
            "distance_m": meters,
            "bearing_deg": bearing,
            "inside_visible": meters <= self.config.visible_radius_m,
            "trail": self._segments(track, now),
            "behaviors": self._behaviors(track, classification.kind, now),
        }

    def snapshot(self, now: datetime) -> dict:
        f = self.config.freshness
        shown, last_seen = [], []
        for track in self.tracks.values():
            # Replay may hold records newer than `now` only if misused; never show them
            if track.last_received_at > now:
                continue
            item = self._aircraft(track, now)
            item.pop("_operator")
            if item["freshness"] == "hidden":
                last_seen.append(
                    {k: item[k] for k in ("id", "registration", "callsign", "class", "operator_short", "role",
                                          "longitude", "latitude", "position_at", "last_received_at",
                                          "inside_visible")}  # fmt: skip
                )
            else:
                shown.append(item)
        shown.sort(key=lambda a: a["distance_m"])
        last_seen.sort(key=lambda a: a["last_received_at"], reverse=True)
        area = self.config.area
        return {
            "version": SNAPSHOT_VERSION,
            "mode": self.mode,
            "generated_at": to_iso(now),
            "sequence": self.sequence,
            "source": {"provider": self.provider, **ATTRIBUTION.get(self.provider, {})},
            "area": {
                "home": self.config.home.model_dump(),
                "visible_radius_m": self.config.visible_radius_m,
                "collection_radius_m": self.config.collection_radius_m,
                "max_altitude_baro_ft": area.max_altitude_baro_ft,
            },
            "thresholds": f.model_dump(),
            "feed": self.feed.to_dict(now, f.fade_s, f.hide_s),
            "aircraft": shown,
            "last_seen": last_seen,
            "events": list(self.events),
        }

    def aircraft_detail(self, aircraft_id: str, now: datetime) -> dict | None:
        track = self.tracks.get(aircraft_id)
        if track is None:
            return None
        item = self._aircraft(track, now)
        operator = item.pop("_operator")
        # Owners the FAA registry lists as individuals are never shown
        private = operator is not None and operator.private
        item["owner_operator"] = None if private else track.latest.owner_operator
        item["owner_hidden"] = private
        item["recent_positions"] = [
            {
                "position_at": to_iso(p.position_at),
                "longitude": p.longitude,
                "latitude": p.latitude,
                "altitude_baro_ft": p.altitude_baro_ft,
                "inside_visible": p.inside,
            }
            for p in track.points
        ]
        return item
