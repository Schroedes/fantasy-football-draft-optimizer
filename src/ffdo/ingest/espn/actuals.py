"""Season-to-date actual points per player from an already-fetched
mRoster payload -- no extra HTTP call. `statSourceId == 0` is the actual
stat line (1 is a projection); `scoringPeriodId` is the NFL week.
UNVERIFIED against a live league."""

from __future__ import annotations

from ffdo.ingest.espn.crosswalk import Crosswalk


def points_so_far(
    mroster_raw: dict, crosswalk: Crosswalk, through_week: int,
) -> dict[str, float]:
    banked: dict[str, float] = {}
    for team in mroster_raw.get("teams") or []:
        for entry in ((team.get("roster") or {}).get("entries") or []):
            sleeper_id = crosswalk.espn_to_sleeper.get(str(entry.get("playerId")))
            if sleeper_id is None:
                continue
            stats = (((entry.get("playerPoolEntry") or {}).get("player") or {}).get("stats") or [])
            for s in stats:
                if s.get("statSourceId") != 0:
                    continue
                if int(s.get("scoringPeriodId") or 0) > max(0, through_week):
                    continue
                banked[sleeper_id] = banked.get(sleeper_id, 0.0) + float(s.get("appliedTotal") or 0.0)
    return banked
