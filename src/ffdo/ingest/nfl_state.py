"""The current NFL week, from Sleeper's /state/nfl. The anchor for
'rest-of-season' math and which matchup week to read. Shared with
sub-project #3 (weekly optimal lineup)."""

from __future__ import annotations

from ffdo.domain.models import NflWeek
from ffdo.ingest.client import V1, SleeperClient


def current_week(sleeper: SleeperClient) -> NflWeek:
    raw = sleeper.get_json(f"{V1}/state/nfl")
    # `week` flips the moment the previous week's games are done (Tuesday
    # morning). `display_week` is Sleeper's own UI hint and lags a day
    # behind that during the Tue/Wed stat-correction window -- preferring
    # it here made `_through_week` in app.py think the just-finished week
    # was still in progress, banking zero points for it until Wednesday.
    week = int(raw.get("week") or raw.get("display_week") or 0)
    season_type = raw.get("season_type") or "regular"
    return NflWeek(
        season=int(raw["season"]),
        week=week,
        season_type=season_type,
        complete=season_type == "post" or week > 18,
    )
