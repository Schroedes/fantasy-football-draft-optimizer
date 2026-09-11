"""Translates /league/<id>/rosters and /league/<id>/users into team identity."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import TeamProfile


def _display_names(users: list[dict[str, Any]]) -> dict[str, str]:
    """{user_id: team_name or display_name}. Team name from a user's
    metadata beats the raw display name; a user with neither is omitted."""
    out: dict[str, str] = {}
    for u in users:
        user_id = u.get("user_id")
        if user_id is None:
            continue
        metadata = u.get("metadata") or {}
        name = metadata.get("team_name") or u.get("display_name")
        if name:
            out[str(user_id)] = name
    return out


def parse(
    rosters: list[dict[str, Any]],
    users: list[dict[str, Any]],
) -> dict[int, TeamProfile]:
    names = _display_names(users)
    out: dict[int, TeamProfile] = {}
    for r in rosters:
        raw_roster_id = r.get("roster_id")
        if raw_roster_id is None:
            continue
        roster_id = int(raw_roster_id)
        owner_id = r.get("owner_id")
        name = names.get(str(owner_id)) if owner_id is not None else None
        out[roster_id] = TeamProfile(
            roster_id=roster_id,
            display_name=name or f"Team {roster_id}",
        )
    return out
