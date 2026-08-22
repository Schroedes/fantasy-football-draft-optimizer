# tests/ingest/test_snapshot.py
from ffdo.ingest import snapshot


def test_loads_players_snapshot():
    players = snapshot.load("players_nfl")
    assert isinstance(players, dict)
    assert len(players) > 10_000
    assert players["9221"]["last_name"] == "Gibbs"


def test_loads_league_history_snapshot():
    hist = snapshot.load("league_history")
    assert hist["leagues"]["2026"]["league_id"] == "1315881559957458944"
    assert hist["drafts"]["2025"]["meta"]["type"] == "auction"
    assert len(hist["drafts"]["2025"]["picks"]) == 168


def test_loads_every_season_of_stats():
    for season in (2021, 2022, 2023, 2024, 2025):
        stats = snapshot.load(f"stats_{season}")
        assert len(stats) > 1_000
