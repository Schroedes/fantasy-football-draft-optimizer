"""A team's best-current-estimate fantasy score for the live/current
week: a starter's LIVE points once their team's game has locked (already
accruing as the game plays, climbing to the final total once it ends),
else their pre-game weekly projection.

Deliberately NOT `engine.weekly_lineup`'s VOR-scaled per-player values --
those compare players against each other for lineup-optimization
purposes and are not a number a user would recognize as "my team's
score" (a real Sleeper-sized total like 118.4). This sums RAW fantasy
points instead, via the same `score_stats` weekly_lineup.py itself
already uses for the projection half.

Powers the command-center home's per-league matchup row -- see
docs/superpowers/specs/2026-09-14-command-center-home-design.md."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ffdo.domain.models import PlayerProfile, WeeklyProjection
from ffdo.engine.scoring import score_stats


def team_projected_score(
    starter_ids: Iterable[str],
    *,
    weekly_points: Mapping[str, WeeklyProjection],
    live_points: Mapping[str, float],
    profiles: Mapping[str, PlayerProfile],
    locked_teams: frozenset[str],
    scoring_settings: Mapping[str, float],
) -> float:
    total = 0.0
    for pid in starter_ids:
        if pid is None:
            continue
        profile = profiles.get(pid)
        if profile is not None and profile.team in locked_teams:
            total += live_points.get(pid, 0.0)
            continue
        proj = weekly_points.get(pid)
        if proj is not None:
            total += score_stats(proj.stats, scoring_settings)
    return total
