"""Resolves a Sleeper league ID + username into a connected Session.

Orchestrates the handful of Sleeper calls needed to go from "league ID and
username" to a fully-identified league/draft/roster -- the one-time lookup
that runs when the main screen's connect form is submitted, not on every
board poll.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone

import httpx

from ffdo.domain.models import Session
from ffdo.ingest import draft as draft_mod
from ffdo.ingest import league as league_mod
from ffdo.ingest import mock_draft
from ffdo.ingest import user as user_mod
from ffdo.ingest.client import V1, SleeperClient


class ConnectError(Exception):
    """A user-facing reason `resolve()` could not connect a league."""


def resolve(
    sleeper: SleeperClient,
    league_id: str,
    username: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> Session:
    now = now or (lambda: datetime.now(timezone.utc))

    try:
        league_raw = sleeper.get_json(f"{V1}/league/{league_id}")
    except httpx.HTTPStatusError as exc:
        raise ConnectError("League not found") from exc
    league = league_mod.parse(league_raw)

    drafts_raw = sleeper.get_json(f"{V1}/league/{league_id}/drafts")
    draft_id = league_mod.most_recent_draft_id(drafts_raw)
    if draft_id is None:
        raise ConnectError("No draft found for this league")

    draft_meta = sleeper.get_json(f"{V1}/draft/{draft_id}")
    state = draft_mod.parse(draft_meta, [])

    # Some leagues carry the auction budget on the draft object rather than
    # the league's own settings -- same fallback ffdo.api.app.get_board()
    # already applies, kept consistent here so a connected Session's budget
    # is never spuriously None for a league this app already supports.
    if league.budget is None:
        league = replace(league, budget=state.budget)

    try:
        user_raw = sleeper.get_json(f"{V1}/user/{username}")
    except httpx.HTTPStatusError as exc:
        raise ConnectError("Username not found") from exc
    user_id, _display_name = user_mod.parse(user_raw)

    rosters_raw = sleeper.get_json(f"{V1}/league/{league_id}/rosters")
    roster_id = league_mod.find_roster_id(rosters_raw, user_id)
    if roster_id is None:
        raise ConnectError("This user is not a member of that league")

    return Session(
        username=username,
        user_id=user_id,
        league_id=league.league_id,
        draft_id=draft_id,
        roster_id=roster_id,
        league_name=league.name,
        season=league.season,
        num_teams=league.num_teams,
        budget=league.budget,
        roster_positions=league.roster_positions,
        scoring_settings=league.scoring_settings,
        draft_type=state.draft_type,
        draft_status=state.status,
        rounds=state.rounds,
        connected_at=now().isoformat(),
        is_mock=False,
    )


def resolve_mock(
    sleeper: SleeperClient,
    draft_id: str,
    username: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> Session:
    now = now or (lambda: datetime.now(timezone.utc))

    try:
        draft_raw = sleeper.get_json(f"{V1}/draft/{draft_id}")
    except httpx.HTTPStatusError as exc:
        raise ConnectError("Mock draft not found") from exc

    if not mock_draft.is_mock_draft(draft_raw):
        raise ConnectError(
            "This looks like a real league draft — use the League ID + "
            "Username form instead")

    try:
        lg = mock_draft.build_league_profile(draft_raw)
    except mock_draft.MockDraftError as exc:
        raise ConnectError(str(exc)) from exc

    try:
        user_raw = sleeper.get_json(f"{V1}/user/{username}")
    except httpx.HTTPStatusError as exc:
        raise ConnectError("Username not found") from exc
    user_id, _display_name = user_mod.parse(user_raw)

    roster_id = mock_draft.resolve_roster_id(draft_raw, user_id)
    settings = draft_raw.get("settings") or {}

    return Session(
        username=username,
        user_id=user_id,
        league_id="",
        draft_id=draft_id,
        roster_id=roster_id,
        league_name=lg.name,
        season=lg.season,
        num_teams=lg.num_teams,
        budget=lg.budget,
        roster_positions=lg.roster_positions,
        scoring_settings=lg.scoring_settings,
        draft_type=draft_raw["type"],
        draft_status=draft_raw["status"],
        rounds=int(settings.get("rounds", 0)),
        connected_at=now().isoformat(),
        is_mock=True,
    )
