"""Auction valuation: baseline dollars, live inflation, and max bid."""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.constants import OFFENSE_POSITIONS
from ffdo.domain.models import DraftState, ValuedPlayer
from ffdo.engine.replacement import FLEX_ELIGIBILITY

MIN_BID = 1


def baseline_prices(
    valued: Mapping[str, ValuedPlayer],
    league,
) -> dict[str, float]:
    """Fair prices in an efficient market, summing to the league budget.

    Negative VOR is clamped to zero before summing; without that, deep bench
    players deflate the dollar-per-VOR scale for everyone. The $1-per-slot
    floor is reserved up front so the model never produces prices the league
    literally cannot pay.
    """
    total_slots = league.num_teams * league.roster_size
    discretionary = league.num_teams * league.budget - total_slots * MIN_BID

    surplus = sorted(
        ((pid, max(0.0, vp.vor)) for pid, vp in valued.items()),
        key=lambda kv: kv[1], reverse=True,
    )[:total_slots]
    total_vor = sum(v for _, v in surplus)
    per_vor = discretionary / total_vor if total_vor > 0 else 0.0

    return {pid: MIN_BID + max(0.0, vp.vor) * per_vor
            for pid, vp in valued.items()}


def inflation_factor(
    baseline: Mapping[str, float],
    state: DraftState,
    league,
) -> float:
    """Remaining money divided by remaining value.

    Above 1.0 means the room has underspent and everything left will cost more
    than fair. Below 1.0 means bargains are available.
    """
    total_budget = league.num_teams * league.budget
    spent = sum(state.spent_by_roster().values())
    drafted = state.drafted_player_ids()

    remaining_money = total_budget - spent
    total_slots = league.num_teams * league.roster_size
    slots_left = max(1, total_slots - len(drafted))

    remaining = sorted(
        (price for pid, price in baseline.items() if pid not in drafted),
        reverse=True,
    )[:slots_left]
    remaining_value = sum(remaining)
    if remaining_value <= 0:
        return 1.0
    return remaining_money / remaining_value


def max_bid(spent: int, slots_filled: int, league) -> int:
    """The most you can bid and still fill every remaining roster slot."""
    remaining_budget = league.budget - spent
    slots_left = league.roster_size - slots_filled
    if slots_left <= 0:
        return 0
    return max(0, remaining_budget - (slots_left - 1) * MIN_BID)


def positional_budget(
    valued: Mapping[str, ValuedPlayer],
    baseline: Mapping[str, float],
    factor: float,
    state: DraftState,
    league,
    roster_id: int | None,
    your_dollars_left: float,
) -> dict[str, dict[str, float] | float]:
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
    drafted = state.drafted_player_ids()
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
    leftover = sum(max(0, drafted_count[pos] - dedicated_count[pos])
                   for pos in OFFENSE_POSITIONS)

    flex_total = sum(1 for slot in league.roster_positions
                     if slot in FLEX_ELIGIBILITY)
    bench_total = league.roster_positions.count("BN")
    flex_remaining = max(0, flex_total - leftover)
    bench_spill = max(0, leftover - flex_total)
    bench_remaining = max(0, bench_total - bench_spill - undetermined)

    available = [vp for pid, vp in valued.items() if pid not in drafted]

    raw: dict[str, float] = {}
    pos_leftover_pool: dict[str, list[float]] = {}
    for pos in OFFENSE_POSITIONS:
        need = dedicated_need[pos]
        pool = sorted(
            (max(MIN_BID, baseline.get(vp.profile.player_id, 1.0) * factor)
             for vp in available if vp.profile.position == pos),
            reverse=True,
        )
        raw[pos] = sum(pool[:need])
        pos_leftover_pool[pos] = pool[need:]

    flex_positions = {
        pos for slot in league.roster_positions if slot in FLEX_ELIGIBILITY
        for pos in FLEX_ELIGIBILITY[slot]
    }
    flex_candidates = sorted(
        (price for pos in flex_positions for price in pos_leftover_pool.get(pos, [])),
        reverse=True,
    )[:flex_remaining]
    raw_flex = sum(flex_candidates)
    raw_bench = MIN_BID * bench_remaining

    total_raw = sum(raw.values()) + raw_flex + raw_bench
    scale = your_dollars_left / total_raw if total_raw > 0 else 0.0

    out: dict[str, dict[str, float] | float] = {
        pos: {
            "recommended": round(raw[pos] * scale, 1),
            "slots_open": dedicated_need[pos],
        }
        for pos in OFFENSE_POSITIONS
    }
    out["FLEX"] = {
        "recommended": round(raw_flex * scale, 1),
        "slots_open": flex_remaining,
    }
    out["BENCH"] = {
        "recommended": round(raw_bench * scale, 1),
        "slots_open": bench_remaining,
    }
    return out
