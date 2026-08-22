"""Greedy slot-fill shared by league-wide replacement level and single-team
lineup value (see engine/roster.py).

Every starting lineup in the league is filled greedily by value; replacement
level at a position is the best player who did not make one. This handles
FLEX allocation and superflex with no special cases -- the only input that
changes is `roster_positions`.
"""

from __future__ import annotations

from collections.abc import Mapping

FLEX_ELIGIBILITY: dict[str, frozenset[str]] = {
    "FLEX": frozenset({"RB", "WR", "TE"}),
    "WRRB_FLEX": frozenset({"RB", "WR"}),
    "REC_FLEX": frozenset({"WR", "TE"}),
    "SUPER_FLEX": frozenset({"QB", "RB", "WR", "TE"}),
}


def rank_by_position(
    values: Mapping[str, float],
    positions: Mapping[str, str],
) -> dict[str, list[tuple[float, str]]]:
    """Groups `values` by position, each list sorted descending."""
    ranked: dict[str, list[tuple[float, str]]] = {}
    for player_id, val in values.items():
        pos = positions.get(player_id)
        if pos is None:
            continue
        ranked.setdefault(pos, []).append((val, player_id))
    for pos in ranked:
        ranked[pos].sort(reverse=True)
    return ranked


def greedy_fill_slots(
    ranked: Mapping[str, list[tuple[float, str]]],
    positions: Mapping[str, str],
    slots: tuple[str, ...],
    iterations: int,
) -> tuple[set[str], dict[str, int]]:
    """Fills `slots`, `iterations` passes through, from pools pre-sorted
    descending by whatever value ranked them.

    Dedicated slots claim by-position first, since they have no discretion;
    FLEX-eligible slots then take the best remaining eligible player. That
    cross-position comparison is only meaningful when every pool is ranked
    on the same value scale -- league-wide replacement (`replacement_levels`
    below) ranks by raw points, since it is answering "who gets rostered at
    all"; single-team lineup value (engine/roster.py) ranks by VOR instead,
    since it is answering "which of this team's players maximizes value
    above replacement," and raw points alone aren't comparable across
    positions with different replacement baselines.

    Returns the set of player_ids that filled a slot, and the final
    per-position cursor (how deep into each pool `iterations` passes
    reached).
    """
    cursor: dict[str, int] = dict.fromkeys(ranked, 0)
    taken: set[str] = set()

    dedicated = [s for s in slots if s not in FLEX_ELIGIBILITY]
    flex = [s for s in slots if s in FLEX_ELIGIBILITY]

    for slot in dedicated:
        for _ in range(iterations):
            pool = ranked.get(slot, [])
            if cursor.get(slot, 0) < len(pool):
                taken.add(pool[cursor[slot]][1])
                cursor[slot] += 1

    for slot in flex:
        eligible = FLEX_ELIGIBILITY[slot]
        for _ in range(iterations):
            best: tuple[float, str] | None = None
            for pos in eligible:
                pool = ranked.get(pos, [])
                idx = cursor.get(pos, 0)
                if idx < len(pool) and (best is None or pool[idx][0] > best[0]):
                    best = pool[idx]
            if best is None:
                break
            pos = positions[best[1]]
            taken.add(best[1])
            cursor[pos] += 1

    return taken, cursor


def replacement_levels(
    points: Mapping[str, float],
    positions: Mapping[str, str],
    league,
) -> dict[str, float]:
    slots = league.starting_slots
    ranked = rank_by_position(points, positions)
    _, cursor = greedy_fill_slots(ranked, positions, slots, league.num_teams)

    rostered_positions = {p for s in slots
                          for p in (FLEX_ELIGIBILITY.get(s) or {s})}

    levels: dict[str, float] = {}
    for pos in rostered_positions:
        pool = ranked.get(pos, [])
        idx = cursor.get(pos, 0)
        levels[pos] = pool[idx][0] if idx < len(pool) else (
            pool[-1][0] if pool else 0.0)
    return levels
