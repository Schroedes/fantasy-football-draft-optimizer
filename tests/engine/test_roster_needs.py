# tests/engine/test_roster_needs.py
from dataclasses import replace

import pytest

from ffdo.domain.models import LeagueProfile, PlayerProfile, RosterEntry, ValuedPlayer
from ffdo.engine import roster_needs


def _league(roster_positions, num_teams=2):
    return LeagueProfile(league_id="x", season=2026, num_teams=num_teams,
                         roster_positions=roster_positions, scoring_settings={}, budget=200)


def _vp(pid, pos, v):
    prof = PlayerProfile(player_id=pid, first_name=pos, last_name=pid, position=pos,
                         team="X", age=26, years_exp=4, injury_status=None, active=True)
    return ValuedPlayer(profile=prof, projected_points=v, adjusted_points=v,
                        vor=v, tier=1, adjustments={})


def _entry(rid, players):
    return RosterEntry(roster_id=rid, team_name=f"T{rid}", player_ids=tuple(players),
                       starter_ids=(), wins=0, losses=0, ties=0,
                       points_for=0.0, points_against=0.0)


# ---- _weakness: pure rank normalization ----

def test_weakness_best_team_is_zero():
    assert roster_needs._weakness(1, 4) == 0.0


def test_weakness_worst_team_is_one():
    assert roster_needs._weakness(4, 4) == 1.0


def test_weakness_middle_rank_is_fractional():
    assert roster_needs._weakness(2, 4) == pytest.approx(1 / 3)


def test_weakness_single_team_league_is_zero():
    assert roster_needs._weakness(1, 1) == 0.0


# ---- _thinness: the part the user required a revision on ----

def test_thinness_def_never_penalized_for_meeting_starters_only():
    """DEF's POSITION_CAP_EXTRA is 0 -- this league expects zero bench
    depth at DEF, so having exactly enough to start (and nothing behind
    it) must never read as thin."""
    league = _league(("QB", "RB", "WR", "TE", "DEF", "K", "BN"))
    assert roster_needs._thinness("DEF", 1, league) == 0.0


def test_thinness_def_penalized_only_for_missing_a_starter():
    league = _league(("QB", "RB", "WR", "TE", "DEF", "K", "BN"))
    assert roster_needs._thinness("DEF", 0, league) == 1.0


def test_thinness_te_partial_cap_scales_between_starting_reach_and_cap():
    # TE: dedicated=1, flex_eligible=1 (TE reachable via FLEX) -> starting_reach=2.
    # POSITION_CAP_EXTRA["TE"]=1 -> cap=3.
    league = _league(("QB", "RB", "WR", "TE", "FLEX", "DEF", "K", "BN", "BN"))
    assert roster_needs._thinness("TE", 2, league) == 1.0   # exactly starting_reach, no cushion
    assert roster_needs._thinness("TE", 3, league) == 0.0   # at the cap, fully deep
    assert roster_needs._thinness("TE", 1, league) == 1.0   # below starting_reach, clamped at 1.0


def test_thinness_rb_uncapped_decays_smoothly_with_each_extra_player():
    # RB: dedicated=2, flex_eligible=1 (RB reachable via FLEX) -> starting_reach=3.
    # RB is not in POSITION_CAP_EXTRA -- uncapped, no fixed "ideal" bench count.
    league = _league(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "DEF", "K", "BN", "BN", "BN"))
    assert roster_needs._thinness("RB", 3, league) == 1.0              # extra=0
    assert roster_needs._thinness("RB", 4, league) == 0.5              # extra=1
    assert roster_needs._thinness("RB", 5, league) == pytest.approx(1 / 3)  # extra=2


# ---- _severity: bucket thresholds ----

def test_severity_buckets():
    assert roster_needs._severity(0.0) == "Fine"
    assert roster_needs._severity(0.32) == "Fine"
    assert roster_needs._severity(0.33) == "Moderate"
    assert roster_needs._severity(0.65) == "Moderate"
    assert roster_needs._severity(0.66) == "Severe"
    assert roster_needs._severity(1.0) == "Severe"


# ---- position_needs: the full, integration-shaped function ----

_LEAGUE = _league(("QB", "RB", "WR", "TE", "FLEX", "BN", "BN"), num_teams=2)
_VALUED = {
    "q1": _vp("q1", "QB", 30), "r1": _vp("r1", "RB", 25), "w1": _vp("w1", "WR", 20), "t1": _vp("t1", "TE", 10),
    "q2": _vp("q2", "QB", 15), "r2": _vp("r2", "RB", 12), "w2": _vp("w2", "WR", 8), "t2": _vp("t2", "TE", 5),
    "q3": _vp("q3", "QB", 5),
}
_ROSTERS = [
    _entry(1, ["q1", "r1", "w1", "t1"]),
    _entry(2, ["q2", "r2", "w2", "t2"]),
]


def test_position_needs_returns_rank_and_severity_for_all_four_positions():
    result = roster_needs.position_needs(_ROSTERS[0], _ROSTERS, _VALUED, _LEAGUE)
    assert set(result) == {"QB", "RB", "WR", "TE"}
    assert result["QB"].rank == 1   # team1's QB (30) beats team2's (15)
    for need in result.values():
        assert need.severity in ("Fine", "Moderate", "Severe")


def test_position_needs_scores_a_hypothetical_roster_with_other_teams_frozen():
    # Hypothetical: team1 trades away its strong QB (q1, 30) for a much
    # weaker one (q3, 5) -- team2's real, untouched QB (q2, 15) now beats it.
    hypothetical = replace(_ROSTERS[0], player_ids=("q3", "r1", "w1", "t1"))
    result = roster_needs.position_needs(hypothetical, _ROSTERS, _VALUED, _LEAGUE)
    assert result["QB"].rank == 2
    assert result["QB"].severity != "Fine"
    # A second, independent call using the ORIGINAL (unmodified) _ROSTERS
    # list must read team1's REAL QB value (q1, 30), not leftover state
    # from the hypothetical (q3, 5) used above -- team1's real 30 beats
    # team2's real 15, so team2's genuine rank is 2. If Call A's
    # substitution had somehow leaked into this fresh call, team2 would
    # wrongly show rank 1 instead.
    real_team2_result = roster_needs.position_needs(_ROSTERS[1], _ROSTERS, _VALUED, _LEAGUE)
    assert real_team2_result["QB"].rank == 2
