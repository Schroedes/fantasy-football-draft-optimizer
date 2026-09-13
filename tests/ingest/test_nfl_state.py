import httpx

from ffdo.ingest import nfl_state
from ffdo.ingest.client import SleeperClient


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def test_current_week_from_display_week():
    def handler(request):
        assert request.url.path == "/v1/state/nfl"
        return httpx.Response(200, json={
            "season": "2026", "season_type": "regular",
            "week": 10, "display_week": 10, "leg": 10,
        })

    w = nfl_state.current_week(_client(handler))
    assert w.season == 2026
    assert w.week == 10
    assert w.season_type == "regular"
    assert w.complete is False


def test_current_week_falls_back_to_week_when_no_display_week():
    def handler(request):
        return httpx.Response(200, json={"season": "2026", "season_type": "regular", "week": 3})

    assert nfl_state.current_week(_client(handler)).week == 3


def test_postseason_is_complete():
    def handler(request):
        return httpx.Response(200, json={
            "season": "2026", "season_type": "post", "week": 19, "display_week": 19})

    w = nfl_state.current_week(_client(handler))
    assert w.season_type == "post"
    assert w.complete is True


def test_week_past_18_is_complete_even_if_labeled_regular():
    def handler(request):
        return httpx.Response(200, json={
            "season": "2026", "season_type": "regular", "week": 19, "display_week": 19})

    assert nfl_state.current_week(_client(handler)).complete is True
