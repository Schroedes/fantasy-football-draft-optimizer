"""Positional scarcity: how much a position's VOR should scale up when its
replacement-level cliff (the points gap immediately below replacement) is
steep -- there's no comparable fallback once that player is gone, unlike a
position with plenty of similarly-valued depth just below the cutoff.

Both functions are pure math over an already-ranked player pool -- no I/O,
no league-object coupling beyond what the caller (engine.vor.compute)
already has in hand from engine.replacement.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

CLIFF_DEPTH: Final[int] = 3


def positional_cliff(
    ranked: Mapping[str, list[tuple[float, str]]],
    levels: Mapping[str, float],
) -> dict[str, float]:
    """Points gap between each position's replacement level and the mean of
    the CLIFF_DEPTH next-lowest-valued players below it. Always >= 0, since
    only players with a value strictly less than `level` are counted --
    this is deliberate: `ranked[pos]` can have multiple players tied
    exactly at the replacement value (common at deep positions like
    K/DEF/TE, where many players carry the same 0.0 projection), and a
    naive positional slice below wherever the tie happens to sit in the
    sorted list can pull in another tied (not actually lower) player,
    understating the cliff. Filtering on value instead of position makes
    the result correct regardless of how many players tie at `level`.
    A position with fewer than CLIFF_DEPTH players strictly below
    replacement (a thin pool) averages over however many remain; a
    position with none strictly below gets 0.0 -- there is nothing to
    fall off a cliff into.
    """
    cliffs: dict[str, float] = {}
    for pos, level in levels.items():
        pool = ranked.get(pos, [])
        below = [v for v, _ in pool if v < level][:CLIFF_DEPTH]
        cliffs[pos] = (level - sum(below) / len(below)) if below else 0.0
    return cliffs


def scarcity_multiplier(cliff: Mapping[str, float], strength: float) -> dict[str, float]:
    """1.0 + strength * (this position's cliff / the steepest cliff of any
    position). strength=0.0 always returns every multiplier as 1.0 -- a
    pure no-op, matching SCARCITY_STRENGTH's default in engine.vor until a
    real backtest (scripts/backtest_scarcity.py) promotes it.
    """
    if not cliff:
        return {}
    max_cliff = max(cliff.values())
    if max_cliff <= 0.0:
        return dict.fromkeys(cliff, 1.0)
    return {pos: 1.0 + strength * (c / max_cliff) for pos, c in cliff.items()}
