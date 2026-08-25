"""Recompute fantasy points from component stats.

Sleeper's precomputed pts_* fields are NOT stable across seasons -- the
half-PPR preset counted `fum` at -1 in 2021 and stopped by 2023. Component
stats are raw facts and do not drift, so everything is rescored from them.
"""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.constants import (
    is_defense_scoring_key, is_kicking_scoring_key, is_offense_scoring_key,
)


def score_stats(stats: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Total fantasy points for `stats` under `weights`, across offense,
    defense, and kicking scoring categories.

    A key is summed if any of the three classifiers recognizes it. This is
    safe with no position awareness because Sleeper's raw stat dicts are
    inherently position-specific -- a WR's stats never contain `sack`, a
    DEF's never contain `rec_yd` -- so the same key can never fire for two
    different positions' players.
    """
    return sum(
        float(stats.get(key, 0.0)) * float(weight)
        for key, weight in weights.items()
        if is_offense_scoring_key(key)
        or is_defense_scoring_key(key)
        or is_kicking_scoring_key(key)
    )
