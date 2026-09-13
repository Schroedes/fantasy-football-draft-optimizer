import httpx

from ffdo.domain.models import SeasonStatLine
from ffdo.ingest.client import V1, SleeperClient
from ffdo.ingest.sleeper import player_history


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def test_fetch_season_hits_the_right_url_and_parses():
    def handler(request):
        assert str(request.url) == f"{V1}/stats/nfl/regular/2023"
        return httpx.Response(200, json={"p1": {"gp": 16, "rush_yd": 1000.0}})

    out = player_history.fetch_season(_client(handler), 2023)
    assert out["p1"].season == 2023
    assert out["p1"].games_played == 16
    assert out["p1"].stats["rush_yd"] == 1000.0


def test_history_for_filters_to_requested_players_across_seasons():
    season_stats = {
        2022: {
            "p1": SeasonStatLine(player_id="p1", season=2022, games_played=16,
                                 season_length=17, stats={}),
            "p2": SeasonStatLine(player_id="p2", season=2022, games_played=10,
                                 season_length=17, stats={}),
        },
        2023: {
            "p1": SeasonStatLine(player_id="p1", season=2023, games_played=17,
                                 season_length=17, stats={}),
        },
    }
    out = player_history.history_for(["p1"], season_stats)
    assert set(out) == {"p1"}
    assert {line.season for line in out["p1"]} == {2022, 2023}


def test_history_for_excludes_players_not_requested():
    season_stats = {
        2022: {"p2": SeasonStatLine(player_id="p2", season=2022, games_played=10,
                                    season_length=17, stats={})},
    }
    out = player_history.history_for(["p1"], season_stats)
    assert "p2" not in out


def test_history_for_returns_an_empty_list_for_a_player_with_no_seasons():
    out = player_history.history_for(["ghost"], {2022: {}})
    assert out == {"ghost": []}
