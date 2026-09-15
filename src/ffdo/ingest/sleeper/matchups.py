"""This week's matchup pairing (who's playing whom) and each player's
LIVE, currently-accrued fantasy points for the CURRENT week only --
distinct from `ffdo.ingest.actuals.points_so_far`, which banks only
FINAL weeks (`nfl.week - 1` and earlier) for season-to-date totals and
discards everything except `players_points`. Sleeper's own
`matchups/{week}` row already carries both `matchup_id` (pairing) and
`players_points` (live, updating through game day) -- one HTTP call
covers both, so `current_matchup` and `live_points` are not two separate
fetches."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from ffdo.ingest.client import V1, SleeperClient


@dataclass(frozen=True, slots=True)
class CurrentWeekMatchups:
    pairing: dict[int, int] = field(default_factory=dict)
    live_points: dict[str, float] = field(default_factory=dict)


def fetch(sleeper: SleeperClient, league_id: str, week: int) -> CurrentWeekMatchups:
    try:
        rows = sleeper.get_json(f"{V1}/league/{league_id}/matchups/{week}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return CurrentWeekMatchups()
        raise

    by_matchup: dict[int, list[int]] = {}
    live_points: dict[str, float] = {}
    for row in rows or []:
        roster_id = row.get("roster_id")
        matchup_id = row.get("matchup_id")
        if roster_id is not None and matchup_id is not None:
            by_matchup.setdefault(matchup_id, []).append(roster_id)
        for pid, pts in (row.get("players_points") or {}).items():
            live_points[str(pid)] = float(pts)

    pairing: dict[int, int] = {}
    for roster_ids in by_matchup.values():
        if len(roster_ids) == 2:
            a, b = roster_ids
            pairing[a] = b
            pairing[b] = a
        # len == 1 is a bye (odd team count); anything else is malformed
        # data -- either way, no pairing entry, which the caller reads as
        # "no matchup this week" rather than an error.

    return CurrentWeekMatchups(pairing=pairing, live_points=live_points)
