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

    A key is summed if any of the three classifiers recognizes it. This has
    no position parameter -- it relies on Sleeper's raw stat dicts being
    largely position-specific (a WR's stats essentially never contain
    `sack`, a DEF's essentially never contain `rec_yd`). That is a verified
    tendency against real data, NOT an absolute guarantee: kickers'
    real stat lines can carry fake-FG-trick-play keys like `rush_yd`/
    `rush_att`/`pass_att`, and several defense/kicking-vocabulary keys
    (`def_kr_td`, `pr_td`, `fum_rec_td`, `pass_int_td`) really do appear on
    real rostered offensive skill-position players in Sleeper's own data,
    not just on team-DEF/K entities. Where verification found a key that
    doesn't hold the tendency, the classifiers exclude it outright rather
    than risk crediting the wrong player -- see the `_DEFENSIVE_ONLY` and
    `_DEFENSE_BARE` exclusion comments in `domain.constants` for the actual
    belt-and-suspenders mechanism this function leans on.
    """
    return sum(
        float(stats.get(key, 0.0)) * float(weight)
        for key, weight in weights.items()
        if is_offense_scoring_key(key)
        or is_defense_scoring_key(key)
        or is_kicking_scoring_key(key)
    )
