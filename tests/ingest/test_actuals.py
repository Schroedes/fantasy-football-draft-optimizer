import httpx

from ffdo.ingest import actuals
from ffdo.ingest.client import SleeperClient


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def test_sums_players_points_across_weeks():
    weeks = {
        1: [{"roster_id": 1, "players_points": {"100": 20.5, "200": 8.0}},
            {"roster_id": 2, "players_points": {"300": 15.0}}],
        2: [{"roster_id": 1, "players_points": {"100": 12.0}},
            {"roster_id": 2, "players_points": {"300": 9.5, "400": 4.0}}],
    }

    def handler(request):
        w = int(str(request.url).rsplit("/", 1)[-1])
        return httpx.Response(200, json=weeks.get(w, []))

    out = actuals.points_so_far(_client(handler), "L1", 2)
    assert out["100"] == 32.5      # 20.5 + 12.0
    assert out["200"] == 8.0       # only week 1
    assert out["300"] == 24.5
    assert out["400"] == 4.0


def test_empty_or_missing_week_contributes_nothing():
    def handler(request):
        w = int(str(request.url).rsplit("/", 1)[-1])
        if w == 1:
            return httpx.Response(200, json=[{"roster_id": 1, "players_points": {"100": 10.0}}])
        return httpx.Response(200, json=[])       # week 2 not played yet

    out = actuals.points_so_far(_client(handler), "L1", 2)
    assert out == {"100": 10.0}


def test_through_week_zero_returns_empty_and_makes_no_calls():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=[])

    assert actuals.points_so_far(_client(handler), "L1", 0) == {}
    assert calls == []


def test_404_week_is_skipped_but_earlier_weeks_still_counted():
    """A 404 on a future week should not raise; earlier weeks' data
    should still be summed normally (satisfies brief: 404 contributes nothing
    and does not raise)."""
    def handler(request):
        w = int(str(request.url).rsplit("/", 1)[-1])
        if w == 1:
            return httpx.Response(200, json=[{"roster_id": 1, "players_points": {"100": 15.5}}])
        elif w == 2:
            # Simulate a future week Sleeper hasn't opened data for yet
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json=[])

    out = actuals.points_so_far(_client(handler), "L1", 2)
    assert out == {"100": 15.5}  # Only week 1's points, week 2 404'd and contributed nothing
