import httpx
import pytest

from ffdo.ingest.espn.client import EspnClient


def test_sends_cookie_and_user_agent_headers():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = EspnClient("s2value", "{00000001-0000-0000-0000-000000000000}",
                        base_delay=0, transport=httpx.MockTransport(handler))
    result = client.get_json("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/x")

    assert result == {"ok": True}
    assert len(seen) == 1
    cookie = seen[0].headers["cookie"]
    assert "espn_s2=s2value" in cookie
    assert "SWID={00000001-0000-0000-0000-000000000000}" in cookie
    assert "Mozilla" in seen[0].headers["user-agent"]


def test_retryable_status_then_success_makes_multiple_requests(monkeypatch):
    monkeypatch.setattr("ffdo.ingest.espn.client.time.sleep", lambda *_a, **_kw: None)
    responses = [httpx.Response(503), httpx.Response(200, json={"ok": True})]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses[len(calls) - 1]

    client = EspnClient("s2", "{00000001-0000-0000-0000-000000000000}",
                        base_delay=0, transport=httpx.MockTransport(handler))
    result = client.get_json("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/x")
    assert result == {"ok": True}
    assert len(calls) == 2


def test_non_retryable_status_raises_immediately():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client = EspnClient("bad", "{00000000-0000-0000-0000-000000000000}",
                        base_delay=0, transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/x")
