"""Simulate the rest of a snake draft forward to estimate your best
achievable team, accounting for who's likely to survive to each of your
future picks."""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.constants import OFFENSE_POSITIONS
from ffdo.domain.models import DraftState, ValuedPlayer
from ffdo.engine.replacement import FLEX_ELIGIBILITY


def _your_draft_slot(state: DraftState, roster_id: int | None) -> int | None:
    """Your seat, read off any pick you've already made. None if you
    haven't picked yet (or roster_id is unset) -- there's no other
    signal for which seat is yours before that."""
    if roster_id is None:
        return None
    return next((p.draft_slot for p in state.picks if p.roster_id == roster_id), None)


def _pick_no_for(round_no: int, draft_slot: int, num_teams: int) -> int:
    pick_in_round = draft_slot if round_no % 2 == 1 else num_teams - draft_slot + 1
    return (round_no - 1) * num_teams + pick_in_round


def _slot_for_pick(pick_no: int, num_teams: int) -> int:
    round_no = (pick_no - 1) // num_teams + 1
    pick_in_round = (pick_no - 1) % num_teams + 1
    return pick_in_round if round_no % 2 == 1 else num_teams - pick_in_round + 1


def _need_weights(sim_roster: Mapping[str, ValuedPlayer], league) -> dict[str, float]:
    """Cheap stand-in for "do I still need this position": full weight
    while a dedicated starting slot is open, reduced weight once only
    FLEX-eligible room remains, low (bench-only) weight otherwise. Covers
    every position this league actually rosters -- not hardcoded to
    OFFENSE_POSITIONS -- so DEF/K (real dedicated slots, never
    flex-eligible) get weighted the same way a real offense position
    does, rather than being silently unpickable (they score `vor * 0.0`
    forever if absent from this dict, since the caller looks them up via
    `weights.get(position, 0.0)`). Not a replacement for
    roster.marginal_lineup_values -- that still scores each trial's FINAL
    roster (see Task 3's simulate_snake_plan); this only steers the
    in-simulation pick, where the exact version is too expensive to run
    at every pick of every trial.
    """
    pos_counts: dict[str, int] = {}
    for vp in sim_roster.values():
        pos_counts[vp.profile.position] = pos_counts.get(vp.profile.position, 0) + 1

    # Every literal position this league has a dedicated slot for.
    # OFFENSE_POSITIONS is always included even at zero dedicated slots
    # (e.g. no dedicated QB slot, QB only via superflex), so those
    # positions still get a real bench-tier weight instead of being
    # absent from the dict entirely.
    dedicated_positions = frozenset(
        slot for slot in league.roster_positions
        if slot not in FLEX_ELIGIBILITY and slot != "BN"
    ) | OFFENSE_POSITIONS
    dedicated_counts = {pos: league.roster_positions.count(pos) for pos in dedicated_positions}

    flex_positions = frozenset(
        pos for slot in league.roster_positions if slot in FLEX_ELIGIBILITY
        for pos in FLEX_ELIGIBILITY[slot]
    )
    flex_total = sum(1 for slot in league.roster_positions if slot in FLEX_ELIGIBILITY)
    flex_used = sum(max(0, pos_counts.get(pos, 0) - dedicated_counts.get(pos, 0))
                    for pos in flex_positions)
    flex_open = flex_total - flex_used

    weights: dict[str, float] = {}
    for pos in dedicated_positions:
        dedicated_open = dedicated_counts[pos] - min(pos_counts.get(pos, 0), dedicated_counts[pos])
        if dedicated_open > 0:
            weights[pos] = 1.0
        elif pos in flex_positions and flex_open > 0:
            weights[pos] = 0.85
        else:
            weights[pos] = 0.15
    return weights
