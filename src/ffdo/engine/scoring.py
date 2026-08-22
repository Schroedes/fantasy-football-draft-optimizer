"""Recompute fantasy points from component stats.

Sleeper's precomputed pts_* fields are NOT stable across seasons -- the
half-PPR preset counted `fum` at -1 in 2021 and stopped by 2023. Component
stats are raw facts and do not drift, so everything is rescored from them.
"""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.constants import is_offense_scoring_key


def score_stats(stats: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Total offensive fantasy points for `stats` under `weights`.

    Scoring keys that only apply to defensive or kicking units are ignored,
    so a league's DEF/K rules never leak into a skill player's total.
    """
    return sum(
        float(stats.get(key, 0.0)) * float(weight)
        for key, weight in weights.items()
        if is_offense_scoring_key(key)
    )
