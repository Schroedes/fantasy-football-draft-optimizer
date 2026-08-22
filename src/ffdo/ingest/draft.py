"""Translates /v1/draft/<id> and /picks into DraftState."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import DraftPick, DraftState


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse(meta: dict[str, Any], picks: list[dict[str, Any]]) -> DraftState:
    settings = meta.get("settings") or {}
    parsed = tuple(
        DraftPick(
            pick_no=int(p["pick_no"]),
            round=int(p["round"]),
            draft_slot=int(p["draft_slot"]),
            roster_id=_as_int(p.get("roster_id")),
            picked_by=p.get("picked_by") or None,
            player_id=str(p["player_id"]),
            # Auction amounts arrive as strings, e.g. "42".
            amount=_as_int((p.get("metadata") or {}).get("amount")),
        )
        for p in picks
    )
    return DraftState(
        draft_id=meta["draft_id"],
        draft_type=meta["type"],
        status=meta["status"],
        num_teams=int(settings.get("teams", 0)),
        rounds=int(settings.get("rounds", 0)),
        budget=settings.get("budget"),
        picks=parsed,
    )
