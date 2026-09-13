import httpx

from ffdo.ingest.client import PROJECTIONS, SleeperClient
from ffdo.ingest.sleeper import weekly_projections


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def test_fetch_hits_the_week_scoped_url_with_position_filters():
    seen_urls = []

    def handler(request):
        seen_urls.append(str(request.url))
        return httpx.Response(200, json=[])

    weekly_projections.fetch(_client(handler), 2026, 10)
    assert len(seen_urls) == 1
    url = seen_urls[0]
    assert url.startswith(f"{PROJECTIONS}/2026/10?")
    for pos in ("QB", "RB", "WR", "TE", "DEF", "K"):
        assert f"position%5B%5D={pos}" in url or f"position[]={pos}" in url


def test_fetch_parses_stats_into_weekly_projection():
    def handler(request):
        return httpx.Response(200, json=[
            {"player_id": "p1", "stats": {"pass_yd": 260.0, "pass_td": 2.0}},
        ])

    out = weekly_projections.fetch(_client(handler), 2026, 10)
    assert out["p1"].player_id == "p1"
    assert out["p1"].season == 2026
    assert out["p1"].week == 10
    assert out["p1"].stats == {"pass_yd": 260.0, "pass_td": 2.0}


def test_fetch_drops_a_row_with_no_player_id():
    def handler(request):
        return httpx.Response(200, json=[{"stats": {"pass_yd": 260.0}}])

    assert weekly_projections.fetch(_client(handler), 2026, 10) == {}


def test_fetch_drops_a_row_with_only_gp_and_no_real_projection():
    def handler(request):
        return httpx.Response(200, json=[
            {"player_id": "p1", "stats": {"gp": 1.0}},
        ])

    assert weekly_projections.fetch(_client(handler), 2026, 10) == {}


def test_fetch_ignores_boolean_stat_values():
    def handler(request):
        return httpx.Response(200, json=[
            {"player_id": "p1", "stats": {"pass_yd": 260.0, "some_flag": True}},
        ])

    out = weekly_projections.fetch(_client(handler), 2026, 10)
    assert "some_flag" not in out["p1"].stats
