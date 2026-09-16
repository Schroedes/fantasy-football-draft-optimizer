"""ESPN's current-week matchup: pairing + each side's per-starter point
total. ESPN's `appliedTotal` is already fully-scored fantasy points under
the league's own real rules (statSourceId 0 = live/final actual, 1 =
pre-game projection) -- no re-derivation via this project's own scoring
engine needed, unlike Sleeper's raw stat lines.

Verified live 2026-09-16: `schedule[].{home,away}.rosterForCurrentScoringPeriod
.entries` carries per-player `appliedTotal` but no player id at all,
while `teams[].roster.entries` (the same combined mRoster+mMatchupScore
response) carries `playerId` but no points. The two arrays were
confirmed to share the exact same per-team entry order (identical
`lineupSlotId` sequence, identical length) -- undocumented behavior of
this unofficial API, not a guaranteed contract, so this zips them
positionally rather than matching any other way. Revisit if a mismatch
is ever observed."""

from __future__ import annotations

from dataclasses import dataclass, field

from ffdo.ingest.espn.client import BASE, EspnClient
from ffdo.ingest.espn.crosswalk import Crosswalk

_BENCH_SLOTS = {20, 21}   # BN, IR -- same convention as ingest.espn.rosters


@dataclass(frozen=True, slots=True)
class CurrentWeekMatchup:
    opponent_roster_id: int
    your_starter_points: dict[str, float] = field(default_factory=dict)
    opponent_starter_points: dict[str, float] = field(default_factory=dict)


def _starter_points(
    roster_entries: list[dict], score_entries: list[dict], crosswalk: Crosswalk,
) -> dict[str, float]:
    points: dict[str, float] = {}
    for r_entry, s_entry in zip(roster_entries, score_entries):
        if r_entry.get("lineupSlotId") in _BENCH_SLOTS:
            continue
        sleeper_id = crosswalk.espn_to_sleeper.get(str(r_entry.get("playerId")))
        if sleeper_id is None:
            continue
        stats = ((s_entry.get("playerPoolEntry") or {}).get("player") or {}).get("stats") or []
        actual = next((s for s in stats if s.get("statSourceId") == 0), None)
        proj = next((s for s in stats if s.get("statSourceId") == 1), None)
        best = actual or proj
        points[sleeper_id] = float(best.get("appliedTotal") or 0.0) if best else 0.0
    return points


def fetch(
    espn: EspnClient, league_id: str, season: int, crosswalk: Crosswalk, roster_id: int,
) -> CurrentWeekMatchup | None:
    raw = espn.get_json(
        f"{BASE}/seasons/{season}/segments/0/leagues/{league_id}"
        "?view=mRoster&view=mMatchupScore")

    current_period = int((raw.get("status") or {}).get("currentMatchupPeriod") or 0)
    matchup = next(
        (m for m in raw.get("schedule") or []
         if m.get("matchupPeriodId") == current_period
         and roster_id in ((m.get("home") or {}).get("teamId"), (m.get("away") or {}).get("teamId"))),
        None)
    if matchup is None:
        return None   # bye week, or no schedule data for the current period yet

    home, away = matchup["home"], matchup["away"]
    you_side, opp_side = (home, away) if home["teamId"] == roster_id else (away, home)
    opponent_roster_id = opp_side["teamId"]

    roster_by_team = {t["id"]: (t.get("roster") or {}).get("entries") or []
                      for t in raw.get("teams") or []}

    def _side(side: dict) -> dict[str, float]:
        roster_entries = roster_by_team.get(side["teamId"]) or []
        score_entries = (side.get("rosterForCurrentScoringPeriod") or {}).get("entries") or []
        return _starter_points(roster_entries, score_entries, crosswalk)

    return CurrentWeekMatchup(
        opponent_roster_id=opponent_roster_id,
        your_starter_points=_side(you_side),
        opponent_starter_points=_side(opp_side),
    )
