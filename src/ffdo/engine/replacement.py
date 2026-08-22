"""Replacement level derived from the league's actual starting requirements.

Every starting lineup in the league is filled greedily by projected points;
replacement level at a position is the best player who did not make one. This
handles FLEX allocation and superflex with no special cases -- the only input
that changes is `roster_positions`.
"""

from __future__ import annotations

from collections.abc import Mapping

FLEX_ELIGIBILITY: dict[str, frozenset[str]] = {
    "FLEX": frozenset({"RB", "WR", "TE"}),
    "WRRB_FLEX": frozenset({"RB", "WR"}),
    "REC_FLEX": frozenset({"WR", "TE"}),
    "SUPER_FLEX": frozenset({"QB", "RB", "WR", "TE"}),
}


def replacement_levels(
    points: Mapping[str, float],
    positions: Mapping[str, str],
    league,
) -> dict[str, float]:
    slots = league.starting_slots
    ranked: dict[str, list[tuple[float, str]]] = {}
    for player_id, pts in points.items():
        pos = positions.get(player_id)
        if pos is None:
            continue
        ranked.setdefault(pos, []).append((pts, player_id))
    for pos in ranked:
        ranked[pos].sort(reverse=True)

    cursor = dict.fromkeys(ranked, 0)
    taken: set[str] = set()

    # Dedicated slots first: they have no discretion, so they must claim their
    # players before flex slots choose from what is left.
    dedicated = [s for s in slots if s not in FLEX_ELIGIBILITY]
    flex = [s for s in slots if s in FLEX_ELIGIBILITY]

    for slot in dedicated:
        for _ in range(league.num_teams):
            pool = ranked.get(slot, [])
            if cursor.get(slot, 0) < len(pool):
                taken.add(pool[cursor[slot]][1])
                cursor[slot] += 1

    for slot in flex:
        eligible = FLEX_ELIGIBILITY[slot]
        for _ in range(league.num_teams):
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

    rostered_positions = {p for s in slots
                          for p in (FLEX_ELIGIBILITY.get(s) or {s})}

    levels: dict[str, float] = {}
    for pos in rostered_positions:
        pool = ranked.get(pos, [])
        idx = cursor.get(pos, 0)
        levels[pos] = pool[idx][0] if idx < len(pool) else (
            pool[-1][0] if pool else 0.0)
    return levels
