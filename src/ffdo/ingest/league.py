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
    )
