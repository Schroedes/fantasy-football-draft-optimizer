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
    """A player with more raw points can still have lower VOR if their
    position's replacement level is much higher -- FLEX must compare VOR,
    not points, or it seats the wrong player. Points and VOR are made to
    diverge here specifically so a points-based implementation and a
    VOR-based one produce different, observable outcomes."""
    league = _league(["RB", "WR", "FLEX", "BN"])

    def _player(pid, pos, points, vor):
        prof = PlayerProfile(player_id=pid, first_name=pos, last_name=pid,
                             position=pos, team="X", age=25, years_exp=3,
                             injury_status=None, active=True)
        return ValuedPlayer(profile=prof, projected_points=points,
                            adjusted_points=points, vor=vor, tier=1,
                            adjustments={})

    team = {
        "rb1": _player("rb1", "RB", points=100.0, vor=30.0),  # dedicated RB slot
        "wr1": _player("wr1", "WR", points=90.0, vor=25.0),   # dedicated WR slot
        "rb2": _player("rb2", "RB", points=10.0, vor=9.0),    # low points, higher VOR -- should win FLEX
        "wr2": _player("wr2", "WR", points=50.0, vor=5.0),    # high points, lower VOR -- should be benched
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


def test_marginal_value_credits_full_vor_for_an_empty_slot():
    """A position with no starter yet has nothing to bump -- a candidate
    who fills that empty slot should be credited close to their full VOR,
    not discounted for a "need" that isn't actually competing with anyone."""
    league = _league(["RB", "WR", "BN"])
    team = {"rb1": _vp("rb1", "RB", 10.0)}
    candidates = {"wr1": _vp("wr1", "WR", 25.0)}

    result = roster.marginal_lineup_values(team, candidates, league)

    assert result["wr1"] == pytest.approx(25.0)


def test_marginal_value_is_near_zero_when_position_already_stacked():
    """The user's actual complaint: a 4th RB with high raw VOR that can't
    crack an already-full, already-better RB group nets nothing -- even
    though its VOR alone looks like a great pick."""
    league = _league(["RB", "RB", "BN", "BN"])
    team = {
        "rb1": _vp("rb1", "RB", 50.0),
        "rb2": _vp("rb2", "RB", 45.0),
        "rb3": _vp("rb3", "RB", 40.0),
    }
    candidates = {"rb4": _vp("rb4", "RB", 35.0)}

    result = roster.marginal_lineup_values(team, candidates, league)

    assert result["rb4"] == pytest.approx(0.0)


def test_marginal_value_credits_the_bump_delta_not_the_full_vor():
    """A candidate who beats the current weakest starter at a full position
    bumps that starter to the bench -- the marginal gain is the VOR
    difference between them, not the candidate's raw VOR."""
    league = _league(["RB", "BN"])
    team = {"rb1": _vp("rb1", "RB", 10.0)}
    candidates = {"rb2": _vp("rb2", "RB", 18.0)}

    result = roster.marginal_lineup_values(team, candidates, league)

    assert result["rb2"] == pytest.approx(8.0)
