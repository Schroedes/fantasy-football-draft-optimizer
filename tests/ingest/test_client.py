# tests/ingest/test_client.py
import httpx
import pytest

from ffdo.ingest.client import SleeperClient


def test_non_retryable_status_raises_immediately_after_one_request():
    """A permanent error (404) must fail fast, not burn all retry attempts."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    client = SleeperClient(base_delay=0, transport=transport)

    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("https://api.sleeper.app/v1/players/nfl/does-not-exist")

    assert len(calls) == 1


def test_retryable_status_then_success_makes_multiple_requests(monkeypatch):
    """429/5xx must retry and eventually return the successful response."""
    monkeypatch.setattr("ffdo.ingest.client.time.sleep", lambda *_args, **_kw: None)

    calls = []
    responses = [
        httpx.Response(429),
        httpx.Response(503),
        httpx.Response(200, json={"ok": True}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses[len(calls) - 1]

    transport = httpx.MockTransport(handler)
    client = SleeperClient(base_delay=0, transport=transport)

    result = client.get_json("https://api.sleeper.app/v1/players/nfl")

    assert result == {"ok": True}
    assert len(calls) > 1
    assert len(calls) == 3
