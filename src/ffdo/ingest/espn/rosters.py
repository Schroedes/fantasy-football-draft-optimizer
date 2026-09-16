"""ESPN current rosters + standings + week, from mTeam/mRoster/mSettings.
ESPN player ids are crosswalked to Sleeper ids (the valuation source is
Sleeper's player pool regardless of provider). Verified live 2026-09-16
against a real drafted league. Every field access is defensive."""

from __future__ import annotations

from ffdo.domain.models import NflWeek, RosterEntry
from ffdo.ingest.espn.client import BASE, EspnClient
from ffdo.ingest.espn.crosswalk import Crosswalk

_BENCH_SLOTS = {20, 21}   # BN, IR


def fetch(
    espn: EspnClient, league_id: str, season: int, crosswalk: Crosswalk,
) -> tuple[list[RosterEntry], NflWeek, dict]:
    raw = espn.get_json(
        f"{BASE}/seasons/{season}/segments/0/leagues/{league_id}"
        "?view=mTeam&view=mRoster&view=mSettings")

    settings = raw.get("settings") or {}
    # `status` is a sibling of `settings` at the payload's top level, not
    # nested under it -- confirmed live 2026-09-16 (`raw["status"]`, not
    # `raw["settings"]["status"]`). Reading it from the wrong place silently
    # left this at 0 forever, which made every ESPN league's season-to-date
    # actuals compute as "no weeks final yet," always, regardless of the
    # real week.
    current_period = int((raw.get("status") or {}).get("currentMatchupPeriod") or 0)
    period_count = int((settings.get("scheduleSettings") or {}).get("matchupPeriodCount") or 18)
    week = NflWeek(
        season=season,
        week=current_period,
        season_type="regular" if current_period <= period_count else "post",
        complete=current_period > period_count,
    )

    entries: list[RosterEntry] = []
    for team in raw.get("teams") or []:
        rec = ((team.get("record") or {}).get("overall") or {})
        roster_entries = ((team.get("roster") or {}).get("entries") or [])
        player_ids: list[str] = []
        starter_ids: list[str] = []
        for re in roster_entries:
            sleeper_id = crosswalk.espn_to_sleeper.get(str(re.get("playerId")))
            if sleeper_id is None:
                continue
            player_ids.append(sleeper_id)
            if re.get("lineupSlotId") not in _BENCH_SLOTS:
                starter_ids.append(sleeper_id)
        name = team.get("name") or " ".join(
            p for p in (team.get("location"), team.get("nickname")) if p
        ) or f"Team {team.get('id')}"
        entries.append(RosterEntry(
            roster_id=int(team["id"]),
            team_name=name,
            player_ids=tuple(player_ids),
            starter_ids=tuple(starter_ids),
            wins=int(rec.get("wins") or 0),
            losses=int(rec.get("losses") or 0),
            ties=int(rec.get("ties") or 0),
            points_for=float(rec.get("pointsFor") or 0.0),
            points_against=float(rec.get("pointsAgainst") or 0.0),
        ))
    entries.sort(key=lambda e: e.roster_id)
    return entries, week, raw
