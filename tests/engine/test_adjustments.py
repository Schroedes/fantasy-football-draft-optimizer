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
