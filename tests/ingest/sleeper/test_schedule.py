import httpx

from ffdo.ingest.client import SleeperClient
from ffdo.ingest.sleeper import schedule

_RAW = [
    {"status": "complete", "date": "2026-11-12", "home": "AAA", "away": "BBB",
     "week": 10, "game_id": "1"},
    {"status": "pre_game", "date": "2026-11-15", "home": "CCC", "away": "DDD",
     "week": 10, "game_id": "2"},
    {"status": "pre_game", "date": "2026-11-08", "home": "EEE", "away": "FFF",
     "week": 9, "game_id": "3"},
]


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def test_week_games_filters_to_the_requested_week():
    def handler(request):
        return httpx.Response(200, json=_RAW)

    games = schedule.week_games(_client(handler), 2026, 10)
    assert len(games) == 2
    assert all(g["week"] == 10 for g in games)


def test_locked_teams_includes_home_and_away_of_a_non_pre_game_game():
    games = [g for g in _RAW if g["week"] == 10]
    locked = schedule.locked_teams(games)
    assert locked == frozenset({"AAA", "BBB"})


def test_locked_teams_excludes_a_pre_game_games_teams():
    games = [g for g in _RAW if g["week"] == 10]
    locked = schedule.locked_teams(games)
    assert "CCC" not in locked and "DDD" not in locked


def test_bye_teams_is_every_team_not_playing_that_week():
    games = [g for g in _RAW if g["week"] == 10]
    all_teams = frozenset({"AAA", "BBB", "CCC", "DDD", "GGG"})
    assert schedule.bye_teams(games, all_teams) == frozenset({"GGG"})


def test_week_locked_false_while_any_game_is_pre_game():
    games = [g for g in _RAW if g["week"] == 10]
    assert schedule.week_locked(games) is False


def test_week_locked_true_once_every_game_has_started():
    games = [{**g, "status": "complete"} for g in _RAW if g["week"] == 10]
    assert schedule.week_locked(games) is True


def test_week_locked_false_for_an_empty_games_list():
    assert schedule.week_locked([]) is False
