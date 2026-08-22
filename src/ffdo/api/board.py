"""Shapes engine output into the JSON the board renders."""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.models import DraftState, ValuedPlayer
from ffdo.engine import auction


def build_auction_board(
    league,
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    baseline: Mapping[str, float],
    *,
    roster_id: int | None = None,
) -> dict:
    factor = auction.inflation_factor(baseline, state, league)
    drafted = state.drafted_player_ids()
    spent = state.spent_by_roster()

    # "Your" roster state, for the max-bid ceiling and budget strip. When
    # `roster_id` is unknown (FFDO_ROSTER_ID unset), fall back to a fresh
    # 0-spent/0-filled roster rather than guessing -- an honestly-labeled
    # "as if starting from scratch" number beats a silently wrong one.
    your_spent = spent.get(roster_id, 0) if roster_id is not None else 0
    your_slots_filled = (
        sum(1 for p in state.picks if p.roster_id == roster_id)
        if roster_id is not None else 0)
    your_max_bid = auction.max_bid(your_spent, your_slots_filled, league)
    your_slots_left = max(0, league.roster_size - your_slots_filled)
    your_dollars_left = league.budget - your_spent

    by_position = auction.positional_budget(
        valued, baseline, factor, state, league, roster_id, your_dollars_left)

    total_slots = league.num_teams * league.roster_size
    slots_remaining_room = max(1, total_slots - len(drafted))
    league_dollars_per_slot = (
        (league.num_teams * league.budget - sum(spent.values()))
        / slots_remaining_room)
    your_dollars_per_slot = (
        your_dollars_left / your_slots_left if your_slots_left > 0 else 0.0)

    rows = []
    for pid, vp in valued.items():
        base = baseline.get(pid, 1.0)
        # A $1-baseline player must never display a sub-$1 price -- $1 is
        # the legal minimum bid, so the model cannot recommend a number the
        # room can't act on, however low inflation drops.
        adjusted = max(auction.MIN_BID, base * factor)
        rows.append({
            "player_id": pid,
            "name": vp.profile.full_name,
            "position": vp.profile.position,
            "team": vp.profile.team,
            "age": vp.profile.age,
            "vor": round(vp.vor, 1),
            "tier": vp.tier,
            "baseline": round(base, 1),
            "adjusted": round(adjusted, 1),
            "max_bid": your_max_bid,
            "drafted": pid in drafted,
        })
    rows.sort(key=lambda r: r["vor"], reverse=True)

    return {
        "format": "auction",
        "inflation": round(factor, 3),
        "budget": {
            "total": league.num_teams * league.budget,
            "spent": sum(spent.values()),
            "by_roster": spent,
            "your_roster_id": roster_id,
            "your_spent": your_spent,
            "your_slots_left": your_slots_left,
            "your_dollars_left": your_dollars_left,
            "your_dollars_per_slot": round(your_dollars_per_slot, 1),
            "league_dollars_per_slot": round(league_dollars_per_slot, 1),
            "by_position": by_position,
        },
        "picks_made": len(state.picks),
        "players": rows,
    }


def build_snake_board(
    league,
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    survival: Mapping[str, float],
    cost_of_waiting: Mapping[str, Mapping[str, float]],
) -> dict:
    drafted = state.drafted_player_ids()
    rows = [
        {
            "player_id": pid,
            "name": vp.profile.full_name,
            "position": vp.profile.position,
            "team": vp.profile.team,
            "age": vp.profile.age,
            "vor": round(vp.vor, 1),
            "tier": vp.tier,
            # `simulate_survival` only returns entries for players who carry
            # an ADP; a player absent from it has no ADP, not a 0% chance
            # of survival. Defaulting to 0.0 there previously rendered
            # "definitely gone" for players who are actually near-certain
            # to still be on the board -- backwards. Absence means no
            # signal either way, so default to certain survival (1.0).
            "survival": round(survival.get(pid, 1.0), 3),
            "drafted": pid in drafted,
        }
        for pid, vp in valued.items()
    ]
    rows.sort(key=lambda r: r["vor"], reverse=True)
    return {
        "format": "snake",
        "cost_of_waiting": dict(cost_of_waiting),
        "picks_made": len(state.picks),
        "players": rows,
    }
