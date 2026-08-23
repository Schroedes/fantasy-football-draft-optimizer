"""Translates ESPN's `mTeam` view into team identity."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import TeamProfile


def parse(raw: dict[str, Any]) -> dict[int, TeamProfile]:
    out: dict[int, TeamProfile] = {}
    for t in raw.get("teams", []):
        team_id = t.get("id")
        if team_id is None:
            continue
        name = t.get("name") or " ".join(
            p for p in (t.get("location"), t.get("nickname")) if p) or f"Team {team_id}"
        out[int(team_id)] = TeamProfile(roster_id=int(team_id), display_name=name)
    return out


def find_roster_id(raw: dict[str, Any], swid: str) -> int | None:
    """`raw` is the mTeam view. `swid` should already be normalized to
    include braces (see ingest/espn/connect.py). Mirrors Sleeper's
    ingest.league.find_roster_id."""
    for t in raw.get("teams", []):
        owners = t.get("owners") or []
        if swid in owners:
            team_id = t.get("id")
            return int(team_id) if team_id is not None else None
    return None
