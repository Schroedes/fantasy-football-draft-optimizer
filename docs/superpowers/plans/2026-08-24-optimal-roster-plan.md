# Optimal Roster Plan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute and surface, live during the draft, the specific combination of still-available players — one per remaining roster slot — that maximizes total starting-lineup VOR within your remaining budget, in a new "Optimal Plan" sidebar panel.

**Architecture:** A shared `RosterNeeds` computation (extracted, behavior-preserving, from the existing `positional_budget()`) feeds a new two-phase planner: Phase 1 is a single VOR-ranked greedy pass that fills every slot within budget and per-position caps; Phase 2 is a bounded pairwise-swap local search that improves Phase 1's result toward the true budget-optimal combination. The API layer wires the planner's output into the existing auction board payload as a new top-level key; the UI renders it as a new sidebar card, following the existing Rosters/History panel pattern.

**Tech Stack:** Python 3 (pytest), vanilla JS/CSS (no build step, no JS test runner in this repo).

**Spec:** `docs/superpowers/specs/2026-08-24-optimal-roster-plan-design.md`

## Global Constraints

- Position cap = `2 × (dedicated slots for that position + flex slot instances that position is eligible for)`, counting players already drafted against the cap. Dedicated and FLEX assignments can never exceed it by construction — it only ever binds BENCH picks.
- Every slot in the plan is always filled — this is a guaranteed invariant given real player-pool depth, not a best-effort UI state. Tests assert it; no "no candidate" rendering path is built.
- Total plan cost must never exceed `your_dollars_left`.
- `total_plan_vor` sums VOR over `"dedicated"` and `"flex"` type slots only — BENCH doesn't score and is excluded from that headline number.
- A swap in the Phase 2 refinement is only ever taken if the swapped pair's combined price does not increase — this is what guarantees the running total never exceeds budget without tracking a separate global ledger during refinement.
- No change to `positional_budget()`'s own output shape, the existing position-budget panel, or the nominated-player card.
- Run `uv run pytest` (full suite) before every commit; every commit must leave the suite green.

---

### Task 1: Extract `compute_roster_needs()` and add `position_caps()`

**Files:**
- Modify: `src/ffdo/engine/auction.py` (extract `RosterNeeds`/`compute_roster_needs()` from `positional_budget()`'s existing body, lines 97-132; add `position_caps()`; refactor `positional_budget()` to use both)
- Modify: `tests/engine/test_auction.py` (add new tests for `compute_roster_needs()` and `position_caps()`; every existing `positional_budget()` test must keep passing unchanged)

**Interfaces:**
- Consumes: `OFFENSE_POSITIONS` (`ffdo.domain.constants`), `FLEX_ELIGIBILITY` (`ffdo.engine.replacement`) — both already imported in `auction.py`.
- Produces: `RosterNeeds` (frozen dataclass: `drafted_count: Mapping[str, int]`, `dedicated_count: Mapping[str, int]`, `dedicated_need: Mapping[str, int]`, `flex_positions: frozenset[str]`, `flex_total: int`, `flex_remaining: int`, `bench_total: int`, `bench_remaining: int`, `undetermined: int`); `compute_roster_needs(valued, state, league, roster_id) -> RosterNeeds`; `position_caps(league, needs: RosterNeeds) -> dict[str, int]` (position -> remaining cap room). Task 2's `planner.py` imports all three from `ffdo.engine.auction`.

- [ ] **Step 1: Write tests for `compute_roster_needs()`**

Add to `tests/engine/test_auction.py`:

```python
def test_compute_roster_needs_no_picks():
    league = _league_multi(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    valued = _valued_positions({"qb1": ("QB", 50.0), "rb1": ("RB", 80.0)})
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 9, "budget": 200}}, [])

    needs = auction.compute_roster_needs(valued, state, league, roster_id=None)

    assert dict(needs.dedicated_count) == {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
    assert dict(needs.drafted_count) == {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    assert dict(needs.dedicated_need) == {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
    assert needs.flex_positions == frozenset({"RB", "WR", "TE"})
    assert needs.flex_total == 1
    assert needs.flex_remaining == 1
    assert needs.bench_total == 2
    assert needs.bench_remaining == 2
    assert needs.undetermined == 0


def test_compute_roster_needs_with_picks_and_leftover():
    league = _league_multi(("RB", "FLEX", "BN"))
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="rb_drafted_1", amount=10),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=1, picked_by="u1",
                 player_id="rb_drafted_2", amount=10),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=3, budget=200, picks=picks)
    valued = _valued_positions({
        "rb_drafted_1": ("RB", 50.0), "rb_drafted_2": ("RB", 40.0),
    })

    needs = auction.compute_roster_needs(valued, state, league, roster_id=1)

    assert dict(needs.dedicated_need) == {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    assert needs.flex_remaining == 0
    assert needs.bench_remaining == 1
    assert needs.drafted_count["RB"] == 2
```

- [ ] **Step 2: Write tests for `position_caps()`**

Add to the same file:

```python
def test_position_caps_matches_confirmed_examples():
    league = _league_multi(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    valued: dict = {}
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 9, "budget": 200}}, [])
    needs = auction.compute_roster_needs(valued, state, league, roster_id=None)

    caps = auction.position_caps(league, needs)

    assert caps["QB"] == 2   # 1 dedicated + 0 flex-eligible -> 2x1
    assert caps["RB"] == 6   # 2 dedicated + 1 flex-eligible -> 2x3


def test_position_caps_subtracts_already_drafted():
    league = _league_multi(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="qb1", amount=10),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=1, picked_by="u1",
                 player_id="qb2", amount=10),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=9, budget=200, picks=picks)
    valued = _valued_positions({"qb1": ("QB", 50.0), "qb2": ("QB", 40.0)})
    needs = auction.compute_roster_needs(valued, state, league, roster_id=1)

    caps = auction.position_caps(league, needs)

    assert caps["QB"] == 0  # cap 2, already drafted 2 -> 0 remaining
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/engine/test_auction.py -v -k "roster_needs or position_caps"`

Expected: FAIL — `AttributeError: module 'ffdo.engine.auction' has no attribute 'compute_roster_needs'` (and `position_caps`).

- [ ] **Step 4: Implement `RosterNeeds`, `compute_roster_needs()`, and `position_caps()`**

In `src/ffdo/engine/auction.py`, add `from dataclasses import dataclass` to the imports at the top of the file, then insert the following between `max_bid()` and `positional_budget()`:

```python
@dataclass(frozen=True, slots=True)
class RosterNeeds:
    drafted_count: Mapping[str, int]      # pos -> count you've already drafted
    dedicated_count: Mapping[str, int]    # pos -> dedicated slots the league has
    dedicated_need: Mapping[str, int]     # pos -> dedicated slots still open
    flex_positions: frozenset[str]        # union of positions eligible for any flex slot
    flex_total: int
    flex_remaining: int
    bench_total: int
    bench_remaining: int
    undetermined: int                     # your picks at non-offense positions (K/DST)


def compute_roster_needs(
    valued: Mapping[str, ValuedPlayer],
    state: DraftState,
    league,
    roster_id: int | None,
) -> RosterNeeds:
    """Your remaining roster needs, broken into dedicated/FLEX/BENCH.

    Shared by `positional_budget()` and `planner.optimal_plan()` so both
    features agree, by construction, on what "your remaining needs" means.
    """
    your_picks = ([p for p in state.picks if p.roster_id == roster_id]
                  if roster_id is not None else [])

    dedicated_count = {pos: league.roster_positions.count(pos)
                       for pos in OFFENSE_POSITIONS}
    drafted_count = dict.fromkeys(OFFENSE_POSITIONS, 0)
    undetermined = 0
    for pick in your_picks:
        vp = valued.get(pick.player_id)
        if vp is None or vp.profile.position not in OFFENSE_POSITIONS:
            undetermined += 1
            continue
        drafted_count[vp.profile.position] += 1

    dedicated_need = {
        pos: max(0, dedicated_count[pos] - min(dedicated_count[pos], drafted_count[pos]))
        for pos in OFFENSE_POSITIONS
    }
    flex_positions = frozenset(
        pos for slot in league.roster_positions if slot in FLEX_ELIGIBILITY
        for pos in FLEX_ELIGIBILITY[slot]
    )
    flex_eligible_leftover = sum(
        max(0, drafted_count[pos] - dedicated_count[pos])
        for pos in OFFENSE_POSITIONS if pos in flex_positions)
    non_flex_leftover = sum(
        max(0, drafted_count[pos] - dedicated_count[pos])
        for pos in OFFENSE_POSITIONS if pos not in flex_positions)

    flex_total = sum(1 for slot in league.roster_positions
                     if slot in FLEX_ELIGIBILITY)
    bench_total = league.roster_positions.count("BN")
    flex_remaining = max(0, flex_total - flex_eligible_leftover)
    bench_spill = max(0, flex_eligible_leftover - flex_total) + non_flex_leftover
    bench_remaining = max(0, bench_total - bench_spill - undetermined)

    return RosterNeeds(
        drafted_count=drafted_count, dedicated_count=dedicated_count,
        dedicated_need=dedicated_need, flex_positions=flex_positions,
        flex_total=flex_total, flex_remaining=flex_remaining,
        bench_total=bench_total, bench_remaining=bench_remaining,
        undetermined=undetermined,
    )


def position_caps(league, needs: RosterNeeds) -> dict[str, int]:
    """How many MORE players of each position the plan may add.

    Cap = 2 x (dedicated slots for that position + flex slot instances
    that position is eligible for), counting what you've already drafted
    against the cap. Dedicated and FLEX assignments can never exceed this
    by construction -- it only ever actually constrains BENCH picks.
    """
    remaining: dict[str, int] = {}
    for pos in OFFENSE_POSITIONS:
        flex_slots_for_pos = sum(
            1 for slot in league.roster_positions
            if slot in FLEX_ELIGIBILITY and pos in FLEX_ELIGIBILITY[slot]
        )
        starting_spots = needs.dedicated_count[pos] + flex_slots_for_pos
        cap = 2 * starting_spots
        remaining[pos] = max(0, cap - needs.drafted_count[pos])
    return remaining
```

- [ ] **Step 5: Refactor `positional_budget()` to use the extracted helper**

Replace `positional_budget()`'s body (the code before the `raw: dict[str, float] = {}` line) so it calls `compute_roster_needs()` instead of recomputing the same bookkeeping inline. The full function becomes:

```python
def positional_budget(
    valued: Mapping[str, ValuedPlayer],
    baseline: Mapping[str, float],
    factor: float,
    state: DraftState,
    league,
    roster_id: int | None,
    your_dollars_left: float,
) -> dict[str, dict[str, float]]:
    """Recommended $ per position to fill your remaining roster slots.

    Dedicated slots (e.g. a plain "RB" slot) only ever take that exact
    position. FLEX-eligible slots are priced from the best remaining
    players at any flex-eligible position, after dedicated slots have
    already claimed their share -- so FLEX (a real starting spot that
    scores every week) gets a real market price, not a floor. Bench slots
    carry no positional preference and score nothing, so they stay a flat
    $1/slot reserve. `roster_id=None` (FFDO_ROSTER_ID unset) is treated as
    a fresh roster -- zero drafted -- the same fallback the board applies
    to max-bid elsewhere.
    """
    needs = compute_roster_needs(valued, state, league, roster_id)
    drafted = state.drafted_player_ids()
    available = [vp for pid, vp in valued.items() if pid not in drafted]

    raw: dict[str, float] = {}
    pos_leftover_pool: dict[str, list[float]] = {}
    for pos in OFFENSE_POSITIONS:
        need = needs.dedicated_need[pos]
        pool = sorted(
            (max(MIN_BID, baseline.get(vp.profile.player_id, 1.0) * factor)
             for vp in available if vp.profile.position == pos),
            reverse=True,
        )
        raw[pos] = sum(pool[:need])
        pos_leftover_pool[pos] = pool[need:]

    flex_candidates = sorted(
        (price for pos in needs.flex_positions for price in pos_leftover_pool.get(pos, [])),
        reverse=True,
    )[:needs.flex_remaining]
    raw_flex = sum(flex_candidates)
    raw_bench = MIN_BID * needs.bench_remaining

    total_raw = sum(raw.values()) + raw_flex + raw_bench
    scale = your_dollars_left / total_raw if total_raw > 0 else 0.0

    out: dict[str, dict[str, float]] = {
        pos: {
            "recommended": round(raw[pos] * scale, 1),
            "slots_open": needs.dedicated_need[pos],
        }
        for pos in OFFENSE_POSITIONS
    }
    out["FLEX"] = {
        "recommended": round(raw_flex * scale, 1),
        "slots_open": needs.flex_remaining,
    }
    out["BENCH"] = {
        "recommended": round(raw_bench * scale, 1),
        "slots_open": needs.bench_remaining,
    }
    return out
```

- [ ] **Step 6: Run the new tests, then the full auction/board test files, to verify everything passes**

Run: `uv run pytest tests/engine/test_auction.py tests/api/test_board.py -v`

Expected: PASS — the 4 new tests, plus every pre-existing test in both files (the `positional_budget()` refactor must be behavior-preserving).

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`

Expected: PASS, no regressions anywhere else.

- [ ] **Step 8: Commit**

```bash
git add src/ffdo/engine/auction.py tests/engine/test_auction.py
git commit -m "refactor: extract compute_roster_needs(), add position_caps()"
```

---

### Task 2: `optimal_plan()` Phase 1 — greedy VOR-ranked fill

**Files:**
- Create: `src/ffdo/engine/planner.py`
- Test: `tests/engine/test_planner.py` (new file)

**Interfaces:**
- Consumes: `RosterNeeds`, `compute_roster_needs()`, `position_caps()`, `MIN_BID` from `ffdo.engine.auction` (Task 1).
- Produces: `_greedy_fill(valued, baseline, factor, state, league, roster_id, your_dollars_left) -> tuple[list[dict], list[ValuedPlayer], set[str], RosterNeeds, dict[str, int]]` returning `(plan, available, used_ids, needs, caps)` — Task 3's `_refine()` consumes exactly this tuple shape. `_to_output(plan, your_dollars_left) -> dict` shaped `{"slots": [...], "total_plan_vor": float, "total_plan_cost": float, "dollars_left_after_plan": float}`. `optimal_plan(valued, baseline, factor, state, league, roster_id, your_dollars_left) -> dict` — the public entry point, same shape as `_to_output()`'s return. Each `plan` entry: `{"category": str, "type": "dedicated"|"flex"|"bench", "eligible_position": str, "player_id": str, "name": str, "target_price": float, "vor": float}`.

- [ ] **Step 1: Write tests for `_greedy_fill()` and `optimal_plan()`**

Create `tests/engine/test_planner.py`:

```python
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
    # Scaled down from raw VOR: 9 slots at the unscaled VOR-as-price values
    # (up to $70/player) sum to well over the $200 budget, making every
    # slot mathematically unaffordable regardless of algorithm correctness.
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
    # Scaled down for the same reason as the test above -- 8 slots at raw
    # VOR-as-price values are not affordable within the $200 budget.
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/engine/test_planner.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'ffdo.engine.planner'`.

- [ ] **Step 3: Implement `planner.py`**

Create `src/ffdo/engine/planner.py`:

```python
"""Budget-constrained optimal roster planning."""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.constants import OFFENSE_POSITIONS
from ffdo.domain.models import DraftState, ValuedPlayer
from ffdo.engine.auction import MIN_BID, RosterNeeds, compute_roster_needs, position_caps


def _price_of(vp: ValuedPlayer, baseline: Mapping[str, float], factor: float) -> float:
    return max(MIN_BID, baseline.get(vp.profile.player_id, 1.0) * factor)


def _greedy_fill(
    valued: Mapping[str, ValuedPlayer],
    baseline: Mapping[str, float],
    factor: float,
    state: DraftState,
    league,
    roster_id: int | None,
    your_dollars_left: float,
) -> tuple[list[dict], list[ValuedPlayer], set[str], RosterNeeds, dict[str, int]]:
    """Phase 1: single VOR-ranked pass, budget-and-cap-constrained.

    Walks every available offense player once, VOR descending, greedily
    assigning each to the most specific open slot it's legal for (its
    exact dedicated position first, then FLEX, then BENCH). Returns
    (plan, available, used_ids, needs, caps) so Phase 2 (`_refine`, added
    in a later task) can build directly on this pass's state without
    recomputing it.
    """
    needs = compute_roster_needs(valued, state, league, roster_id)
    caps = position_caps(league, needs)
    drafted = state.drafted_player_ids()
    available = sorted(
        (vp for pid, vp in valued.items()
         if pid not in drafted and vp.profile.position in OFFENSE_POSITIONS),
        key=lambda vp: vp.vor, reverse=True,
    )

    remaining_dedicated = dict(needs.dedicated_need)
    remaining_flex = needs.flex_remaining
    remaining_bench = needs.bench_remaining
    remaining_cap = dict(caps)
    total_slots_left = (sum(remaining_dedicated.values())
                        + remaining_flex + remaining_bench)
    budget_left = your_dollars_left

    plan: list[dict] = []
    used_ids: set[str] = set()

    for vp in available:
        if total_slots_left == 0:
            break
        pos = vp.profile.position
        if remaining_cap.get(pos, 0) <= 0:
            continue

        price = _price_of(vp, baseline, factor)
        reserve_for_others = MIN_BID * (total_slots_left - 1)
        if price > budget_left - reserve_for_others:
            continue

        if remaining_dedicated.get(pos, 0) > 0:
            slot_type, category = "dedicated", pos
            remaining_dedicated[pos] -= 1
        elif remaining_flex > 0 and pos in needs.flex_positions:
            slot_type, category = "flex", "FLEX"
            remaining_flex -= 1
        elif remaining_bench > 0:
            slot_type, category = "bench", "BENCH"
            remaining_bench -= 1
        else:
            continue

        plan.append({
            "category": category, "type": slot_type,
            "eligible_position": pos, "player_id": vp.profile.player_id,
            "name": vp.profile.full_name, "target_price": price, "vor": vp.vor,
        })
        used_ids.add(vp.profile.player_id)
        budget_left -= price
        total_slots_left -= 1
        remaining_cap[pos] -= 1

    return plan, available, used_ids, needs, caps


def _to_output(plan: list[dict], your_dollars_left: float) -> dict:
    total_cost = sum(s["target_price"] for s in plan)
    total_vor = sum(s["vor"] for s in plan if s["type"] in ("dedicated", "flex"))
    return {
        "slots": plan,
        "total_plan_vor": round(total_vor, 1),
        "total_plan_cost": round(total_cost, 1),
        "dollars_left_after_plan": round(your_dollars_left - total_cost, 1),
    }


def optimal_plan(
    valued: Mapping[str, ValuedPlayer],
    baseline: Mapping[str, float],
    factor: float,
    state: DraftState,
    league,
    roster_id: int | None,
    your_dollars_left: float,
) -> dict:
    """The specific, budget-affordable roster that maximizes starting VOR.

    Phase 1 (`_greedy_fill`) builds an initial plan. A later task adds
    Phase 2, a bounded local-search refinement, between the two calls
    below.
    """
    plan, available, used_ids, needs, caps = _greedy_fill(
        valued, baseline, factor, state, league, roster_id, your_dollars_left)
    return _to_output(plan, your_dollars_left)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_planner.py -v`

Expected: PASS — all 4 tests.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`

Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/engine/planner.py tests/engine/test_planner.py
git commit -m "feat: add optimal_plan() Phase 1 -- greedy VOR-ranked roster fill"
```

---

### Task 3: `optimal_plan()` Phase 2 — pairwise swap refinement

**Files:**
- Modify: `src/ffdo/engine/planner.py` (add `_is_legal()`, `_caps_ok()`, `_apply()`, `_refine()`; wire `_refine()` into `optimal_plan()`)
- Modify: `tests/engine/test_planner.py` (add the swap-improvement regression test and the no-improvement-unchanged test)

**Interfaces:**
- Consumes: the exact `(plan, available, used_ids, needs, caps)` tuple `_greedy_fill()` produces (Task 2).
- Produces: `_refine(plan, available, used_ids, baseline, factor, needs, caps) -> list[dict]` — mutates and returns the same `plan` list (slot dicts updated in place via `_apply()`). `optimal_plan()`'s public signature and return shape are unchanged from Task 2; only its internals gain the Phase 2 call.

- [ ] **Step 1: Write the swap-improvement regression test**

Add to `tests/engine/test_planner.py`:

```python
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
```

- [ ] **Step 2: Write the no-improvement-leaves-unchanged test**

Add to the same file:

```python
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
```

- [ ] **Step 3: Run both new tests to verify they fail**

Run: `uv run pytest tests/engine/test_planner.py -v -k "swap_refinement"`

Expected: FAIL — `AttributeError: module 'ffdo.engine.planner' has no attribute '_refine'`.

- [ ] **Step 4: Implement Phase 2**

Add to `src/ffdo/engine/planner.py`, after `_to_output()` and before `optimal_plan()`:

```python
MAX_SWAP_ITERATIONS = 200
CANDIDATES_PER_SLOT = 15


def _is_legal(vp: ValuedPlayer, slot: dict, flex_positions: frozenset[str]) -> bool:
    pos = vp.profile.position
    if slot["type"] == "dedicated":
        return pos == slot["category"]
    if slot["type"] == "flex":
        return pos in flex_positions
    return True  # bench: any offense position is legal


def _caps_ok(
    pos_counts: dict[str, int],
    old_pos_a: str, new_pos_a: str,
    old_pos_b: str, new_pos_b: str,
    caps: dict[str, int],
) -> bool:
    """True if swapping old_pos_a/old_pos_b out for new_pos_a/new_pos_b
    keeps every position's plan usage within its cap."""
    trial = dict(pos_counts)
    trial[old_pos_a] -= 1
    trial[old_pos_b] -= 1
    trial[new_pos_a] = trial.get(new_pos_a, 0) + 1
    trial[new_pos_b] = trial.get(new_pos_b, 0) + 1
    return all(trial.get(pos, 0) <= caps.get(pos, 0) for pos in trial)


def _apply(slot: dict, vp: ValuedPlayer, price: float) -> None:
    slot["eligible_position"] = vp.profile.position
    slot["player_id"] = vp.profile.player_id
    slot["name"] = vp.profile.full_name
    slot["target_price"] = price
    slot["vor"] = vp.vor


def _refine(
    plan: list[dict],
    available: list[ValuedPlayer],
    used_ids: set[str],
    baseline: Mapping[str, float],
    factor: float,
    needs: RosterNeeds,
    caps: dict[str, int],
) -> list[dict]:
    """Bounded local search: repeatedly swap a pair of planned slots for a
    higher-combined-VOR pair of legal replacements at no higher combined
    cost, until no improving swap exists or the iteration cap is hit.

    This is what catches "an early expensive pick blocked two efficient
    players that together beat it" -- Phase 1's single greedy pass can't
    see that after the fact; this pass can, within its search radius (top
    `CANDIDATES_PER_SLOT` unplanned players by VOR per slot).
    """
    pos_counts = dict.fromkeys(needs.dedicated_count, 0)
    for slot in plan:
        pos_counts[slot["eligible_position"]] = pos_counts.get(slot["eligible_position"], 0) + 1

    for _ in range(MAX_SWAP_ITERATIONS):
        improved = False
        for i, slot_a in enumerate(plan):
            candidates_a = [vp for vp in available
                            if vp.profile.player_id not in used_ids
                            and _is_legal(vp, slot_a, needs.flex_positions)][:CANDIDATES_PER_SLOT]
            for j, slot_b in enumerate(plan):
                if j <= i:
                    continue
                candidates_b = [vp for vp in available
                                if vp.profile.player_id not in used_ids
                                and _is_legal(vp, slot_b, needs.flex_positions)][:CANDIDATES_PER_SLOT]

                current_price = slot_a["target_price"] + slot_b["target_price"]
                current_vor = slot_a["vor"] + slot_b["vor"]

                best = None  # (vor_gain, ca, cb, pa, pb)
                for ca in candidates_a:
                    for cb in candidates_b:
                        if ca.profile.player_id == cb.profile.player_id:
                            continue
                        pa = _price_of(ca, baseline, factor)
                        pb = _price_of(cb, baseline, factor)
                        if pa + pb > current_price:
                            continue
                        if not _caps_ok(pos_counts,
                                        slot_a["eligible_position"], ca.profile.position,
                                        slot_b["eligible_position"], cb.profile.position,
                                        caps):
                            continue
                        vor_gain = (ca.vor + cb.vor) - current_vor
                        if vor_gain > 0 and (best is None or vor_gain > best[0]):
                            best = (vor_gain, ca, cb, pa, pb)

                if best is not None:
                    _, ca, cb, pa, pb = best
                    used_ids.discard(slot_a["player_id"])
                    used_ids.discard(slot_b["player_id"])
                    pos_counts[slot_a["eligible_position"]] -= 1
                    pos_counts[slot_b["eligible_position"]] -= 1
                    _apply(slot_a, ca, pa)
                    _apply(slot_b, cb, pb)
                    used_ids.add(ca.profile.player_id)
                    used_ids.add(cb.profile.player_id)
                    pos_counts[ca.profile.position] = pos_counts.get(ca.profile.position, 0) + 1
                    pos_counts[cb.profile.position] = pos_counts.get(cb.profile.position, 0) + 1
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break

    return plan
```

Then update `optimal_plan()`'s body to call `_refine()` between `_greedy_fill()` and `_to_output()`:

```python
def optimal_plan(
    valued: Mapping[str, ValuedPlayer],
    baseline: Mapping[str, float],
    factor: float,
    state: DraftState,
    league,
    roster_id: int | None,
    your_dollars_left: float,
) -> dict:
    """The specific, budget-affordable roster that maximizes starting VOR.

    Two phases: Phase 1 (`_greedy_fill`) builds an initial plan by walking
    every available player once, VOR descending. Phase 2 (`_refine`)
    then runs a bounded pairwise-swap local search to catch cases where
    an early expensive pick blocked a better later combination.
    """
    plan, available, used_ids, needs, caps = _greedy_fill(
        valued, baseline, factor, state, league, roster_id, your_dollars_left)
    plan = _refine(plan, available, used_ids, baseline, factor, needs, caps)
    return _to_output(plan, your_dollars_left)
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/engine/test_planner.py -v`

Expected: PASS — all tests in the file, including the two new ones.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`

Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/ffdo/engine/planner.py tests/engine/test_planner.py
git commit -m "feat: add optimal_plan() Phase 2 -- pairwise swap refinement"
```

---

### Task 4: Wire `optimal_plan()` into the auction board API

**Files:**
- Modify: `src/ffdo/api/board.py` (import `planner`, call `optimal_plan()` in `build_auction_board()`, add `"optimal_plan"` to the payload)
- Modify: `tests/api/test_board.py` (add a wiring test)

**Interfaces:**
- Consumes: `planner.optimal_plan(valued, baseline, factor, state, league, roster_id, your_dollars_left) -> dict` (Tasks 2-3).
- Produces: `build_auction_board(...)`'s returned payload gains a top-level `"optimal_plan"` key, sibling to `"budget"`, shaped exactly as `optimal_plan()` returns it. `board.js` (Task 5) reads `d.optimal_plan` from this payload.

- [ ] **Step 1: Write the wiring test**

Add to `tests/api/test_board.py`:

```python
def test_board_includes_optimal_plan():
    # num_teams=1 (not the usual 12): inflation_factor() scales prices by
    # league-wide budget over league-wide remaining value, and this
    # fixture only values 4 players against a 12-team economy's assumed
    # 48 total slots -- that mismatch inflates prices far past what a
    # single team's $200 could ever afford. A 1-team league keeps the
    # test's actual purpose (payload wiring) intact while making the
    # inflation math self-consistent with this fixture's tiny player pool.
    league = LeagueProfile(league_id="x", season=2025, num_teams=1,
                           roster_positions=("QB", "RB", "FLEX", "BN"),
                           scoring_settings={}, budget=200)
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 1, "rounds": 4, "budget": 200}}, [])

    def _player(pid, pos, vor):
        prof = PlayerProfile(player_id=pid, first_name="P", last_name=pid,
                             position=pos, team="X", age=25, years_exp=3,
                             injury_status=None, active=True)
        return ValuedPlayer(profile=prof, projected_points=0.0,
                            adjusted_points=0.0, vor=vor, tier=1,
                            adjustments={})

    valued = {
        "qb1": _player("qb1", "QB", 50.0),
        "rb1": _player("rb1", "RB", 60.0),
        "rb2": _player("rb2", "RB", 40.0),
        "wr1": _player("wr1", "WR", 45.0),
    }
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}

    out = board.build_auction_board(league, state, valued, baseline, roster_id=None)

    assert "optimal_plan" in out
    plan = out["optimal_plan"]
    assert len(plan["slots"]) == league.roster_size
    assert all(s["player_id"] is not None for s in plan["slots"])
    assert plan["total_plan_cost"] <= out["budget"]["your_dollars_left"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_board.py -v -k "optimal_plan"`

Expected: FAIL — `KeyError: 'optimal_plan'` (the payload doesn't have this key yet).

- [ ] **Step 3: Wire the planner into `board.py`**

In `src/ffdo/api/board.py`, add `from ffdo.engine import planner` to the imports (alongside the existing `from ffdo.engine import auction` / `grading` / `roster as roster_engine` lines).

In `build_auction_board()`, right after the existing:

```python
    by_position = auction.positional_budget(
        valued, baseline, factor, state, league, roster_id, your_dollars_left)
```

add:

```python
    plan = planner.optimal_plan(
        valued, baseline, factor, state, league, roster_id, your_dollars_left)
```

Then, in the function's returned dict, add `"optimal_plan": plan,` as a new top-level key, immediately after the closing `},` of the existing `"budget": {...}` block (i.e. a sibling of `"budget"`, `"picks_made"`, `"players"`, etc.).

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/api/test_board.py -v -k "optimal_plan"`

Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`

Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/ffdo/api/board.py tests/api/test_board.py
git commit -m "feat: wire optimal_plan() into the auction board API payload"
```

---

### Task 5: Render the Optimal Plan panel

**Files:**
- Modify: `src/ffdo/web/board/index.html` (new `<aside id="optimal-plan">` card)
- Modify: `src/ffdo/web/board/board.js` (new `renderOptimalPlan()`, hooked into `render()`)
- Modify: `src/ffdo/web/board/board.css` (new `.plan-*` classes)

**Interfaces:**
- Consumes: `state.data.optimal_plan` (Task 4's payload shape: `{"slots": [...], "total_plan_vor": number, "total_plan_cost": number, "dollars_left_after_plan": number}`, each slot `{"category": str, "type": str, "eligible_position": str, "player_id": str, "name": str, "target_price": number, "vor": number}`).
- Produces: no new interface — `renderOptimalPlan()` is a void function called from `render()`, following the exact pattern `renderPositionBudget()` already establishes.

- [ ] **Step 1: Add the panel markup**

In `src/ffdo/web/board/index.html`, insert a new `<aside>` right after the existing `</aside>` that closes `#history` (and before the closing `</div>` of `#sidebar`):

```html
  <aside id="optimal-plan">
    <div class="plan-head">
      <h2>Optimal plan</h2>
      <span class="plan-sub">the best affordable roster within your remaining budget</span>
    </div>
    <div class="plan-totals">
      <span><b id="plan-vor">&mdash;</b> VOR</span>
      <span><b id="plan-cost">&mdash;</b> spent</span>
      <span><b id="plan-left">&mdash;</b> left</span>
    </div>
    <div id="plan-rows"></div>
  </aside>
```

- [ ] **Step 2: Add `renderOptimalPlan()` and hook it into `render()`**

In `src/ffdo/web/board/board.js`, add this new function near `renderPositionBudget()`:

```javascript
function renderOptimalPlan() {
  const el = document.getElementById("optimal-plan");
  const d = state.data;
  const plan = d && d.optimal_plan;
  if (d.format === "snake" || !plan) {
    el.hidden = true;
    return;
  }
  el.hidden = false;

  document.getElementById("plan-vor").textContent = plan.total_plan_vor;
  document.getElementById("plan-cost").textContent = `$${plan.total_plan_cost}`;
  document.getElementById("plan-left").textContent = `$${plan.dollars_left_after_plan}`;

  document.getElementById("plan-rows").innerHTML = plan.slots.map(slot => {
    const posColor = `var(--${slot.eligible_position.toLowerCase()}, var(--muted))`;
    return `<div class="plan-row">
      <span class="plan-category">${slot.category}</span>
      <span class="plan-name" style="color:${posColor}">${slot.name}</span>
      <span class="plan-price">$${slot.target_price}</span>
      <span class="plan-vor-val">${slot.vor} VOR</span>
    </div>`;
  }).join("");
}
```

Then, in `render()`, add a call to `renderOptimalPlan();` immediately after the existing `renderPositionBudget();` line.

- [ ] **Step 3: Add CSS for the new panel**

In `src/ffdo/web/board/board.css`, add the following near the existing `.history-row`/`.rosters-head` rules:

```css
#optimal-plan {
  flex-shrink: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.plan-head h2 { font-size: 13px; font-weight: 600; margin: 0 0 2px; }
.plan-sub { font-size: 11px; color: var(--faint); }
.plan-totals { display: flex; gap: 14px; padding: 6px 4px 10px; font-size: 11.5px; color: var(--faint); }
.plan-totals b { font-family: var(--font-mono); font-size: 13px; color: var(--text); font-weight: 700; }
#plan-rows { display: flex; flex-direction: column; max-height: 360px; overflow-y: auto; }
.plan-row { display: flex; align-items: center; gap: 10px; padding: 8px 4px; border-radius: 7px; }
.plan-row:hover { background: var(--surface-2); }
.plan-category { width: 44px; font-family: var(--font-mono); font-size: 11px; font-weight: 700; color: var(--faint); }
.plan-name { flex: 1; min-width: 0; font-size: 12.5px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.plan-price { font-family: var(--font-mono); font-size: 12px; font-weight: 600; }
.plan-vor-val { font-family: var(--font-mono); font-size: 11px; color: var(--faint); width: 56px; text-align: right; }
```

- [ ] **Step 4: Verify with a syntax check**

Run: `node --check src/ffdo/web/board/board.js` (if `node` is unavailable, carefully re-read the diff instead — there is no JS test runner in this repo).

Expected: no output (success) from `node --check`, or a careful manual read confirming valid syntax.

- [ ] **Step 5: Commit**

```bash
git add src/ffdo/web/board/index.html src/ffdo/web/board/board.js src/ffdo/web/board/board.css
git commit -m "feat: add Optimal Plan panel to the auction board"
```

---

### Task 6: Manual browser verification

**Files:** none (verification only)

**Interfaces:**
- Consumes: the running app at `http://localhost:8000`, backed by Tasks 1-5's changes.

- [ ] **Step 1: Start the dev server**

Run: `uv run uvicorn ffdo.api.app:app --port 8000` (background/long-running).

- [ ] **Step 2: Open the board and locate the Optimal Plan panel**

Navigate to `http://localhost:8000/board/`. The sidebar should show a new "Optimal plan" card below Pick history, with a VOR/spent/left totals line and one row per roster slot (category, target player name in position-colored text, target price, VOR).

- [ ] **Step 3: Verify the numbers look sane**

Confirm: every slot shows a real player name (no blanks, no `undefined`); `total_plan_cost` plus `dollars_left_after_plan` roughly equals your remaining budget; `total_plan_vor` is a plausible sum of the dedicated+FLEX rows' VOR values (BENCH rows excluded from that total).

- [ ] **Step 4: Check the browser console for errors**

Confirm no JS errors (e.g. `Cannot read properties of undefined`).

- [ ] **Step 5: Stop the dev server**

No commit for this task — manual verification only.
