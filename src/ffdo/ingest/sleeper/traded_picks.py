"""Draft-pick ownership for a Sleeper dynasty/keeper league, from
/league/<id>/traded_picks. Untraded picks are implicit -- every roster
owns its own pick in every round of every upcoming draft year unless a
trade entry says otherwise. Projected slot = the ORIGINAL roster's
position in reverse-standings order (worst record drafts first)."""

from __future__ import annotations

import httpx

from ffdo.domain.models import DraftPickAsset
from ffdo.ingest.client import V1, SleeperClient


def _traded_raw(sleeper: SleeperClient, league_id: str) -> list[dict]:
    try:
        return sleeper.get_json(f"{V1}/league/{league_id}/traded_picks") or []
    except (httpx.HTTPError, RuntimeError):
        return []


def capital(
    sleeper: SleeperClient,
    league_id: str,
    *,
    num_teams: int,
    rounds: int,
    standings_order: list[int],
    draft_years: tuple[int, ...],
    team_names: dict[int, str],
) -> list[DraftPickAsset]:
    traded = _traded_raw(sleeper, league_id)

    # (year, round, original_roster) -> final current owner
    owner: dict[tuple[int, int, int], int] = {}
    for entry in traded:
        try:
            key = (int(entry["season"]), int(entry["round"]), int(entry["roster_id"]))
        except (KeyError, TypeError, ValueError):
            continue
        owner[key] = int(entry["owner_id"])

    next_year = min(draft_years) if draft_years else None
    out: list[DraftPickAsset] = []
    for year in draft_years:
        for rnd in range(1, rounds + 1):
            for original in range(1, num_teams + 1):
                current = owner.get((year, rnd, original), original)
                slot = (standings_order.index(original) + 1
                        if year == next_year and original in standings_order
                        else None)
                out.append(DraftPickAsset(
                    season=year,
                    round=rnd,
                    projected_slot=slot,
                    current_owner_roster_id=current,
                    original_roster_id=original,
                    via_team_name=team_names.get(current) if current != original else None,
                ))
    return out
