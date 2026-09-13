"""Lists every Sleeper league a user is in for a season — the input to the
discover-then-pick onboarding flow. Distinct from `ingest.connect`, which
does the heavier per-league league/draft/roster resolution once the user
picks which leagues to track."""

from __future__ import annotations

import httpx

from ffdo.domain.models import DiscoveredLeague, make_league_key
from ffdo.ingest import league as league_mod
from ffdo.ingest import user as user_mod
from ffdo.ingest.client import V1, SleeperClient
from ffdo.ingest.connect import ConnectError


def resolve_user_id(sleeper: SleeperClient, username: str) -> str:
    try:
        raw = sleeper.get_json(f"{V1}/user/{username}")
    except httpx.HTTPStatusError as exc:
        raise ConnectError("Username not found") from exc
    # Sleeper answers an unknown username with `200 null` rather than a 404,
    # so the except arm above never fires -- without this guard
    # `user_mod.parse(None)` raises a bare TypeError and the discovery screen
    # gets a 500 where it should get "Username not found".
    if not raw:
        raise ConnectError("Username not found")
    return user_mod.parse(raw)[0]


def list_leagues(
    sleeper: SleeperClient,
    user_id: str,
    season: int,
    *,
    tracked_keys: frozenset[str] = frozenset(),
) -> list[DiscoveredLeague]:
    raw = sleeper.get_json(f"{V1}/user/{user_id}/leagues/nfl/{season}")
    out: list[DiscoveredLeague] = []
    for lg in raw or []:
        league_id = str(lg["league_id"])
        settings = lg.get("settings") or {}
        out.append(DiscoveredLeague(
            provider="sleeper",
            provider_league_id=league_id,
            season=season,
            name=lg.get("name") or "",
            num_teams=int(settings.get("num_teams") or lg.get("total_rosters") or 0),
            draft_type="",  # resolved at track time from the draft object
            fmt=league_mod.detect_format(lg),
            draft_status=lg.get("status") or "",
            already_tracked=make_league_key("sleeper", league_id, season) in tracked_keys,
        ))
    return out
