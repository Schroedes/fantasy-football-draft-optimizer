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

    # The cheapest any player can actually cost right now -- `_price_of`
    # floors every price at `MIN_BID * factor` (never below MIN_BID
    # itself), so in an inflated market (factor > 1) the true floor is
    # higher than the flat $1 constant. Reserving at the flat constant
    # under-reserves, letting an early pick overspend and silently
    # starving every slot after it once the remaining budget window
    # drops below what any real candidate could ever cost.
    true_min_price = MIN_BID * max(1.0, factor)

    # Roster slots that exist (e.g. K, DEF, IR) but that `needs` doesn't
    # model at all -- the optimizer never plans to fill them, but they
    # still need at least $1 each reserved so the plan stays executable.
    your_picks_count = sum(needs.drafted_count.values()) + needs.undetermined
    non_planned_slots = max(0, league.roster_size - your_picks_count - total_slots_left)

    plan: list[dict] = []
    used_ids: set[str] = set()

    for vp in available:
        if total_slots_left == 0:
            break
        pos = vp.profile.position
        if remaining_cap.get(pos, 0) <= 0:
            continue

        price = _price_of(vp, baseline, factor)
        reserve_for_others = true_min_price * (total_slots_left - 1 + non_planned_slots)
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
    # Totals are computed from the unrounded per-slot values so they stay
    # exact; only the output copy of each slot is rounded for display, so
    # earlier comparisons (budget/cap/VOR-gain checks) that ran against
    # these dicts during `_greedy_fill`/`_refine`/`_reassign_within_plan`
    # are unaffected by display rounding introduced here.
    total_cost = sum(s["target_price"] for s in plan)
    total_vor = sum(s["vor"] for s in plan if s["type"] in ("dedicated", "flex"))
    rounded_slots = [
        {**s, "target_price": round(s["target_price"], 1), "vor": round(s["vor"], 1)}
        for s in plan
    ]
    return {
        "slots": rounded_slots,
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

                # Only "dedicated"/"flex" slots count toward total_plan_vor
                # (see `_to_output`) -- weight each side's VOR contribution
                # by whether its slot is actually counted, so a swap that
                # raises combined raw VOR by shoving a good player onto
                # BENCH is never mistaken for an improvement.
                weight_a = 1.0 if slot_a["type"] in ("dedicated", "flex") else 0.0
                weight_b = 1.0 if slot_b["type"] in ("dedicated", "flex") else 0.0
                current_price = slot_a["target_price"] + slot_b["target_price"]
                current_vor = weight_a * slot_a["vor"] + weight_b * slot_b["vor"]

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
                        vor_gain = weight_a * ca.vor + weight_b * cb.vor - current_vor
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


def _is_legal_position(pos: str, slot: dict, flex_positions: frozenset[str]) -> bool:
    """Position-string counterpart to `_is_legal`, for checking whether an
    already-planned occupant (which we only have as a position string on
    another slot, not a `ValuedPlayer`) is legal for a *different* slot."""
    if slot["type"] == "dedicated":
        return pos == slot["category"]
    if slot["type"] == "flex":
        return pos in flex_positions
    return True  # bench: any offense position is legal


def _reassign_within_plan(plan: list[dict], needs: RosterNeeds) -> list[dict]:
    """Second local-search pass: swap which slot two ALREADY-PLANNED
    players occupy, when doing so raises the counted (dedicated + flex)
    VOR sum.

    `_refine`'s candidate pools are built from `available` players `not
    in used_ids` -- by construction, every player already placed in the
    plan is excluded from ever being a "candidate" for another slot. So
    `_refine` can never fix "a high-VOR player is stuck on BENCH while a
    much-lower-VOR player of the same/mutually-legal position sits in a
    counted slot" -- both players are already `used_ids` members. This
    pass is the only thing that reaches that move.

    A pure reassignment swaps two players who are BOTH already in the
    plan: same two players, same two prices, just relabeled slots -- so
    total plan cost is unchanged (no budget check needed) and the
    multiset of positions in the plan is unchanged (no cap check needed
    either, unlike `_refine`'s swap loop).
    """
    for _ in range(MAX_SWAP_ITERATIONS):
        improved = False
        for i, slot_a in enumerate(plan):
            weight_a = 1.0 if slot_a["type"] in ("dedicated", "flex") else 0.0
            for j, slot_b in enumerate(plan):
                if j <= i:
                    continue
                weight_b = 1.0 if slot_b["type"] in ("dedicated", "flex") else 0.0
                if weight_a == weight_b:
                    # Gain = (weight_a - weight_b) * (vor_b - vor_a); with
                    # equal weights it's always 0, so no swap between two
                    # counted slots (or two bench slots) can ever help.
                    continue
                if not (_is_legal_position(slot_a["eligible_position"], slot_b, needs.flex_positions)
                        and _is_legal_position(slot_b["eligible_position"], slot_a, needs.flex_positions)):
                    continue

                current = weight_a * slot_a["vor"] + weight_b * slot_b["vor"]
                swapped = weight_a * slot_b["vor"] + weight_b * slot_a["vor"]
                if swapped > current:
                    for key in ("player_id", "name", "target_price", "vor", "eligible_position"):
                        slot_a[key], slot_b[key] = slot_b[key], slot_a[key]
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
    an early expensive pick blocked a better later combination, followed
    by `_reassign_within_plan`, which catches the narrower case of two
    already-placed players sitting in the wrong (counted vs. bench)
    slots relative to each other -- a move `_refine` structurally cannot
    reach, since it only considers players outside the plan as
    candidates. It runs last since a swap can surface a reassignable
    pair that didn't exist before it.
    """
    plan, available, used_ids, needs, caps = _greedy_fill(
        valued, baseline, factor, state, league, roster_id, your_dollars_left)
    plan = _refine(plan, available, used_ids, baseline, factor, needs, caps)
    plan = _reassign_within_plan(plan, needs)
    return _to_output(plan, your_dollars_left)
