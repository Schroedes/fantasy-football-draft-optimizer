"""Resolves an ESPN league ID + season + cookies into a connected Session.

Mirrors ffdo.ingest.connect's Sleeper flow: one-time orchestration when the
main screen's connect form is submitted, not run on every board poll.
Unlike the Sleeper flow (which receives an already-constructed,
credential-free SleeperClient), this module owns constructing its own
EspnClient internally, since it also needs the raw espn_s2/swid strings to
persist into the returned Session for later board polls to reuse.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from ffdo.domain.models import PlayerProfile, Session
from ffdo.ingest.espn import crosswalk as crosswalk_mod
from ffdo.ingest.espn import draft as draft_mod
from ffdo.ingest.espn import league as league_mod
from ffdo.ingest.espn import teams as teams_mod
from ffdo.ingest.espn.client import BASE, EspnClient


class ConnectError(Exception):
    """A user-facing reason resolve() could not connect an ESPN league."""


def normalize_swid(swid: str) -> str:
    """ESPN's own cookie jar wraps SWID in curly braces; a user pasting the
    raw value verbatim might paste it with or without them. Always store
    (and send) the braced form, since that's the form verified live
    against the real API."""
    swid = swid.strip()
    if not swid.startswith("{"):
        swid = "{" + swid
    if not swid.endswith("}"):
        swid = swid + "}"
    return swid


def resolve(
    league_id: str,
    season: int,
    espn_s2: str,
    swid: str,
    profiles: dict[str, PlayerProfile],
    espn_id_index: dict[str, str],
    *,
    now: Callable[[], datetime] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Session:
    now = now or (lambda: datetime.now(timezone.utc))
    swid = normalize_swid(swid)

    espn = EspnClient(espn_s2, swid, base_delay=0, transport=transport)
    try:
        try:
            raw = espn.get_json(
                f"{BASE}/seasons/{season}/segments/0/leagues/{league_id}"
                "?view=mSettings&view=mTeam&view=mDraftDetail")
        except httpx.HTTPStatusError as exc:
            raise ConnectError("League not found") from exc

        league = league_mod.parse(raw)

        if league_mod.draft_type(raw) != "snake":
            raise ConnectError("ESPN auction support isn't built yet")

        roster_id = teams_mod.find_roster_id(raw, swid)
        if roster_id is None:
            raise ConnectError("This SWID is not a member of that league")

        player_pool_raw = espn.get_json(
            f"{BASE}/seasons/{season}/players?view=kona_player_info")
        espn_players = crosswalk_mod.parse_player_pool(player_pool_raw)
        cw = crosswalk_mod.build(espn_id_index, profiles, espn_players)

        state = draft_mod.parse(raw, cw)
    finally:
        espn.close()

    return Session(
        username="",
        user_id=swid,
        league_id=league.league_id,
        draft_id=league.league_id,
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
        provider="espn",
        espn_s2=espn_s2,
        swid=swid,
    )
