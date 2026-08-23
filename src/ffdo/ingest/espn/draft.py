"""Translates ESPN's `mDraftDetail` view into DraftState."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import DraftPick, DraftState
from ffdo.ingest.espn.crosswalk import Crosswalk


def parse(raw: dict[str, Any], crosswalk: Crosswalk) -> DraftState:
    settings = raw["settings"]
    draft_settings = settings.get("draftSettings") or {}
    detail = raw["draftDetail"]
    all_picks = detail.get("picks", [])
    # The `mDraftDetail` view (unlike `mSettings`, which ingest/espn/league.py
    # reads) carries no top-level `size` field -- verified live 2026-08-23
    # against the real committed fixture, whose `settings` object holds only
    # `draftSettings`. `draftSettings.pickOrder` lists every team exactly
    # once, so its length is this view's only reliable team count; fall back
    # to `settings["size"]` first in case a future/other view does include it.
    num_teams_raw = settings.get("size")
    if num_teams_raw is None:
        num_teams_raw = len(draft_settings.get("pickOrder") or [])
    num_teams = int(num_teams_raw)
    rounds = len(all_picks) // num_teams if num_teams else 0

    if detail.get("drafted"):
        status = "complete"
    elif detail.get("inProgress"):
        status = "drafting"
    else:
        status = "pre_draft"

    parsed: list[DraftPick] = []
    for p in all_picks:
        espn_player_id = p.get("playerId")
        # ESPN pre-populates the *entire* draft with placeholder picks
        # before it starts -- an unplayed slot carries playerId: -1
        # (verified live 2026-08-23; a team defense's REAL id is a large
        # negative number like -16034, so this check must be an exact
        # equality against -1, never `<= 0`).
        if espn_player_id is None or espn_player_id == -1:
            continue
        sleeper_player_id = crosswalk.espn_to_sleeper.get(str(espn_player_id))
        if sleeper_player_id is None:
            continue
        bid_amount = p.get("bidAmount")
        parsed.append(DraftPick(
            pick_no=int(p["overallPickNumber"]),
            round=int(p["roundId"]),
            draft_slot=int(p["roundPickNumber"]),
            roster_id=int(p["teamId"]),
            picked_by=None,
            player_id=sleeper_player_id,
            amount=int(bid_amount) if bid_amount else None,
        ))

    return DraftState(
        draft_id=str(raw["id"]),
        draft_type=draft_settings.get("type", "").lower(),
        status=status,
        num_teams=num_teams,
        rounds=rounds,
        budget=draft_settings.get("auctionBudget"),
        picks=tuple(parsed),
    )
