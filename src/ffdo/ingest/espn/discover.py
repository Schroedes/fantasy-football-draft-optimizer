"""Lists every ESPN fantasy-football league a SWID belongs to for a season,
via ESPN's unofficial "fan" API. The espn_s2/SWID cookie pair the user
already pastes for one league covers all of them.

The fan-API response shape is not contract-stable; every field access is
defensive and an unparseable entry is skipped. On any non-auth error this
returns `[]` — the manual add-by-league-ID path is the fallback (spec
§4.1)."""

from __future__ import annotations

import logging

import httpx

from ffdo.domain.models import DiscoveredLeague, make_league_key
from ffdo.ingest.espn import league as league_mod
from ffdo.ingest.espn.client import _USER_AGENT
from ffdo.ingest.espn.connect import ConnectError, normalize_swid

log = logging.getLogger(__name__)

_FAN_BASE = "https://fan.api.espn.com/apis/v2/fans"
_FAN_QUERY = (
    "?displayEvents=true&displayNow=true&displayRecs=false"
    "&featureFlags=fanApiIntegrationWebview&source=ESPN.com&lang=en&section=espn"
)
_FOOTBALL_GAME_IDS = {1, "1", "ffl"}


def list_leagues(
    espn_s2: str,
    swid: str,
    season: int,
    *,
    tracked_keys: frozenset[str] = frozenset(),
    transport: httpx.BaseTransport | None = None,
) -> list[DiscoveredLeague]:
    swid = normalize_swid(swid)
    headers = {
        "Cookie": f"espn_s2={espn_s2}; SWID={swid}",
        "User-Agent": _USER_AGENT,
    }
    url = f"{_FAN_BASE}/{swid}{_FAN_QUERY}"
    try:
        with httpx.Client(timeout=30.0, transport=transport) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            raw = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            raise ConnectError(
                "Your ESPN cookies look expired -- grab fresh espn_s2/SWID values"
            ) from exc
        log.warning("ESPN fan API returned %s; falling back to manual add",
                    exc.response.status_code)
        return []
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("ESPN fan API call failed (%s); falling back to manual add", exc)
        return []

    out: list[DiscoveredLeague] = []
    for pref in raw.get("preferences") or []:
        if ((pref.get("type") or {}).get("code")) != "fantasy":
            continue
        entry = (pref.get("metaData") or {}).get("entry") or {}
        if entry.get("gameId") not in _FOOTBALL_GAME_IDS:
            continue
        if int(entry.get("seasonId") or 0) != season:
            continue
        groups = entry.get("groups") or []
        if not groups:
            continue
        group = groups[0]
        league_id = group.get("groupId")
        if league_id is None:
            continue
        league_id = str(league_id)
        draft_complete = bool(group.get("draftComplete"))
        out.append(DiscoveredLeague(
            provider="espn",
            provider_league_id=league_id,
            season=season,
            name=group.get("groupName") or "",
            num_teams=int(group.get("groupSize") or 0),
            draft_type="",  # resolved at track time
            fmt="redraft",  # detect_format needs mSettings, not in the fan payload
            draft_status="complete" if draft_complete else "pre_draft",
            already_tracked=make_league_key("espn", league_id, season) in tracked_keys,
        ))
    return out
