"""adsb.fi open data v3 adapter.

Field meanings follow readsb's aircraft JSON, which adsb.fi's v3 geographic
endpoint mirrors. Verified against a captured response on 2026-10-17; see
tests/fixtures/adsbfi_v3_la.json.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from airspace.models import Observation, ParsedResponse, PositionTimeBasis, Rejection, SchemaError

BASE_URL = "https://opendata.adsb.fi/api/v3"

# Small negative ages can come from rounding; anything beyond this is flagged
FUTURE_TOLERANCE_S = 1.0


class _Aircraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hex: str = Field(min_length=1)
    type: str | None = None
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    seen_pos: float | None = None
    alt_baro: float | Literal["ground"] | None = None
    alt_geom: float | None = None
    gs: float | None = None
    track: float | None = Field(default=None, ge=0, le=360)
    baro_rate: float | None = None
    geom_rate: float | None = None
    flight: str | None = None
    r: str | None = None
    t: str | None = None
    desc: str | None = None
    category: str | None = None
    ownOp: str | None = None
    squawk: str | None = None
    emergency: str | None = None


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _round(value: float | None) -> int | None:
    return None if value is None else round(value)


class AdsbFi:
    name = "adsbfi"
    max_radius_nm = 250.0

    def url(self, latitude: float, longitude: float, radius_nm: float) -> str:
        if not 0 < radius_nm <= self.max_radius_nm:
            raise ValueError(f"radius must be within (0, {self.max_radius_nm}] NM")
        return f"{BASE_URL}/lat/{latitude:.6f}/lon/{longitude:.6f}/dist/{radius_nm:g}"

    def parse(self, body: bytes, received_at: datetime) -> ParsedResponse:
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SchemaError(f"invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise SchemaError("top level is not an object")
        aircraft = data.get("ac")
        if not isinstance(aircraft, list):
            raise SchemaError("missing or non-list 'ac'")
        now_ms = data.get("now")
        if not isinstance(now_ms, (int, float)) or isinstance(now_ms, bool):
            raise SchemaError("missing or non-numeric 'now'")

        # 'now' is epoch milliseconds in v3 (seconds in readsb's local aircraft.json)
        provider_at = datetime.fromtimestamp(now_ms / 1000, UTC)
        parsed = ParsedResponse(provider_at=provider_at)
        for raw in aircraft:
            try:
                record = _Aircraft.model_validate(raw)
            except ValidationError as exc:
                fields = ",".join(".".join(map(str, e["loc"])) or "record" for e in exc.errors())
                parsed.rejections.append(Rejection(reason=f"invalid: {fields}", raw=raw))
                continue
            parsed.observations.append(self._normalize(record, provider_at, received_at))
        return parsed

    def _normalize(self, a: _Aircraft, provider_at: datetime, received_at: datetime) -> Observation:
        time_flag = None
        if a.seen_pos is None:
            position_at, basis = None, PositionTimeBasis.UNKNOWN
        else:
            position_at = provider_at - timedelta(seconds=a.seen_pos)
            basis = PositionTimeBasis.DERIVED_AGE
            if a.seen_pos < -FUTURE_TOLERANCE_S:
                time_flag = "future_position"

        if a.alt_baro == "ground":
            on_ground, alt_baro = True, None
        elif a.alt_baro is None:
            on_ground, alt_baro = None, None
        else:
            on_ground, alt_baro = False, round(a.alt_baro)

        vertical_rate = a.baro_rate if a.baro_rate is not None else a.geom_rate

        return Observation(
            provider=self.name,
            aircraft_id=a.hex.strip().lower(),
            received_at=received_at,
            provider_at=provider_at,
            position_at=position_at,
            position_time_basis=basis,
            time_flag=time_flag,
            latitude=a.lat,
            longitude=a.lon,
            altitude_baro_ft=alt_baro,
            altitude_geom_ft=_round(a.alt_geom),
            on_ground=on_ground,
            ground_speed_kt=a.gs,
            track_deg=None if a.track is None else a.track % 360,
            vertical_rate_fpm=_round(vertical_rate),
            callsign=_blank_to_none(a.flight),
            registration=_blank_to_none(a.r),
            aircraft_type=_blank_to_none(a.t),
            aircraft_description=_blank_to_none(a.desc),
            category=_blank_to_none(a.category),
            position_source=_blank_to_none(a.type),
            owner_operator=_blank_to_none(a.ownOp),
            squawk=_blank_to_none(a.squawk),
            emergency=_blank_to_none(a.emergency),
        )
