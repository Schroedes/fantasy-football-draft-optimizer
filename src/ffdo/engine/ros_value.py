"""Per-player value for the season screen -- THE SWAPPABLE SEAM.

Sub-project #4 replaced dynasty_curve.py's hand-authored multiplier with a
real, data-driven multi-year model (engine.dynasty_value.annuity_value)
while keeping this function's external call shape backward compatible:
`history`/`age_curve` default to `None`, so every existing redraft/keeper
call site is unaffected.

What it does today:
  current_full = blend(preseason projection, season-to-date pace),
                 weight shifting toward pace as weeks_played grows
  redraft/keeper: value = max(0, current_full - banked)   [rest of season]
  dynasty:        value = dynasty_value.annuity_value(current_full, ...)
                          [a real multi-year, discounted, data-driven model]
Then engine.vor.compute puts it on a value-over-replacement scale.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ffdo.domain.constants import INJURY_OUT_STATUSES
from ffdo.domain.models import (
    PlayerProfile, SeasonProjection, SeasonStatLine, ValuedPlayer,
)
from ffdo.engine import dynasty_value, vor
from ffdo.engine.scoring import score_stats

K = 4  # pace-blend half-life: at weeks_played == K, pace and preseason weigh equally


def _blended_full_season(
    preseason: float, banked: float, weeks_played: int, season_weeks: int,
) -> float:
    if weeks_played <= 0:
        return preseason
    pace_full = (banked / weeks_played) * season_weeks
    w = weeks_played / (weeks_played + K)
    return preseason * (1.0 - w) + pace_full * w


def roster_value(
    player_ids: Iterable[str],
    league,
    *,
    resolved_format: str,
    season_proj: Mapping[str, SeasonProjection],
    profiles: Mapping[str, PlayerProfile],
    actuals: Mapping[str, float],
    weeks_played: int,
    season_weeks: int = 18,
    history: Mapping[str, Sequence[SeasonStatLine]] | None = None,
    age_curve: Mapping[str, Mapping[int, float]] | None = None,
) -> dict[str, ValuedPlayer]:
    is_dynasty = resolved_format == "dynasty"
    value_pts: dict[str, float] = {}

    for pid in player_ids:
        proj = season_proj.get(pid)
        profile = profiles.get(pid)
        if proj is None or profile is None:
            continue

        preseason = score_stats(proj.stats, league.scoring_settings)
        banked = float(actuals.get(pid, 0.0))
        current_full = _blended_full_season(preseason, banked, weeks_played, season_weeks)

        if not profile.active or profile.injury_status in INJURY_OUT_STATUSES:
            value_pts[pid] = 0.0
        elif is_dynasty:
            value_pts[pid] = dynasty_value.annuity_value(
                current_full, profile,
                (history or {}).get(pid, ()), age_curve or {},
                current_season=league.season)
        else:
            value_pts[pid] = max(0.0, current_full - banked)

    return vor.compute(value_pts, profiles, league)
