import pytest

from ffdo.domain.models import LeagueProfile, PlayerProfile, ValuedPlayer
from ffdo.engine import planner
from ffdo.ingest import draft


def _league_multi(roster_positions, budget=200, n=12):
    return LeagueProfile(league_id="x", season=2026, num_teams=n,
                         roster_positions=roster_positions,
                         scoring_settings={}, budget=budget)


def _valued_positions(position_vors):
    """position_vors: dict[pid] -> (position, vor)."""
    out = {}
    for pid, (pos, v) in position_vors.items():
        prof = PlayerProfile(player_id=pid, first_name="P", last_name=pid,
                             position=pos, team="X", age=25, years_exp=3,
                             injury_status=None, active=True)
        out[pid] = ValuedPlayer(profile=prof, projected_points=0.0,
                                adjusted_points=0.0, vor=v, tier=1,
                                adjustments={})
    return out


def _empty_state(teams=12, rounds=9, budget=200):
    return draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                        "settings": {"teams": teams, "rounds": rounds, "budget": budget}}, [])


def test_greedy_fill_never_exceeds_budget_and_fills_every_slot():
    league = _league_multi(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    valued = _valued_positions({
        **{f"qb{i}": ("QB", 60.0 - i) for i in range(5)},
        **{f"rb{i}": ("RB", 70.0 - i) for i in range(10)},
        **{f"wr{i}": ("WR", 65.0 - i) for i in range(10)},
        **{f"te{i}": ("TE", 40.0 - i) for i in range(5)},
    })
    baseline = {pid: max(1.0, vp.vor / 10) for pid, vp in valued.items()}
    state = _empty_state()

    plan, available, used_ids, needs, caps = planner._greedy_fill(
        valued, baseline, 1.0, state, league, roster_id=None, your_dollars_left=200.0)

    assert len(plan) == league.roster_size
    assert all(s["player_id"] is not None for s in plan)
    assert sum(s["target_price"] for s in plan) <= 200.0


def test_greedy_fill_dedicated_slots_match_position():
    league = _league_multi(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    valued = _valued_positions({
        **{f"qb{i}": ("QB", 60.0 - i) for i in range(3)},
        **{f"rb{i}": ("RB", 70.0 - i) for i in range(5)},
        **{f"wr{i}": ("WR", 65.0 - i) for i in range(5)},
        **{f"te{i}": ("TE", 40.0 - i) for i in range(3)},
    })
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}
    state = _empty_state()

    plan, *_ = planner._greedy_fill(
        valued, baseline, 1.0, state, league, roster_id=None, your_dollars_left=200.0)

    for slot in plan:
        if slot["type"] == "dedicated":
            assert slot["eligible_position"] == slot["category"]


def test_greedy_fill_respects_position_caps():
    """A deep single-position pool must not flood BENCH beyond the cap --
    other positions with room absorb the remaining bench slots instead."""
    league = _league_multi(("QB", "RB", "FLEX", "BN", "BN", "BN", "BN", "BN"))
    valued = _valued_positions({
        "qb1": ("QB", 50.0), "qb2": ("QB", 45.0),
        **{f"rb{i}": ("RB", 60.0 - i) for i in range(20)},
        **{f"wr{i}": ("WR", 30.0 - i) for i in range(10)},
    })
    baseline = {pid: max(1.0, vp.vor / 10) for pid, vp in valued.items()}
    state = _empty_state()

    plan, available, used_ids, needs, caps = planner._greedy_fill(
        valued, baseline, 1.0, state, league, roster_id=None, your_dollars_left=200.0)

    rb_count = sum(1 for s in plan if s["eligible_position"] == "RB")
    assert rb_count <= caps["RB"]
    assert len(plan) == league.roster_size
    assert all(s["player_id"] is not None for s in plan)


def test_optimal_plan_output_shape():
    league = _league_multi(("QB", "RB", "FLEX", "BN"))
    valued = _valued_positions({
        "qb1": ("QB", 50.0), "rb1": ("RB", 60.0), "rb2": ("RB", 40.0), "wr1": ("WR", 30.0),
    })
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}
    state = _empty_state()

    result = planner.optimal_plan(
        valued, baseline, 1.0, state, league, roster_id=None, your_dollars_left=200.0)

    assert len(result["slots"]) == league.roster_size
    assert result["total_plan_cost"] <= 200.0
    assert result["dollars_left_after_plan"] == pytest.approx(
        200.0 - result["total_plan_cost"], abs=0.01)
    assert result["total_plan_vor"] == pytest.approx(
        sum(s["vor"] for s in result["slots"] if s["type"] in ("dedicated", "flex")), abs=0.01)


def test_greedy_fill_skips_unaffordable_candidate_for_a_cheaper_one():
    """A tight budget must force the algorithm to reject a high-VOR but
    unaffordable candidate in favor of a cheaper one -- this is the one
    property the other budget test doesn't actually exercise, since its
    generous budget never triggers a rejection."""
    league = _league_multi(("RB", "BN"))
    valued = _valued_positions({
        "rb_expensive": ("RB", 90.0), "rb_cheap": ("RB", 50.0), "filler": ("RB", 10.0),
    })
    baseline = {"rb_expensive": 15.0, "rb_cheap": 5.0, "filler": 1.0}
    state = _empty_state()

    plan, available, used_ids, needs, caps = planner._greedy_fill(
        valued, baseline, 1.0, state, league, roster_id=None, your_dollars_left=10.0)

    player_ids = {s["player_id"] for s in plan}
    assert "rb_expensive" not in player_ids  # $15 unaffordable within $10 (with $1 reserved for the other slot)
    assert "rb_cheap" in player_ids
    assert sum(s["target_price"] for s in plan) <= 10.0
    assert len(plan) == 2


def test_swap_refinement_fixes_expensive_stud_blocking_two_good_players():
    """Regression test for the exact failure mode that motivated Phase 2:
    a single-pass greedy fill locks in an early expensive stud, leaving too
    little budget for two players elsewhere whose combined VOR would have
    been higher. The swap pass must find and apply that better pair."""
    league = _league_multi(("RB", "WR", "BN"))
    valued = _valued_positions({
        "rb_stud": ("RB", 90.0), "rb_cheap": ("RB", 40.0),
        "wr_good": ("WR", 70.0), "wr_backup": ("WR", 35.0),
        "filler_wr": ("WR", 10.0), "filler_any": ("RB", 8.0),
    })
    baseline = {
        "rb_stud": 19.0, "rb_cheap": 5.0,
        "wr_good": 12.0, "wr_backup": 6.0,
        "filler_wr": 1.0, "filler_any": 1.0,
    }
    state = _empty_state()

    greedy_plan, available, used_ids, needs, caps = planner._greedy_fill(
        valued, baseline, 1.0, state, league, roster_id=None, your_dollars_left=21.0)
    greedy_vor = sum(s["vor"] for s in greedy_plan if s["type"] in ("dedicated", "flex"))
    assert greedy_vor == pytest.approx(100.0)  # rb_stud(90) + filler_wr(10) -- suboptimal

    result = planner.optimal_plan(
        valued, baseline, 1.0, state, league, roster_id=None, your_dollars_left=21.0)

    assert result["total_plan_vor"] == pytest.approx(110.0)  # rb_cheap(40) + wr_good(70)
    by_category = {s["category"]: s["player_id"] for s in result["slots"]}
    assert by_category["RB"] == "rb_cheap"
    assert by_category["WR"] == "wr_good"
    assert result["total_plan_cost"] <= 21.0


def test_swap_refinement_leaves_already_optimal_plan_unchanged():
    """When greedy's output is already locally optimal (no other legal
    candidates exist), refinement must be a no-op."""
    league = _league_multi(("RB", "BN"))
    valued = _valued_positions({
        "rb_best": ("RB", 90.0), "rb_next": ("RB", 40.0),
    })
    baseline = {"rb_best": 15.0, "rb_next": 10.0}
    state = _empty_state()

    greedy_plan, available, used_ids, needs, caps = planner._greedy_fill(
        valued, baseline, 1.0, state, league, roster_id=None, your_dollars_left=25.0)
    before = [(s["player_id"], s["target_price"]) for s in greedy_plan]

    refined = planner._refine(greedy_plan, available, used_ids, baseline, 1.0, needs, caps)
    after = [(s["player_id"], s["target_price"]) for s in refined]

    assert before == after
