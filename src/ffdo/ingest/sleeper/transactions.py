"""Real completed trades, from /league/<id>/transactions/<week>. Verified
live during brainstorming: this endpoint returns every transaction type
(trade, waiver, free_agent), with trade records carrying roster_ids (both
parties), adds/drops (player_id -> roster_id, both directions), draft_picks
(round/season/owner_id/previous_owner_id), a stable transaction_id, and a
created timestamp -- everything api/trade_ledger.py needs, no gaps."""

from __future__ import annotations

from ffdo.domain.models import DraftPickAsset, TradeTransaction
from ffdo.ingest.client import V1, SleeperClient


def _parse_trade(raw: dict, season: int, week: int) -> TradeTransaction:
    roster_ids = sorted(int(r) for r in raw["roster_ids"])
    a_id, b_id = roster_ids[0], roster_ids[1]

    adds = raw.get("adds") or {}
    a_gets = [pid for pid, dest in adds.items() if int(dest) == a_id]
    b_gets = [pid for pid, dest in adds.items() if int(dest) == b_id]

    picks_to_a: list[DraftPickAsset] = []
    picks_to_b: list[DraftPickAsset] = []
    for p in raw.get("draft_picks") or []:
        owner = int(p["owner_id"])
        asset = DraftPickAsset(
            season=int(p["season"]), round=int(p["round"]), projected_slot=None,
            current_owner_roster_id=owner, original_roster_id=int(p["previous_owner_id"]),
            via_team_name=None)
        (picks_to_a if owner == a_id else picks_to_b).append(asset)

    return TradeTransaction(
        transaction_id=raw["transaction_id"], season=season, week=week,
        roster_a_id=a_id, roster_b_id=b_id,
        roster_a_gets=a_gets, roster_b_gets=b_gets,
        picks_to_a=picks_to_a, picks_to_b=picks_to_b,
        traded_at_ms=int(raw["created"]))


def fetch_trades(
    sleeper: SleeperClient, league_id: str, *, season: int, through_week: int,
) -> list[TradeTransaction]:
    out: list[TradeTransaction] = []
    for week in range(1, through_week + 1):
        raw_list = sleeper.get_json(f"{V1}/league/{league_id}/transactions/{week}")
        for raw in raw_list:
            if raw.get("type") == "trade" and raw.get("status") == "complete":
                out.append(_parse_trade(raw, season, week))
    return out
