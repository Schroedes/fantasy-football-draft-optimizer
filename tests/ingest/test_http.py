import httpx
import pytest

from ffdo.ingest.http import get_json_with_retry


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_returns_parsed_json_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    result = get_json_with_retry(_client(handler), "https://example.test/x",
                                 sleep=lambda *_a, **_kw: None)
    assert result == {"ok": True}


def test_passes_headers_through_to_the_request():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    get_json_with_retry(_client(handler), "https://example.test/x",
                        headers={"Cookie": "a=b"}, sleep=lambda *_a, **_kw: None)
    assert seen[0].headers["cookie"] == "a=b"


def test_retryable_status_then_success_makes_multiple_requests():
    responses = [httpx.Response(429), httpx.Response(503),
                httpx.Response(200, json={"ok": True})]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses[len(calls) - 1]

    result = get_json_with_retry(_client(handler), "https://example.test/x",
                                 sleep=lambda *_a, **_kw: None)
    assert result == {"ok": True}
    assert len(calls) == 3


def test_non_retryable_status_raises_immediately_after_one_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(httpx.HTTPStatusError):
        get_json_with_retry(_client(handler), "https://example.test/x",
                            sleep=lambda *_a, **_kw: None)
    assert len(calls) == 1
