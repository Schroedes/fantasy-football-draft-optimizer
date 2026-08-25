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
