"""Provider interface and the HTTP handling every adapter shares."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx

from airspace.models import ErrorKind, ParsedResponse, PollResult, SchemaError, utcnow


class Provider(Protocol):
    name: str
    max_radius_nm: float

    def url(self, latitude: float, longitude: float, radius_nm: float) -> str: ...

    def parse(self, body: bytes, received_at: datetime) -> ParsedResponse: ...


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Retry-After is either delay seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or utcnow())).total_seconds())


async def fetch(
    provider: Provider,
    client: httpx.AsyncClient,
    latitude: float,
    longitude: float,
    radius_nm: float,
) -> PollResult:
    """Make one geographic request and classify the outcome. Never raises for HTTP or parse failures."""
    requested_at = utcnow()
    started = time.perf_counter()
    result = PollResult(provider=provider.name, requested_at=requested_at, received_at=None, latency_ms=None)
    try:
        response = await client.get(provider.url(latitude, longitude, radius_nm))
    except httpx.TimeoutException as exc:
        result.error_kind, result.error = ErrorKind.TRANSIENT, f"timeout: {type(exc).__name__}"
        return result
    except httpx.TransportError as exc:
        result.error_kind, result.error = ErrorKind.TRANSIENT, f"transport: {type(exc).__name__}: {exc}"
        return result

    result.received_at = utcnow()
    result.latency_ms = (time.perf_counter() - started) * 1000
    result.status_code = response.status_code
    result.raw_body = response.content

    status = response.status_code
    if status == 429:
        result.error_kind, result.error = ErrorKind.RATE_LIMITED, "HTTP 429"
        result.retry_after_s = parse_retry_after(response.headers.get("retry-after"), result.received_at)
        return result
    if status >= 500:
        result.error_kind, result.error = ErrorKind.TRANSIENT, f"HTTP {status}"
        return result
    if status >= 400:
        result.error_kind, result.error = ErrorKind.FATAL, f"HTTP {status}"
        return result
    if status != 200:
        result.error_kind, result.error = ErrorKind.SCHEMA, f"unexpected HTTP {status}"
        return result

    try:
        parsed = provider.parse(response.content, result.received_at)
    except SchemaError as exc:
        result.error_kind, result.error = ErrorKind.SCHEMA, str(exc)
        return result
    result.provider_at = parsed.provider_at
    result.observations = parsed.observations
    result.rejections = parsed.rejections
    return result
