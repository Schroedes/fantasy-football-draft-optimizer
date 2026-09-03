"""Resolves a Sleeper league ID + username into a tracked league.

Orchestrates the handful of Sleeper calls needed to go from "league ID and
username" to a fully-identified league/draft/roster -- the one-time lookup
that runs when the main screen's connect form is submitted, not on every
board poll.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

import httpx

from ffdo.domain.models import TrackedLeague, make_league_key
from ffdo.ingest import draft as draft_mod
from ffdo.ingest import league as league_mod
from ffdo.ingest import mock_draft
from ffdo.ingest import user as user_mod
from ffdo.ingest.client import V1, SleeperClient


class ConnectError(Exception):
    """A user-facing reason `track()` could not connect a league."""


def track(
    sleeper: SleeperClient,
    league_id: str,
    username: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> TrackedLeague:
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
    # already applies, kept consistent here so a tracked league's budget is
    # never spuriously None for a league this app already supports.
    budget = league.budget if league.budget is not None else state.budget

    try:
        user_raw = sleeper.get_json(f"{V1}/user/{username}")
    except httpx.HTTPStatusError as exc:
        raise ConnectError("Username not found") from exc
    # Sleeper answers an unknown username with `200 null`, not a 404 -- so the
    # except arm above never fires and `user_mod.parse(None)` would raise a
    # bare TypeError, surfacing as a 500 instead of the same clean, user-facing
    # "Username not found" a real 404 already produces.
    if not user_raw:
        raise ConnectError("Username not found")
    user_id, _display_name = user_mod.parse(user_raw)

    rosters_raw = sleeper.get_json(f"{V1}/league/{league_id}/rosters")
    roster_id = league_mod.find_roster_id(rosters_raw, user_id)
    if roster_id is None:
        raise ConnectError("This user is not a member of that league")

    stamp = now().isoformat()
    return TrackedLeague(
        league_key=make_league_key("sleeper", league.league_id, league.season),
        provider="sleeper",
        provider_league_id=league.league_id,
        season=league.season,
        name=league.name,
        user_id=user_id,
        roster_id=roster_id,
        draft_id=draft_id,
        draft_type=state.draft_type,
        draft_status=state.status,
        num_teams=league.num_teams,
        budget=budget,
        rounds=state.rounds,
        roster_positions=league.roster_positions,
        scoring_settings=league.scoring_settings,
        fmt=league_mod.detect_format(league_raw),
        format_override=None,
        raw_settings=league_raw.get("settings") or {},
        is_mock=False,
        tracked_at=stamp,
        last_refreshed_at=stamp,
    )


def track_mock(
    sleeper: SleeperClient,
    draft_id: str,
    username: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> TrackedLeague:
    now = now or (lambda: datetime.now(timezone.utc))

    try:
        draft_raw = sleeper.get_json(f"{V1}/draft/{draft_id}")
    except httpx.HTTPStatusError as exc:
        raise ConnectError("Mock draft not found") from exc

    # Sleeper answers some invalid/unknown draft IDs with `200 {}` (or
    # `200 null`) instead of a 404. Without this guard, `is_mock_draft({})`
    # returns True (an empty dict's `.get("league_id")` is None, same as a
    # real mock draft), so resolution would proceed past the mock-draft
    # check and die deep inside `build_league_profile()` on a bare
    # `KeyError` for `draft_raw["season"]` -- a 500 instead of the same
    # clean "Mock draft not found" a 404 already produces.
    if not draft_raw:
        raise ConnectError("Mock draft not found")

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
    # Same `200 null` guard as `track()` above -- an unknown username is not a
    # 404 from Sleeper, and `user_mod.parse(None)` is a TypeError/500.
    if not user_raw:
        raise ConnectError("Username not found")
    user_id, _display_name = user_mod.parse(user_raw)

    roster_id = mock_draft.resolve_roster_id(draft_raw, user_id)
    settings = draft_raw.get("settings") or {}

    stamp = now().isoformat()
    return TrackedLeague(
        league_key=make_league_key("sleeper-mock", draft_id, lg.season),
        provider="sleeper-mock",
        provider_league_id=draft_id,
        season=lg.season,
        name=lg.name,
        user_id=user_id,
        roster_id=roster_id,
        draft_id=draft_id,
        draft_type=draft_raw["type"],
        draft_status=draft_raw["status"],
        num_teams=lg.num_teams,
        budget=lg.budget,
        rounds=int(settings.get("rounds", 0)),
        roster_positions=lg.roster_positions,
        scoring_settings=lg.scoring_settings,
        fmt="redraft",
        format_override=None,
        raw_settings=settings,
        is_mock=True,
        tracked_at=stamp,
        last_refreshed_at=stamp,
    )
