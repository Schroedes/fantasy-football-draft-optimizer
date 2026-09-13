"""Real completed FAAB waiver claims, from /league/<id>/transactions/<week>
-- the sibling of ingest.sleeper.transactions.fetch_trades, filtered to
type == "waiver" instead of "trade". Also provides the chronological
running-FAAB-spend walk shared by scripts/fit_faab_curve.py (historical
fitting) and the live /waivers endpoint (current remaining budget) --
both need "walk this season's waiver claims in order, track cumulative
spend per roster," so it lives here once rather than being duplicated.
"""

from __future__ import annotations

from collections.abc import Sequence

from ffdo.domain.models import WaiverClaim
from ffdo.ingest.client import V1, SleeperClient


def fetch_waivers(
    sleeper: SleeperClient, league_id: str, *, season: int, through_week: int,
) -> list[WaiverClaim]:
    out: list[WaiverClaim] = []
    for week in range(1, through_week + 1):
        raw_list = sleeper.get_json(f"{V1}/league/{league_id}/transactions/{week}")
        for raw in raw_list:
            if raw.get("type") != "waiver" or raw.get("status") != "complete":
                continue
            adds = raw.get("adds") or {}
            if not adds:
                continue
            roster_id = int(raw["roster_ids"][0])
            player_id = next(iter(adds))
            bid = ((raw.get("settings") or {}).get("waiver_bid")) or 0.0
            out.append(WaiverClaim(
                transaction_id=raw["transaction_id"], season=season, week=week,
                roster_id=roster_id, player_id=player_id,
                bid_amount=float(bid), created_ms=int(raw["created"])))
    return out


def remaining_budget(
    claims: Sequence[WaiverClaim], *, waiver_budget: float,
) -> dict[int, float]:
    spent: dict[int, float] = {}
    for claim in sorted(claims, key=lambda c: c.created_ms):
        spent[claim.roster_id] = spent.get(claim.roster_id, 0.0) + claim.bid_amount
    return {roster_id: waiver_budget - total for roster_id, total in spent.items()}
