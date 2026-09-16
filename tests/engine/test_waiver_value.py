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


def _roster(roster_id, player_ids, reserve_ids=(), taxi_ids=()):
    return RosterEntry(roster_id=roster_id, team_name=f"Team {roster_id}",
                       player_ids=tuple(player_ids), starter_ids=(),
                       wins=0, losses=0, ties=0, points_for=0.0, points_against=0.0,
                       reserve_ids=tuple(reserve_ids), taxi_ids=tuple(taxi_ids))


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


def test_droppable_player_ids_excludes_reserve_and_taxi_slots():
    """IR ('reserve') and Taxi Squad slots are restricted -- a player
    parked there must never be offered up as a waiver drop candidate."""
    roster = _roster(1, ["p1", "p2", "p3", "p4"], reserve_ids=["p2"], taxi_ids=["p3"])
    result = waiver_value.droppable_player_ids(roster)
    assert result == ("p1", "p4")


def test_droppable_player_ids_with_no_reserve_or_taxi_returns_everyone():
    roster = _roster(1, ["p1", "p2"])
    assert waiver_value.droppable_player_ids(roster) == ("p1", "p2")


def test_droppable_player_ids_all_players_restricted_returns_empty():
    roster = _roster(1, ["p1", "p2"], reserve_ids=["p1"], taxi_ids=["p2"])
    assert waiver_value.droppable_player_ids(roster) == ()


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


from ffdo.domain.models import PlayerProfile, ValuedPlayer


def _profile(pid, pos, injury=None, active=True):
    return PlayerProfile(player_id=pid, first_name="A", last_name="B",
                         position=pos, team="X", age=25, years_exp=3,
                         injury_status=injury, active=active)


def _valued(pid, pos, vor_val, injury=None, active=True):
    return ValuedPlayer(profile=_profile(pid, pos, injury, active),
                        projected_points=vor_val + 50.0, adjusted_points=vor_val + 50.0,
                        vor=vor_val, tier=1, adjustments={})


def test_recommends_a_clear_upgrade_over_worst_bench_player_with_open_position():
    profiles = {"fa1": _profile("fa1", "WR"), "bench1": _profile("bench1", "RB")}
    valued = {"fa1": _valued("fa1", "WR", 40.0), "bench1": _valued("bench1", "RB", 5.0)}
    league = _league(("QB", "RB", "RB", "WR", "WR", "TE", "BN", "BN"))
    result = waiver_value.recommend_adds(
        ["fa1"], ["bench1"], valued, profiles, league,
        faab_curve={0: 0.1, 30: 0.5}, remaining_budget=100.0)
    assert len(result) == 1
    assert result[0].free_agent_id == "fa1"
    assert result[0].drop_player_id == "bench1"
    assert result[0].vor_gain == pytest.approx(35.0)
    assert result[0].suggested_bid == pytest.approx(50.0)  # bucket 30 -> 0.5 * 100


def test_skips_a_free_agent_that_does_not_clear_min_vor_gain():
    profiles = {"fa1": _profile("fa1", "WR"), "bench1": _profile("bench1", "RB")}
    valued = {"fa1": _valued("fa1", "WR", 8.0), "bench1": _valued("bench1", "RB", 5.0)}
    league = _league(("QB", "RB", "WR", "BN"))
    result = waiver_value.recommend_adds(
        ["fa1"], ["bench1"], valued, profiles, league,
        faab_curve={0: 0.1}, remaining_budget=100.0, min_vor_gain=5.0)
    assert result == []  # only 3.0 VOR gain, below the 5.0 minimum


def test_excludes_an_injured_out_free_agent_entirely():
    profiles = {"fa1": _profile("fa1", "WR", injury="IR"), "bench1": _profile("bench1", "RB")}
    valued = {"fa1": _valued("fa1", "WR", 100.0, injury="IR"),
              "bench1": _valued("bench1", "RB", 1.0)}
    league = _league(("QB", "RB", "WR", "BN"))
    result = waiver_value.recommend_adds(
        ["fa1"], ["bench1"], valued, profiles, league,
        faab_curve={0: 0.1}, remaining_budget=100.0)
    assert result == []


def test_at_position_cap_only_compares_against_the_same_position():
    """The core scenario from brainstorming: a standard league already
    rostering 2 QBs (at the cap of dedicated=1 + extra=1 = 2) must not
    have a high-VOR free-agent QB compared against the worst BENCH player
    overall (a low-VOR kicker) -- only against the user's worst QB."""
    profiles = {
        "fa_qb": _profile("fa_qb", "QB"),
        "my_qb1": _profile("my_qb1", "QB"),
        "my_qb2": _profile("my_qb2", "QB"),
        "my_k": _profile("my_k", "K"),
    }
    valued = {
        "fa_qb": _valued("fa_qb", "QB", 20.0),
        "my_qb1": _valued("my_qb1", "QB", 15.0),
        "my_qb2": _valued("my_qb2", "QB", 3.0),   # worst QB
        "my_k": _valued("my_k", "K", -50.0),      # worst bench player overall
    }
    league = _league(("QB", "RB", "WR", "K", "BN", "BN"))
    result = waiver_value.recommend_adds(
        ["fa_qb"], ["my_qb1", "my_qb2", "my_k"], valued, profiles, league,
        faab_curve={0: 0.1, 10: 0.3}, remaining_budget=100.0)
    assert len(result) == 1
    assert result[0].drop_player_id == "my_qb2"   # worst QB, not my_k
    assert result[0].vor_gain == pytest.approx(17.0)  # 20.0 - 3.0, not 20.0 - (-50.0)


def test_no_existing_players_at_all_means_a_pure_add_with_no_drop():
    """Genuine edge case: an empty roster (e.g. right after first sync,
    before any players are rostered) has no drop candidate at all -- not
    a normal scenario (real rosters are kept full), but must not crash
    and must not invent a drop candidate that doesn't exist."""
    profiles = {"fa_qb": _profile("fa_qb", "QB")}
    valued = {"fa_qb": _valued("fa_qb", "QB", 20.0)}
    league = _league(("QB", "RB", "WR", "BN", "BN"))
    result = waiver_value.recommend_adds(
        ["fa_qb"], [], valued, profiles, league,
        faab_curve={0: 0.1, 10: 0.3}, remaining_budget=100.0)
    assert len(result) == 1
    assert result[0].drop_player_id is None
    assert result[0].vor_gain == pytest.approx(20.0)


def test_vor_gain_exactly_at_min_threshold_is_excluded():
    """Regression: min_vor_gain is an exclusive threshold -- a free agent
    must clear the comparison pool's worst player by a real margin, not
    exactly tie it. vor_gain == min_vor_gain (5.0 here) must be excluded,
    not recommended."""
    profiles = {"fa1": _profile("fa1", "WR"), "bench1": _profile("bench1", "RB")}
    valued = {"fa1": _valued("fa1", "WR", 10.0), "bench1": _valued("bench1", "RB", 5.0)}
    league = _league(("QB", "RB", "WR", "BN"))
    result = waiver_value.recommend_adds(
        ["fa1"], ["bench1"], valued, profiles, league,
        faab_curve={0: 0.1}, remaining_budget=100.0, min_vor_gain=5.0)
    # fa1's vor_gain is exactly 10.0 - 5.0 = 5.0, equal to min_vor_gain --
    # must be excluded (strictly greater than the threshold is required).
    assert result == []


def test_sorted_by_vor_gain_descending_and_capped_at_top_n():
    profiles = {f"fa{i}": _profile(f"fa{i}", "WR") for i in range(3)}
    profiles["bench1"] = _profile("bench1", "RB")
    valued = {f"fa{i}": _valued(f"fa{i}", "WR", float(10 * (i + 1))) for i in range(3)}
    valued["bench1"] = _valued("bench1", "RB", 0.0)
    league = _league(("QB", "RB", "WR", "BN"))
    result = waiver_value.recommend_adds(
        [f"fa{i}" for i in range(3)], ["bench1"], valued, profiles, league,
        faab_curve={0: 0.1}, remaining_budget=100.0, top_n=2)
    assert len(result) == 2
    assert result[0].free_agent_id == "fa2"  # highest VOR (30.0) first
    assert result[1].free_agent_id == "fa1"
