# tests/ingest/test_adapters.py
from ffdo.ingest import league, players, snapshot, stats


def test_players_parse_extracts_profile_fields():
    parsed = players.parse(snapshot.load("players_nfl"))
    gibbs = parsed["9221"]
    assert gibbs.full_name == "Jahmyr Gibbs"
    assert gibbs.position == "RB"
    assert gibbs.team == "DET"
    assert gibbs.age == 24
    assert gibbs.active is True


def test_players_parse_tolerates_missing_age():
    raw = {"1": {"first_name": "A", "last_name": "B", "position": "WR",
                 "team": None, "age": None, "years_exp": None,
                 "injury_status": None, "active": False}}
    assert players.parse(raw)["1"].age is None


def test_stats_parse_sets_season_length_from_table():
    parsed = stats.parse(snapshot.load("stats_2023"), 2023)
    assert all(s.season_length == 17 for s in parsed.values())
    parsed_2025 = stats.parse(snapshot.load("stats_2025"), 2025)
    assert all(s.season_length == 18 for s in parsed_2025.values())


def test_stats_parse_keeps_component_stats_and_games_played():
    parsed = stats.parse(snapshot.load("stats_2025"), 2025)
    gibbs = parsed["9221"]
    assert gibbs.games_played > 0
    assert "rush_yd" in gibbs.stats


def test_stats_parse_excludes_boolean_values_instead_of_coercing_them():
    """bool is a subclass of int in Python; a JSON boolean stat value must be
    dropped, not silently coerced to 1.0/0.0. This endpoint is undocumented,
    so a boolean showing up is plausible even though none of the five
    committed snapshot seasons currently contain one."""
    raw = {"1": {"gp": 10, "rush_yd": 120.0, "some_flag": True, "other_flag": False}}
    parsed = stats.parse(raw, 2025)
    assert "some_flag" not in parsed["1"].stats
    assert "other_flag" not in parsed["1"].stats
    assert parsed["1"].stats["rush_yd"] == 120.0
    assert parsed["1"].stats["gp"] == 10.0


def test_league_parse_reads_roster_and_scoring():
    raw = snapshot.load("league_history")["leagues"]["2026"]
    lg = league.parse(raw)
    assert lg.num_teams == 12
    assert lg.starting_slots == ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX")
    assert lg.roster_size == 13
    assert lg.scoring_settings["rec"] == 0.5
    # This league penalises ALL fumbles, not just lost ones. It is the single
    # scoring rule that diverges from Sleeper's half-PPR preset.
    assert lg.scoring_settings["fum"] == -1
