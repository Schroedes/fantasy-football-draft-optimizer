import pytest

from ffdo.domain.constants import FAAB_BID_CURVE
from ffdo.domain.models import RosterEntry
from ffdo.engine import waiver_value


def test_exact_bucket_used_when_present():
    curve = {0: 0.05, 10: 0.15, 20: 0.30}
    result = waiver_value.suggested_bid(22.0, 100.0, curve)
    assert result == pytest.approx(30.0)  # bucket 20 -> 0.30 * 100


def test_falls_back_to_nearest_bucket_below_when_exact_bucket_missing():
    curve = {0: 0.05, 20: 0.30}
    result = waiver_value.suggested_bid(15.0, 100.0, curve)
    # bucket for 15.0 is 10 (missing) -- falls to bucket 0, not up to 20
    assert result == pytest.approx(5.0)


def test_never_extrapolates_upward_to_a_higher_bucket():
    curve = {0: 0.05}
    result = waiver_value.suggested_bid(999.0, 100.0, curve)
    # only bucket 0 exists and is <= bucket(999) -- uses it, does not
    # invent a value for an unseen high-VOR bucket
    assert result == pytest.approx(5.0)


def test_returns_zero_when_no_bucket_at_or_below_exists():
    curve = {50: 0.40}
    result = waiver_value.suggested_bid(10.0, 100.0, curve)
    assert result == 0.0


def test_empty_curve_returns_zero():
    result = waiver_value.suggested_bid(50.0, 100.0, {})
    assert result == 0.0


def test_custom_bucket_width_is_respected():
    curve = {0: 0.10, 5: 0.50}
    result = waiver_value.suggested_bid(7.0, 100.0, curve, bucket_width=5)
    assert result == pytest.approx(50.0)


def test_bucket_floor_handles_negative_fractional_vor_gain_correctly():
    """Regression: int(vor_gain) // bucket_width truncates toward zero for
    negative inputs (int(-100.5) == -100), which is NOT the same as
    floor(-100.5 / 10) == -11 -- the old buggy formula put -100.5 in
    bucket -100 (one bucket_width too HIGH) instead of the correct -110.
    Since real VOR values are essentially never exact integers, this bug
    affected the majority of real negative observations during fitting."""
    curve = {-110: 0.05, -100: 0.20}
    result = waiver_value.suggested_bid(-100.5, 100.0, curve)
    # Correct bucket for -100.5 is floor(-100.5/10)*10 = -110, not -100
    assert result == pytest.approx(5.0)


def test_real_curve_higher_vor_gain_bucket_never_suggests_less_than_the_lowest_bucket():
    """Real-magnitude check against the actual fitted curve (Global
    Constraints: the curve-fitting-derived module needs at least one test
    against real data, not just synthetic fixtures -- the exact regression
    class that let bugs into #4's and #5's shipped formulas)."""
    if len(FAAB_BID_CURVE) < 2:
        pytest.skip("real curve has fewer than 2 populated buckets")
    lowest_bucket = min(FAAB_BID_CURVE)
    highest_bucket = max(FAAB_BID_CURVE)
    low_bid = waiver_value.suggested_bid(float(lowest_bucket), 100.0, FAAB_BID_CURVE)
    high_bid = waiver_value.suggested_bid(float(highest_bucket) + 5.0, 100.0, FAAB_BID_CURVE)
    assert high_bid >= low_bid


def _roster(roster_id, player_ids):
    return RosterEntry(roster_id=roster_id, team_name=f"Team {roster_id}",
                       player_ids=tuple(player_ids), starter_ids=(),
                       wins=0, losses=0, ties=0, points_for=0.0, points_against=0.0)


def _league(roster_positions):
    class _L:
        pass
    lg = _L()
    lg.roster_positions = roster_positions
    return lg


def test_free_agents_excludes_every_rostered_player():
    rosters = [_roster(1, ["p1", "p2"]), _roster(2, ["p3"])]
    result = waiver_value.free_agents(["p1", "p2", "p3", "p4", "p5"], rosters)
    assert result == {"p4", "p5"}


def test_free_agents_with_no_rosters_returns_everyone():
    result = waiver_value.free_agents(["p1", "p2"], [])
    assert result == {"p1", "p2"}


def test_position_cap_uncapped_position_returns_none():
    league = _league(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    assert waiver_value.position_cap("RB", league) is None
    assert waiver_value.position_cap("WR", league) is None


def test_position_cap_te_with_one_te_eligible_flex_matches_the_users_own_example():
    """The user's own worked example from brainstorming: a 1-TE + 1-FLEX
    (TE-eligible) league should cap TE at exactly 3."""
    league = _league(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    assert waiver_value.position_cap("TE", league) == 3


def test_position_cap_qb_standard_non_superflex_matches_the_users_own_example():
    league = _league(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    assert waiver_value.position_cap("QB", league) == 2


def test_position_cap_def_and_k_have_zero_extra():
    league = _league(("QB", "RB", "RB", "WR", "WR", "TE", "DEF", "K", "BN"))
    assert waiver_value.position_cap("DEF", league) == 1
    assert waiver_value.position_cap("K", league) == 1


def test_position_cap_superflex_widens_the_qb_cap():
    league = _league(("QB", "SUPER_FLEX", "RB", "WR", "TE", "BN", "BN"))
    # 1 dedicated QB slot + 1 SUPER_FLEX (QB-eligible) + 1 extra = 3
    assert waiver_value.position_cap("QB", league) == 3
