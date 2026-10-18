import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from airspace.api import create_app
from airspace.collector import Clock


class CountingTransport(httpx.MockTransport):
    def __init__(self, response):
        self.calls = 0

        def handler(request):
            self.calls += 1
            return response()

        super().__init__(handler)


def fast_clock():
    # Real time, but wait in short steps so shutdown is quick
    return Clock(sleep=lambda s: asyncio.sleep(min(s, 0.01)))


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met")


@pytest.fixture
def ok_transport(fixture_bytes):
    return CountingTransport(lambda: httpx.Response(200, content=fixture_bytes))


def test_live_endpoints(config, ok_transport):
    app = create_app(config, transport=ok_transport, clock=fast_clock())
    with TestClient(app) as client:
        wait_for(lambda: client.get("/api/state").json()["sequence"] >= 1)
        state = client.get("/api/state").json()
        assert state["version"] == 1 and state["mode"] == "live"
        assert state["feed"]["status"] == "live"
        assert state["source"]["name"] == "adsb.fi"
        assert len(state["aircraft"]) == 54
        distances = [a["distance_m"] for a in state["aircraft"]]
        assert distances == sorted(distances)
        heli = next(a for a in state["aircraft"] if a["id"] == "a1f48f")
        assert heli["class"] == "rotorcraft" and heli["registration"] == "N225LA"
        assert "owner_operator" not in heli  # detail panel only

        detail = client.get("/api/aircraft/A1F48F").json()
        assert detail["owner_operator"] == "REDACTED OWNER"
        assert client.get("/api/aircraft/zzzzzz").status_code == 404

        health = client.get("/api/health").json()
        assert health["collector_running"] and health["aircraft_shown"] == 54
        assert set(health["process"]) == {"footprint_mb", "rss_mb"}

        sessions = client.get("/api/sessions").json()
        assert sessions[-1]["mode"] == "serve" and sessions[-1]["id"] == state["session_id"]

        replay = client.get("/api/replay", params={"session_id": state["session_id"], "at": state["generated_at"]})
        assert replay.status_code == 200 and replay.json()["mode"] == "replay"
        assert client.get("/api/state").json()["mode"] == "live"  # replay left live state alone

        assert client.get("/api/replay", params={"session_id": 999, "at": state["generated_at"]}).status_code == 404
        assert client.get("/api/replay", params={"session_id": 1, "at": "yesterday"}).status_code == 422
        unencoded = state["generated_at"].replace("+", " ")
        assert client.get(f"/api/replay?session_id={state['session_id']}&at={unencoded}").status_code == 200


def test_browser_polling_does_not_call_provider(config, ok_transport):
    app = create_app(config, transport=ok_transport, clock=fast_clock())
    with TestClient(app) as client:
        wait_for(lambda: ok_transport.calls >= 1)
        before = ok_transport.calls
        for _ in range(25):
            client.get("/api/state")
            client.get("/api/health")
        assert ok_transport.calls == before  # next scheduled poll is 10s away


def test_paused_feed_is_visible(config):
    transport = CountingTransport(lambda: httpx.Response(403))
    app = create_app(config, transport=transport, clock=fast_clock())
    with TestClient(app) as client:
        wait_for(lambda: client.get("/api/state").json()["feed"]["status"] == "paused")
        state = client.get("/api/state").json()
        assert "HTTP 403" in state["feed"]["paused"]
        assert state["aircraft"] == []
        assert transport.calls == 1
    assert transport.calls == 1


def test_replay_mode_makes_no_requests(config, ok_transport):
    # Record a session first
    with TestClient(create_app(config, transport=ok_transport, clock=fast_clock())) as client:
        wait_for(lambda: client.get("/api/state").json()["sequence"] >= 1)
        recorded = client.get("/api/state").json()
    calls = ok_transport.calls

    with TestClient(create_app(config, mode="replay", transport=ok_transport)) as client:
        assert client.get("/api/state").status_code == 409
        assert client.get("/api/health").status_code == 409
        snap = client.get(
            "/api/replay", params={"session_id": recorded["session_id"], "at": recorded["generated_at"]}
        ).json()
        assert {a["id"] for a in snap["aircraft"]} == {a["id"] for a in recorded["aircraft"]}
        assert snap["replay"]["session_id"] == recorded["session_id"]
    assert ok_transport.calls == calls


def test_session_closed_on_shutdown(config, ok_transport):
    with TestClient(create_app(config, transport=ok_transport, clock=fast_clock())) as client:
        wait_for(lambda: ok_transport.calls >= 1)
    with TestClient(create_app(config, mode="replay")) as client:
        (session,) = client.get("/api/sessions").json()
        assert session["end_reason"] == "stopped" and session["ended_at"]
