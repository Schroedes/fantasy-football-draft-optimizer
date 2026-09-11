"""Season-to-date actual fantasy points per player, from a Sleeper
league's weekly matchups. `players_points` is already scored under the
league's own settings, so no re-scoring here. Shared with sub-projects
#3 and #7."""

from __future__ import annotations

from ffdo.ingest.client import V1, SleeperClient


def points_so_far(
    sleeper: SleeperClient, league_id: str, through_week: int,
) -> dict[str, float]:
    banked: dict[str, float] = {}
    for week in range(1, max(0, through_week) + 1):
        rows = sleeper.get_json(f"{V1}/league/{league_id}/matchups/{week}")
        for row in rows or []:
            for pid, pts in (row.get("players_points") or {}).items():
                banked[str(pid)] = banked.get(str(pid), 0.0) + float(pts)
    return banked
