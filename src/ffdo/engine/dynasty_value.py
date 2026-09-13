"""Real, multi-year dynasty value -- replaces dynasty_curve.py's coarse,
hand-authored per-position multiplier.

Projects a player's blended current-year value forward `horizon_years`
using the empirically-fit per-position age-delta curve
(engine.adjustments.fit_age_curve, repurposed here from its ORIGINAL,
rejected use as a redraft points-adjustment -- see
docs/superpowers/specs/2026-09-12-valuation-model-design.md §0 for why a
"no" for that question doesn't invalidate the same data for THIS one: not
"does this improve this year's rank prediction" but "how much future
production does this asset have left"). Discounts each future year by a
fixed per-year rate and a durability-based survival fraction, then
collapses the whole multi-year picture back into a single season-scale
annuity-equivalent value so it slots into engine.vor.compute exactly like
a redraft value does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from ffdo.domain.constants import SEASON_LENGTH
from ffdo.domain.models import PlayerProfile, SeasonStatLine
from ffdo.engine.adjustments import expected_games_missed

HORIZON_YEARS: Final[int] = 5
DISCOUNT_RATE: Final[float] = 0.15  # per year; a common dynasty-community
                                     # convention, NOT backtested -- no clean
                                     # out-of-sample target exists for "was
                                     # this dynasty valuation right" the way
                                     # ADP-vs-actual-points exists for redraft.


def annuity_value(
    current_full: float,
    profile: PlayerProfile,
    history: Sequence[SeasonStatLine],
    age_curve: Mapping[str, Mapping[int, float]],
    *,
    current_season: int,
    horizon_years: int = HORIZON_YEARS,
    discount_rate: float = DISCOUNT_RATE,
) -> float:
    if profile.age is None:
        return current_full

    season_length = SEASON_LENGTH[current_season]
    missed = expected_games_missed(history, profile.position, current_season=current_season)
    survival = 1.0 - (missed / season_length)

    position_curve = age_curve.get(profile.position, {})
    # Year 0 (this season, undiscounted) is folded into the SAME weighted
    # average as every projected year -- see this module's docstring and
    # the plan's Task 4 note for why summing year 0 on top of an average
    # of the other years (instead of folding it in) silently doubles the
    # result whenever `age_curve` has no data for this player's future ages.
    total_value = current_full
    total_weight = 1.0
    cumulative_delta = 0.0
    for year in range(1, horizon_years + 1):
        # `age_curve[A]` is the mean points-PER-GAME delta from age A to
        # A+1 (see adjustments.fit_age_curve's docstring). Year 1 (one
        # season from now) is the A -> A+1 transition, so it reads
        # `curve[age + year - 1]`, not `curve[age + year]` -- the latter
        # silently skips the player's very next transition and reaches
        # one year past `horizon_years` instead. And since `current_full`
        # is a season TOTAL, not a per-game rate, the delta must be scaled
        # by `season_length` before being added -- the same ppg-to-season
        # conversion `adjustments.build`'s age entry already applies
        # (`delta_ppg * length`) -- or the age effect ends up roughly
        # `season_length`-times too small.
        delta_ppg = position_curve.get(profile.age + year - 1, 0.0)
        cumulative_delta += delta_ppg * season_length
        year_value = max(0.0, current_full + cumulative_delta)
        weight = survival / (1.0 + discount_rate) ** year
        total_value += year_value * weight
        total_weight += weight

    return total_value / total_weight
