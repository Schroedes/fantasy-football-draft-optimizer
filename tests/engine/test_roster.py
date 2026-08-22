import pytest

from ffdo.domain.models import LeagueProfile, PlayerProfile, ValuedPlayer
from ffdo.engine import roster


def _league(roster_positions, num_teams=12):
    return LeagueProfile(league_id="x", season=2026, num_teams=num_teams,
                         roster_positions=tuple(roster_positions),
                         scoring_settings={}, budget=200)


def _vp(pid, pos, vor):
    prof = PlayerProfile(player_id=pid, first_name=pos, last_name=pid,
                         position=pos, team="X", age=25, years_exp=3,
                         injury_status=None, active=True)
    return ValuedPlayer(profile=prof, projected_points=vor, adjusted_points=vor,
                        vor=vor, tier=1, adjustments={})


def test_empty_roster_returns_all_zero():
    league = _league(["QB", "RB", "WR", "BN"])
    result = roster.team_lineup({}, league)
    assert result.starting_vor == 0.0
    assert result.bench_vor == 0.0
    assert result.by_position == {}
    assert result.starters == frozenset()


def test_by_position_always_sums_to_starting_vor():
    league = _league(["QB", "RB", "RB", "WR", "WR", "FLEX", "BN", "BN"])
    team = {
        "qb1": _vp("qb1", "QB", 20.0),
        "rb1": _vp("rb1", "RB", 15.0),
        "rb2": _vp("rb2", "RB", 10.0),
        "rb3": _vp("rb3", "RB", 5.0),
        "wr1": _vp("wr1", "WR", 12.0),
        "wr2": _vp("wr2", "WR", 8.0),
    }
    result = roster.team_lineup(team, league)
    assert sum(result.by_position.values()) == pytest.approx(result.starting_vor)


def test_flex_slot_is_filled_by_vor_not_raw_points():
    """A player with the fewest points can still have the highest VOR if
    their position's replacement level is much lower -- FLEX must compare
    VOR, not points, or it seats the wrong player."""
    league = _league(["RB", "WR", "FLEX", "BN"])
    team = {
        "rb1": _vp("rb1", "RB", 30.0),   # dedicated RB slot
        "wr1": _vp("wr1", "WR", 25.0),   # dedicated WR slot
        "rb2": _vp("rb2", "RB", 22.0),   # higher VOR, should win FLEX
        "wr2": _vp("wr2", "WR", 18.0),   # lower VOR, should be benched
    }
    result = roster.team_lineup(team, league)
    assert "rb2" in result.starters
    assert "wr2" not in result.starters


def test_bench_never_double_counts_a_starter():
    league = _league(["RB", "BN"])
    team = {"rb1": _vp("rb1", "RB", 10.0), "rb2": _vp("rb2", "RB", 4.0)}
    result = roster.team_lineup(team, league)
    assert result.starters == frozenset({"rb1"})
    assert result.starting_vor == pytest.approx(10.0)
    assert result.bench_vor == pytest.approx(4.0)


def test_unfilled_slots_contribute_zero_not_a_penalty():
    league = _league(["QB", "RB", "RB", "WR", "WR", "TE", "BN"])
    team = {"qb1": _vp("qb1", "QB", 20.0)}
    result = roster.team_lineup(team, league)
    assert result.starting_vor == pytest.approx(20.0)
    assert result.by_position == pytest.approx({"QB": 20.0})
