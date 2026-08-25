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
