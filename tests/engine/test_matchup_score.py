import pytest

from ffdo.domain.models import PlayerProfile, WeeklyProjection
from ffdo.engine import matchup_score


def _profile(pid, team):
    return PlayerProfile(player_id=pid, first_name="F", last_name=pid, position="RB",
                         team=team, age=25, years_exp=3, injury_status=None, active=True)


def _proj(pid, stats):
    return WeeklyProjection(player_id=pid, season=2026, week=1, stats=stats)


_SCORING = {"rush_yd": 0.1, "rush_td": 6.0, "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0}


def test_uses_live_points_for_a_starter_whose_game_has_locked():
    profiles = {"p1": _profile("p1", "AAA")}
    weekly_points = {"p1": _proj("p1", {"rush_yd": 999.0})}  # would score huge if used -- must be ignored
    live_points = {"p1": 14.3}
    total = matchup_score.team_projected_score(
        ["p1"], weekly_points=weekly_points, live_points=live_points,
        profiles=profiles, locked_teams=frozenset({"AAA"}), scoring_settings=_SCORING)
    assert total == pytest.approx(14.3)


def test_uses_pregame_projection_for_a_starter_whose_game_hasnt_locked():
    profiles = {"p1": _profile("p1", "AAA")}
    weekly_points = {"p1": _proj("p1", {"rush_yd": 100.0, "rush_td": 1.0})}  # 10.0 + 6.0 = 16.0
    live_points = {"p1": 999.0}  # would score huge if used -- must be ignored
    total = matchup_score.team_projected_score(
        ["p1"], weekly_points=weekly_points, live_points=live_points,
        profiles=profiles, locked_teams=frozenset(), scoring_settings=_SCORING)
    assert total == pytest.approx(16.0)


def test_sums_multiple_starters_mixing_locked_and_not_locked():
    profiles = {"p1": _profile("p1", "AAA"), "p2": _profile("p2", "BBB")}
    weekly_points = {"p2": _proj("p2", {"rec": 5.0, "rec_yd": 40.0})}  # 5.0 + 4.0 = 9.0
    live_points = {"p1": 11.0}
    total = matchup_score.team_projected_score(
        ["p1", "p2"], weekly_points=weekly_points, live_points=live_points,
        profiles=profiles, locked_teams=frozenset({"AAA"}), scoring_settings=_SCORING)
    assert total == pytest.approx(20.0)


def test_missing_projection_and_missing_live_points_contribute_zero():
    profiles = {"p1": _profile("p1", "AAA")}
    total = matchup_score.team_projected_score(
        ["p1"], weekly_points={}, live_points={},
        profiles=profiles, locked_teams=frozenset({"AAA"}), scoring_settings=_SCORING)
    assert total == pytest.approx(0.0)


def test_unknown_player_id_contributes_zero_without_erroring():
    total = matchup_score.team_projected_score(
        ["ghost"], weekly_points={}, live_points={},
        profiles={}, locked_teams=frozenset(), scoring_settings=_SCORING)
    assert total == pytest.approx(0.0)
