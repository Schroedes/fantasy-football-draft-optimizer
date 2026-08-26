import pytest

from ffdo.domain.models import DraftPick, DraftState, LeagueProfile, PlayerProfile, ValuedPlayer
from ffdo.engine import auction, planner
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
    """When greedy's output is already locally optimal -- a real, legal,
    unplanned candidate exists for both slots but doesn't beat what's
    already there -- refinement must be a no-op.

    Regression test for a previously near-vacuous version of this test:
    the original fixture let greedy consume the *entire* available pool,
    so `_refine`'s candidate lists were empty and the VOR-gain comparison
    never actually ran a single candidate pair -- it passed trivially,
    not because the "no improvement" logic was exercised. `rb_low1` and
    `rb_low2` below are deliberately left over (greedy fills both slots
    before reaching them), giving `_refine` two real, distinct, legal
    candidates to compare and correctly reject.
    """
    league = _league_multi(("RB", "BN"))
    valued = _valued_positions({
        "rb_best": ("RB", 90.0), "rb_next": ("RB", 40.0),
        "rb_low1": ("RB", 5.0), "rb_low2": ("RB", 3.0),
    })
    baseline = {"rb_best": 15.0, "rb_next": 10.0, "rb_low1": 1.0, "rb_low2": 1.0}
    state = _empty_state()

    greedy_plan, available, used_ids, needs, caps = planner._greedy_fill(
        valued, baseline, 1.0, state, league, roster_id=None, your_dollars_left=25.0)
    # Confirm the fixture actually leaves real candidates on the table --
    # otherwise this test would silently regress back to vacuous.
    assert used_ids == {"rb_best", "rb_next"}
    assert {vp.profile.player_id for vp in available} - used_ids == {"rb_low1", "rb_low2"}
    before = [(s["player_id"], s["target_price"]) for s in greedy_plan]

    refined = planner._refine(greedy_plan, available, used_ids, baseline, 1.0, needs, caps)
    after = [(s["player_id"], s["target_price"]) for s in refined]

    assert before == after


def test_refine_rejects_swap_that_lowers_counted_vor_despite_higher_raw_vor():
    """Regression test for Fix 1, part A: pre-fix, `_refine`'s swap
    objective summed raw VOR symmetrically across the swapped pair with
    no regard for slot type. `total_plan_vor` only counts "dedicated" and
    "flex" slots ("bench" is excluded -- see `_to_output`), so a swap
    that raises combined raw VOR by dumping a good player onto BENCH
    while installing a worse player into a countable slot used to look
    like an improvement even though it lowers the number this feature
    actually displays.

    This is the minimal case that exposes it: one dedicated RB slot
    (currently a 60-VOR player) and one BENCH slot (currently an 8-VOR
    player), plus two unused candidates -- a weak 5-VOR RB (legal for
    the dedicated slot) and a huge 200-VOR WR (illegal for the dedicated
    slot, since dedicated legality requires an exact position match, but
    legal for BENCH, which accepts any offense position). The *only*
    swap `_refine` can even construct for this pair is
    (weak RB -> dedicated, huge WR -> bench): under the old unweighted
    rule that looks like a huge gain (5 + 200 - (60 + 8) = +137) and
    would have been accepted, cutting the dedicated slot's VOR from 60
    to 5 for a bench occupant that doesn't count at all. Under the fixed
    weighted rule the gain is (1*5 + 0*200) - (1*60 + 0*8) = -55, so it's
    correctly rejected and the plan is untouched.

    `caps` is hand-supplied (not derived from `position_caps`) purely so
    the position-cap check -- which a WR-less two-slot league would
    otherwise zero out for WR, rejecting this swap for an unrelated
    reason -- doesn't interfere with isolating the objective-weighting
    logic under test.
    """
    league = _league_multi(("RB", "BN"))
    valued = _valued_positions({
        "cur_dedicated": ("RB", 60.0), "cur_bench": ("RB", 8.0),
        "decoy_weak": ("RB", 5.0), "decoy_strong": ("WR", 200.0),
    })
    baseline = {"cur_dedicated": 8.0, "cur_bench": 1.0, "decoy_weak": 1.0, "decoy_strong": 1.0}
    state = _empty_state()
    needs = auction.compute_roster_needs(valued, state, league, roster_id=None)
    caps = {"RB": 2, "WR": 2}

    plan = [
        {"category": "RB", "type": "dedicated", "eligible_position": "RB",
         "player_id": "cur_dedicated", "name": "Cur Dedicated", "target_price": 8.0, "vor": 60.0},
        {"category": "BENCH", "type": "bench", "eligible_position": "RB",
         "player_id": "cur_bench", "name": "Cur Bench", "target_price": 1.0, "vor": 8.0},
    ]
    used_ids = {"cur_dedicated", "cur_bench"}
    available = [valued["decoy_strong"], valued["decoy_weak"]]  # VOR-descending, as `_greedy_fill` returns

    before_vor = sum(s["vor"] for s in plan if s["type"] in ("dedicated", "flex"))
    refined = planner._refine(plan, available, used_ids, baseline, 1.0, needs, caps)
    after_vor = sum(s["vor"] for s in refined if s["type"] in ("dedicated", "flex"))

    assert after_vor >= before_vor
    assert refined[0]["player_id"] == "cur_dedicated"  # the swap must never be applied
    assert refined[1]["player_id"] == "cur_bench"


def test_reassign_within_plan_promotes_benched_high_vor_player():
    """Regression test for Fix 1, part B, and the real-world case that
    motivated this whole review: two dedicated RB slots (VOR 54.1 and
    88.8), a FLEX slot occupied by a VOR-0 RB, and the highest-VOR RB in
    the plan (107.4) stuck on BENCH.

    `_refine`'s candidate pools are built from `available` players *not*
    already `used_ids` -- by construction, every already-placed player is
    excluded from ever being a "candidate" for another slot, so no
    amount of `_refine` swapping can ever move the BENCH occupant into
    FLEX or vice versa: both are already `used_ids` members. This is
    exactly why `_reassign_within_plan` has to exist as a distinct pass
    that reassigns which slot two *already-placed* players occupy.

    Routing this bad state through `_refine` first (with `available=[]`,
    since Phase 1's single VOR-descending pass structurally cannot
    itself produce "a higher-VOR player benched while a lower-VOR
    mutually-legal player starts" -- see the report for why) confirms
    the new weighted objective is a correct no-op here (nothing to swap,
    nothing corrupted) before `_reassign_within_plan` performs the fix
    that raises `total_plan_vor` by exactly the reviewer's cited amount,
    +107.4 (142.9 -> 250.3).
    """
    league = _league_multi(("RB", "RB", "FLEX", "BN"))
    valued = _valued_positions({
        "rb_d1": ("RB", 54.1), "rb_d2": ("RB", 88.8),
        "rb_flex_low": ("RB", 0.0), "rb_bench_high": ("RB", 107.4),
    })
    state = _empty_state()
    needs = auction.compute_roster_needs(valued, state, league, roster_id=None)
    caps = auction.position_caps(league, needs)

    plan = [
        {"category": "RB", "type": "dedicated", "eligible_position": "RB",
         "player_id": "rb_d1", "name": "RB D1", "target_price": 10.0, "vor": 54.1},
        {"category": "RB", "type": "dedicated", "eligible_position": "RB",
         "player_id": "rb_d2", "name": "RB D2", "target_price": 15.0, "vor": 88.8},
        {"category": "FLEX", "type": "flex", "eligible_position": "RB",
         "player_id": "rb_flex_low", "name": "RB Flex Low", "target_price": 1.0, "vor": 0.0},
        {"category": "BENCH", "type": "bench", "eligible_position": "RB",
         "player_id": "rb_bench_high", "name": "RB Bench High", "target_price": 1.0, "vor": 107.4},
    ]
    used_ids = {"rb_d1", "rb_d2", "rb_flex_low", "rb_bench_high"}

    before_vor = sum(s["vor"] for s in plan if s["type"] in ("dedicated", "flex"))
    assert before_vor == pytest.approx(142.9)

    refined = planner._refine(plan, [], used_ids, {}, 1.0, needs, caps)
    reassigned = planner._reassign_within_plan(refined, needs)

    after_vor = sum(s["vor"] for s in reassigned if s["type"] in ("dedicated", "flex"))
    assert after_vor == pytest.approx(250.3)
    assert after_vor - before_vor == pytest.approx(107.4)

    # The highest-VOR player must now sit in a counted slot, and the
    # bench must hold whichever occupant has the lowest VOR of the four.
    bench_slot = next(s for s in reassigned if s["type"] == "bench")
    assert bench_slot["player_id"] == "rb_flex_low"
    counted_ids = {s["player_id"] for s in reassigned if s["type"] in ("dedicated", "flex")}
    assert "rb_bench_high" in counted_ids


def test_optimal_plan_total_vor_never_below_greedy_alone():
    """Property-style regression test: across several distinct hand-built
    scenarios, `optimal_plan()`'s `total_plan_vor` must never be lower
    than what `_greedy_fill()` alone would have produced -- refinement
    and reassignment exist specifically to improve on Phase 1, never to
    regress it. Before Fix 1 part A, 118/400 of the reviewer's randomized
    scenarios violated this."""

    def greedy_vor(valued, baseline, factor, state, league, your_dollars_left):
        plan, *_ = planner._greedy_fill(
            valued, baseline, factor, state, league, roster_id=None,
            your_dollars_left=your_dollars_left)
        return sum(s["vor"] for s in plan if s["type"] in ("dedicated", "flex"))

    scenarios = []

    # 1. The known stud-blocking case -- refinement strictly improves on
    # greedy here (100.0 -> 110.0, see the swap-refinement test above).
    scenarios.append((
        _league_multi(("RB", "WR", "BN")),
        _valued_positions({
            "rb_stud": ("RB", 90.0), "rb_cheap": ("RB", 40.0),
            "wr_good": ("WR", 70.0), "wr_backup": ("WR", 35.0),
            "filler_wr": ("WR", 10.0), "filler_any": ("RB", 8.0),
        }),
        {"rb_stud": 19.0, "rb_cheap": 5.0, "wr_good": 12.0, "wr_backup": 6.0,
         "filler_wr": 1.0, "filler_any": 1.0},
        21.0,
    ))

    # 2. Already locally optimal -- equality, not improvement, is the
    # expected (and still property-satisfying) outcome.
    scenarios.append((
        _league_multi(("RB", "BN")),
        _valued_positions({
            "rb_best": ("RB", 90.0), "rb_next": ("RB", 40.0),
            "rb_low1": ("RB", 5.0), "rb_low2": ("RB", 3.0),
        }),
        {"rb_best": 15.0, "rb_next": 10.0, "rb_low1": 1.0, "rb_low2": 1.0},
        25.0,
    ))

    # 3. A larger, multi-position, generously-budgeted league.
    scenarios.append((
        _league_multi(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN")),
        _valued_positions({
            **{f"qb{i}": ("QB", 60.0 - i) for i in range(5)},
            **{f"rb{i}": ("RB", 70.0 - i) for i in range(10)},
            **{f"wr{i}": ("WR", 65.0 - i) for i in range(10)},
            **{f"te{i}": ("TE", 40.0 - i) for i in range(5)},
        }),
        None,  # baseline derived below
        200.0,
    ))

    # 4. A tight-budget league where affordability forces real trade-offs.
    scenarios.append((
        _league_multi(("RB", "WR", "FLEX", "BN")),
        _valued_positions({
            "rb1": ("RB", 50.0), "rb2": ("RB", 20.0), "rb3": ("RB", 4.0),
            "wr1": ("WR", 45.0), "wr2": ("WR", 15.0), "wr3": ("WR", 3.0),
        }),
        {"rb1": 9.0, "rb2": 4.0, "rb3": 1.0, "wr1": 8.0, "wr2": 3.0, "wr3": 1.0},
        15.0,
    ))

    state = _empty_state()
    for league, valued, baseline, budget in scenarios:
        if baseline is None:
            baseline = {pid: max(1.0, vp.vor / 10) for pid, vp in valued.items()}
        base_vor = greedy_vor(valued, baseline, 1.0, state, league, budget)
        result = planner.optimal_plan(
            valued, baseline, 1.0, state, league, roster_id=None, your_dollars_left=budget)
        assert result["total_plan_vor"] >= round(base_vor, 1) - 0.01, (
            f"optimal_plan regressed below greedy alone: "
            f"{result['total_plan_vor']} < {base_vor}")


def test_greedy_fill_reserves_budget_for_slots_the_optimizer_does_not_model():
    """Regression test for Fix 2: `league.roster_positions` can contain
    slot types (K, DEF, IR, ...) that `compute_roster_needs` never
    models at all -- they're invisible to `dedicated_need`/`flex_remaining`
    /`bench_remaining`, so without reserving for them, `_greedy_fill` can
    spend the whole budget on offense and leave less than $1/slot for K
    and DEF, producing a plan that claims to fit the budget but isn't
    actually executable.

    League: 1 dedicated RB + 1 BENCH (modeled) + K + DEF (NOT modeled) =
    4 roster slots, `your_dollars_left=6`. `rb_a` (VOR 90, $4) is
    deliberately priced to fall in the gap between the old and new
    reserve thresholds at the first pick (`total_slots_left=2`):
      - old reserve = MIN_BID*(2-1) = 1 -> affordability threshold $5 ->
        rb_a ($4) is accepted.
      - new reserve = MIN_BID*(2-1+2) = 3 -> affordability threshold $3 ->
        rb_a ($4) is correctly rejected.
    Hand-traced old-code outcome (not exercised here, since the fix is
    already applied, but included for why this is a real regression
    test): rb_a takes the dedicated slot for $4, `filler` ($1, VOR 5)
    takes BENCH, total cost $5, leaving dollars_left_after_plan = $1 --
    short of the $2 (MIN_BID * 2 unmodeled slots) actually needed for K
    and DEF. Hand-traced fixed-code outcome (asserted below): rb_a is
    skipped, `filler` ($1) takes the dedicated slot instead, `bench_filler`
    ($1, VOR 3) takes BENCH, total cost $2, leaving dollars_left_after_plan
    = $4 -- comfortably covering the $2 the unmodeled slots need.
    """
    league = _league_multi(("RB", "BN", "K", "DEF"))
    valued = _valued_positions({
        "rb_a": ("RB", 90.0), "filler": ("RB", 5.0), "bench_filler": ("RB", 3.0),
    })
    baseline = {"rb_a": 4.0, "filler": 1.0, "bench_filler": 1.0}
    state = _empty_state()

    plan, available, used_ids, needs, caps = planner._greedy_fill(
        valued, baseline, 1.0, state, league, roster_id=None, your_dollars_left=6.0)

    non_planned_slots = 2  # K, DEF
    dollars_left = 6.0 - sum(s["target_price"] for s in plan)
    assert dollars_left >= planner.MIN_BID * non_planned_slots

    player_ids = {s["player_id"] for s in plan}
    assert "rb_a" not in player_ids  # correctly rejected: would starve K/DEF's reserve
    assert player_ids == {"filler", "bench_filler"}
    assert dollars_left == pytest.approx(4.0)

    # `_refine`/`_reassign_within_plan` never increase total plan cost
    # (a swap only ever applies at <= combined current cost), so the
    # reserve established here can only hold or improve through
    # `optimal_plan()`'s later phases -- never regress.
    result = planner.optimal_plan(
        valued, baseline, 1.0, state, league, roster_id=None, your_dollars_left=6.0)
    assert 6.0 - result["total_plan_cost"] >= planner.MIN_BID * non_planned_slots


def test_reserve_formula_reduces_to_original_with_no_unmodeled_slots():
    """When every roster slot is one the optimizer models (the common
    case -- no K/DEF/IR), `non_planned_slots` must be 0 and the reserve
    formula must reduce to exactly the pre-fix original,
    `MIN_BID * (total_slots_left - 1)`. This is the same fixture/budget
    as `test_greedy_fill_skips_unaffordable_candidate_for_a_cheaper_one`,
    which already pins the exact accept/reject outcome that formula
    produces -- Fix 2 must not change it."""
    league = _league_multi(("RB", "BN"))
    valued = _valued_positions({
        "rb_expensive": ("RB", 90.0), "rb_cheap": ("RB", 50.0), "filler": ("RB", 10.0),
    })
    baseline = {"rb_expensive": 15.0, "rb_cheap": 5.0, "filler": 1.0}
    state = _empty_state()

    plan, available, used_ids, needs, caps = planner._greedy_fill(
        valued, baseline, 1.0, state, league, roster_id=None, your_dollars_left=10.0)

    player_ids = {s["player_id"] for s in plan}
    assert "rb_expensive" not in player_ids
    assert "rb_cheap" in player_ids
    assert sum(s["target_price"] for s in plan) <= 10.0
    assert len(plan) == 2
