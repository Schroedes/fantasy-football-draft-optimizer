"""Auction valuation: baseline dollars, live inflation, and max bid."""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.models import DraftState, ValuedPlayer

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
