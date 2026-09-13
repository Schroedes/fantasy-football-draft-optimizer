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
    """A 404 here means Sleeper has no trade history for this league -- a
    real, common state, not an outage (confirmed live) -- so it degrades to
    `[]` (every roster implicitly owns its own picks), same as an empty 200.
    Any other failure must propagate: `get_season` treats a genuine outage
    on this feed differently from "no trades ever happened" (see its
    handling of `capital()`)."""
    try:
        return sleeper.get_json(f"{V1}/league/{league_id}/traded_picks") or []
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return []
        raise


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

    # (year, round, original_roster) -> {previous_owner_id -> owner_id} edges.
    # A pick that changed hands more than once produces multiple entries
    # sharing the same key; the edges must be walked as a chain (from the
    # original roster forward) rather than merged by array position, since
    # the source array is not guaranteed to be in chronological order.
    edges: dict[tuple[int, int, int], dict[int, int]] = {}
    for entry in traded:
        try:
            key = (int(entry["season"]), int(entry["round"]), int(entry["roster_id"]))
            previous_owner = int(entry["previous_owner_id"])
            new_owner = int(entry["owner_id"])
        except (KeyError, TypeError, ValueError):
            continue
        edges.setdefault(key, {})[previous_owner] = new_owner

    slot_by_roster = {rid: i + 1 for i, rid in enumerate(standings_order)}

    next_year = min(draft_years) if draft_years else None
    out: list[DraftPickAsset] = []
    for year in draft_years:
        for rnd in range(1, rounds + 1):
            for original in range(1, num_teams + 1):
                key_edges = edges.get((year, rnd, original), {})
                current = original
                while current in key_edges:
                    current = key_edges[current]
                slot = (slot_by_roster.get(original)
                        if year == next_year and original in slot_by_roster
                        else None)
                out.append(DraftPickAsset(
                    season=year,
                    round=rnd,
                    projected_slot=slot,
                    current_owner_roster_id=current,
                    original_roster_id=original,
                    # The pick is listed under the CURRENT owner's row (see
                    # _draft_capital_payload's by_owner grouping); "via" is
                    # meant to say where it came FROM, i.e. the original
                    # owner who traded it away. team_names.get(current) named
                    # the same team the row already belongs to.
                    via_team_name=team_names.get(original) if current != original else None,
                ))
    return out
