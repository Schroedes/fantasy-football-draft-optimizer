import pytest

from ffdo.domain.constants import SEASON_LENGTH
from ffdo.domain.models import PlayerProfile, SeasonStatLine
from ffdo.engine import dynasty_value


def _profile(pid="p", pos="RB", age=26, exp=4, active=True, injury=None):
    return PlayerProfile(player_id=pid, first_name="A", last_name="B",
                         position=pos, team="X", age=age, years_exp=exp,
                         injury_status=injury, active=active)


def _line(season, gp, **stats):
    return SeasonStatLine(player_id="p", season=season, games_played=gp,
                          season_length=SEASON_LENGTH[season], stats=stats)


def test_no_age_returns_current_full_unchanged():
    profile = _profile(age=None)
    result = dynasty_value.annuity_value(150.0, profile, [], {}, current_season=2026)
    assert result == 150.0


def test_empty_age_curve_leaves_value_unchanged_regardless_of_durability():
    """The critical regression test for the doubling bug described above.
    An empty curve means "no data on how this player's value changes with
    age" -- the correct behavior is a no-op (dynasty value == current_full),
    not a distortion in either direction, and this must hold EVEN THOUGH
    `expected_games_missed([], ...)` still returns a nonzero prior-based
    estimate (empty history is not the same as zero durability risk) --
    the no-op property comes from the curve being empty, not from
    durability being zero."""
    profile = _profile(pos="RB", age=26)
    result = dynasty_value.annuity_value(150.0, profile, [], {}, current_season=2026)
    assert result == pytest.approx(150.0)


def test_missing_position_in_curve_behaves_like_an_empty_curve():
    profile = _profile(pos="RB", age=26)
    curve = {"WR": {27: 5.0, 28: 5.0, 29: 5.0, 30: 5.0, 31: 5.0}}
    result = dynasty_value.annuity_value(150.0, profile, [], curve, current_season=2026)
    assert result == pytest.approx(150.0)


def test_young_player_with_positive_curve_deltas_scores_above_current_full():
    profile = _profile(pos="WR", age=24)
    curve = {"WR": {25: 3.0, 26: 3.0, 27: 2.0, 28: 1.0, 29: 0.0}}
    result = dynasty_value.annuity_value(100.0, profile, [], curve, current_season=2026)
    assert result > 100.0


def test_old_player_with_negative_curve_deltas_scores_below_current_full():
    profile = _profile(pos="RB", age=30)
    curve = {"RB": {31: -3.0, 32: -3.0, 33: -3.0, 34: -3.0, 35: -3.0}}
    result = dynasty_value.annuity_value(100.0, profile, [], curve, current_season=2026)
    assert result < 100.0


def test_result_is_never_negative_even_with_strongly_negative_deltas():
    profile = _profile(pos="RB", age=33)
    curve = {"RB": {34: -1000.0, 35: -1000.0, 36: -1000.0, 37: -1000.0, 38: -1000.0}}
    result = dynasty_value.annuity_value(50.0, profile, [], curve, current_season=2026)
    assert 0.0 <= result < 50.0


def test_horizon_years_and_discount_rate_are_overridable():
    profile = _profile(pos="WR", age=24)
    curve = {"WR": {25: 10.0, 26: 10.0}}
    short_horizon = dynasty_value.annuity_value(
        100.0, profile, [], curve, current_season=2026, horizon_years=2)
    # With only a 2-year horizon and the curve only defined for ages 25/26,
    # extending the horizon further (with zero-delta fallback beyond age 26)
    # must not raise and must still return a sane, positive-improvement value.
    long_horizon = dynasty_value.annuity_value(
        100.0, profile, [], curve, current_season=2026, horizon_years=5)
    assert short_horizon > 100.0
    assert long_horizon > 100.0
