"""Translates /v1/league/<id> into LeagueProfile."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import LeagueProfile


def parse(raw: dict[str, Any]) -> LeagueProfile:
    settings = raw.get("settings") or {}
    return LeagueProfile(
        league_id=raw["league_id"],
        season=int(raw["season"]),
        num_teams=int(settings.get("num_teams") or raw.get("total_rosters")),
        roster_positions=tuple(raw["roster_positions"]),
        scoring_settings={k: float(v)
                          for k, v in (raw.get("scoring_settings") or {}).items()},
        budget=settings.get("budget"),
        name=raw.get("name") or "",
        status=raw.get("status") or "",
    )


def most_recent_draft_id(drafts: list[dict[str, Any]]) -> str | None:
    """`drafts` is the payload of `/v1/league/<id>/drafts`, newest first."""
    if not drafts:
        return None
    return drafts[0]["draft_id"]


def find_roster_id(rosters: list[dict[str, Any]], user_id: str) -> int | None:
    """`rosters` is the payload of `/v1/league/<id>/rosters`."""
    for roster in rosters:
        if roster.get("owner_id") == user_id:
            return int(roster["roster_id"])
    return None
