import pytest

from ffdo.domain.models import DraftPickAsset, PlayerProfile, ValuedPlayer
from ffdo.engine import trade_value


def _profile(pid, pos="RB"):
    return PlayerProfile(player_id=pid, first_name="A", last_name="B",
                         position=pos, team="X", age=25, years_exp=3,
                         injury_status=None, active=True)


def _valued(pid, vor):
    return ValuedPlayer(profile=_profile(pid), projected_points=vor + 50.0,
                        adjusted_points=vor + 50.0, vor=vor, tier=1, adjustments={})


def _pick(season=2026, round=1, slot=1):
    return DraftPickAsset(season=season, round=round, projected_slot=slot,
                          current_owner_roster_id=1, original_roster_id=1,
                          via_team_name=None)


def test_side_values_sum_players_and_picks():
    valued = {"p1": _valued("p1", 40.0), "p2": _valued("p2", 10.0)}
    curve = {1: {"exact": {1: 60.0}}}
    side_a = {"player_ids": ["p1"], "picks": []}
    side_b = {"player_ids": ["p2"], "picks": [_pick()]}
    result = trade_value.evaluate_trade(
        side_a, side_b, valued_players=valued, pick_curve=curve,
        current_season=2026, round_size=12)
    assert result.side_a_value == pytest.approx(40.0)
    assert result.side_b_value == pytest.approx(70.0)   # 10.0 + 60.0
    assert result.differential == pytest.approx(-30.0)


def test_differential_pct_is_none_when_a_side_is_zero():
    valued = {"p1": _valued("p1", 0.0), "p2": _valued("p2", 10.0)}
    side_a = {"player_ids": ["p1"], "picks": []}
    side_b = {"player_ids": ["p2"], "picks": []}
    result = trade_value.evaluate_trade(
        side_a, side_b, valued_players=valued, pick_curve={},
        current_season=2026, round_size=12)
    assert result.differential_pct is None


def test_differential_pct_computed_against_the_smaller_side():
    valued = {"p1": _valued("p1", 100.0), "p2": _valued("p2", 50.0)}
    side_a = {"player_ids": ["p1"], "picks": []}
    side_b = {"player_ids": ["p2"], "picks": []}
    result = trade_value.evaluate_trade(
        side_a, side_b, valued_players=valued, pick_curve={},
        current_season=2026, round_size=12)
    assert result.differential_pct == pytest.approx(1.0)   # 50 / 50


def test_unknown_player_id_is_silently_skipped():
    valued = {"p1": _valued("p1", 40.0)}
    side_a = {"player_ids": ["p1", "ghost"], "picks": []}
    side_b = {"player_ids": [], "picks": []}
    result = trade_value.evaluate_trade(
        side_a, side_b, valued_players=valued, pick_curve={},
        current_season=2026, round_size=12)
    assert result.side_a_value == pytest.approx(40.0)


def test_redraft_trade_with_no_picks_works():
    valued = {"p1": _valued("p1", 20.0), "p2": _valued("p2", 20.0)}
    side_a = {"player_ids": ["p1"], "picks": []}
    side_b = {"player_ids": ["p2"], "picks": []}
    result = trade_value.evaluate_trade(
        side_a, side_b, valued_players=valued, pick_curve={},
        current_season=2026, round_size=12)
    assert result.differential == pytest.approx(0.0)
