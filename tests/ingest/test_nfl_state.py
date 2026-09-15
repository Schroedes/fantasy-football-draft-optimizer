import httpx

from ffdo.ingest import nfl_state
from ffdo.ingest.client import SleeperClient


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def test_current_week_basic_fields():
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


def test_current_week_uses_week_not_lagging_display_week():
    """Real /state/nfl the Tuesday after week 1 ended: `week` has already
    ticked over to 2, but Sleeper's own `display_week` lags a day behind
    while stat corrections finalize. `week` -- not `display_week` -- is
    the field that flips exactly when the previous week is done, which is
    what `_through_week` in app.py relies on to know week 1 is bankable.
    Using `display_week` here made the whole app look frozen every Tuesday."""
    def handler(request):
        return httpx.Response(200, json={
            "season": "2026", "season_type": "regular",
            "week": 2, "display_week": 1, "leg": 2,
        })

    assert nfl_state.current_week(_client(handler)).week == 2


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
