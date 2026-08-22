import pytest

from ffdo.domain.constants import SEASON_LENGTH
from ffdo.domain.models import PlayerProfile, SeasonStatLine
from ffdo.engine import adjustments


def _line(season, gp, **stats):
    from ffdo.domain.constants import SEASON_LENGTH
    return SeasonStatLine(player_id="p", season=season, games_played=gp,
                          season_length=SEASON_LENGTH[season], stats=stats)


def _profile(pid="p", pos="RB", age=26):
    return PlayerProfile(player_id=pid, first_name="A", last_name="B",
                         position=pos, team="X", age=age, years_exp=4,
                         injury_status=None, active=True)


def test_weights_default_to_zero():
    """Unvalidated adjustments must not reach a live board."""
    assert adjustments.AGE_WEIGHT == 0.0
    assert adjustments.DURABILITY_WEIGHT == 0.0


def test_durable_player_has_lower_expected_games_missed():
    durable = [_line(2023, 17), _line(2024, 18), _line(2025, 18)]
    fragile = [_line(2023, 9), _line(2024, 11), _line(2025, 10)]
    assert (adjustments.expected_games_missed(durable, "RB")
            < adjustments.expected_games_missed(fragile, "RB"))


def test_estimate_shrinks_toward_prior_with_thin_history():
    """One bad season must not brand a player fragile forever."""
    one_bad = [_line(2025, 4)]
    many_bad = [_line(2023, 4), _line(2024, 5), _line(2025, 4)]
    assert (adjustments.expected_games_missed(one_bad, "RB")
            < adjustments.expected_games_missed(many_bad, "RB"))


def test_no_history_returns_the_positional_prior():
    got = adjustments.expected_games_missed([], "RB", prior_rate=0.15)
    assert got == 0.15 * 18


def test_availability_cost_uses_the_gap_to_replacement_not_raw_points():
    """A missed game costs the gap to a streamer, not the player's full output."""
    profiles = {"p": _profile()}
    history = {"p": [_line(2023, 9), _line(2024, 9), _line(2025, 9)]}
    built = adjustments.build(profiles, history, points={"p": 180.0},
                              replacement_ppg={"RB": 8.0},
                              durability_weight=1.0)
    cost = built["p"]["durability"]
    assert cost < 0
    # Player is 10 ppg; replacement is 8. Cost per missed game is 2, not 10.
    assert abs(cost) < 180.0 * 0.5


def test_build_returns_empty_adjustments_when_weights_are_zero():
    profiles = {"p": _profile()}
    history = {"p": [_line(2025, 4)]}
    built = adjustments.build(profiles, history, points={"p": 180.0},
                              replacement_ppg={"RB": 8.0})
    assert built["p"] == {}


def test_fit_age_curve_computes_training_age_from_the_fixed_profile_snapshot_anchor():
    """`profiles` is always the single 2026-anchored `players_nfl` snapshot --
    there is no per-season age history. A player who is 30 in that snapshot
    was 25 during the 2021 season (30 - (2026 - 2021)), so a training pair
    spanning 2021->2022 must be keyed at age 25, not some other value.
    """
    history = {"p": [
        _line(2021, 16, pts_half_ppr=160.0),   # 10 ppg, age 25 in 2021
        _line(2022, 16, pts_half_ppr=192.0),   # 12 ppg, age 26 in 2022
    ]}
    profiles = {"p": _profile(age=30)}
    curve = adjustments.fit_age_curve(history, profiles)
    assert curve["RB"] == {25: pytest.approx(2.0)}


def test_build_looks_up_the_age_curve_at_the_season_being_evaluated():
    """Regression for the train/apply mismatch: during a backtest for a past
    season, the age curve must be looked up using the player's age AT that
    past season -- not their raw (2026-anchored) profile age. A player who
    is 30 in the profile snapshot was 27 during the 2023 season
    (30 - (2026 - 2023)).
    """
    profiles = {"p": _profile(age=30)}
    history = {"p": []}

    # Curve only has an entry at the correct age-at-2023 (27).
    curve_at_correct_age = {"RB": {27: 5.0}}
    built = adjustments.build(
        profiles, history, points={"p": 180.0}, replacement_ppg={"RB": 8.0},
        age_weight=1.0, age_curve=curve_at_correct_age, current_season=2023)
    assert built["p"]["age"] == pytest.approx(1.0 * 5.0 * SEASON_LENGTH[2023])

    # A curve keyed by the raw, un-shifted profile age (30) must NOT hit --
    # proves the lookup shifts by season rather than always using prof.age.
    curve_at_raw_age = {"RB": {30: 5.0}}
    built_raw = adjustments.build(
        profiles, history, points={"p": 180.0}, replacement_ppg={"RB": 8.0},
        age_weight=1.0, age_curve=curve_at_raw_age, current_season=2023)
    assert "age" not in built_raw["p"]


def test_build_age_lookup_still_uses_raw_profile_age_for_live_2026_use():
    """Regression: current_season defaults to 2026, matching the profile
    snapshot's own anchor, so live (non-backtest) behavior is unchanged --
    the age-at-season shift is a no-op when current_season == 2026.
    """
    profiles = {"p": _profile(age=30)}
    history = {"p": []}
    curve = {"RB": {30: 5.0}}
    built = adjustments.build(
        profiles, history, points={"p": 180.0}, replacement_ppg={"RB": 8.0},
        age_weight=1.0, age_curve=curve)
    assert built["p"]["age"] == pytest.approx(1.0 * 5.0 * SEASON_LENGTH[2026])
