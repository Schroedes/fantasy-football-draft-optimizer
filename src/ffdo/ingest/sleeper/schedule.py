"""This week's NFL game schedule, from Sleeper's unofficial (undocumented
in Sleeper's public API docs) /schedule/nfl/regular/{season} endpoint.
Confirmed live and working 2026-09-11.

`status` is the live lock signal this project trusts: it starts as
"pre_game" and transitions once a game starts (through at least
"complete") -- this answers "can this player still be swapped" without
needing exact kickoff timestamps, which this endpoint doesn't even expose
(`date` is calendar-day only, no time-of-day).

If this endpoint ever disappears or changes shape, callers must degrade
gracefully rather than fail the whole request -- see
`ffdo.api.app.get_lineup`'s handling of a failed `week_games` call."""

from __future__ import annotations

from typing import Any

from ffdo.ingest.client import SCHEDULE, SleeperClient


def week_games(sleeper: SleeperClient, season: int, week: int) -> list[dict[str, Any]]:
    raw = sleeper.get_json(f"{SCHEDULE}/{season}")
    return [g for g in (raw or []) if g.get("week") == week]


def locked_teams(games: list[dict[str, Any]]) -> frozenset[str]:
    locked: set[str] = set()
    for g in games:
        if g.get("status") == "pre_game":
            continue
        if g.get("home"):
            locked.add(g["home"])
        if g.get("away"):
            locked.add(g["away"])
    return frozenset(locked)


def bye_teams(games: list[dict[str, Any]], all_teams: frozenset[str]) -> frozenset[str]:
    playing = {t for g in games for t in (g.get("home"), g.get("away")) if t}
    return all_teams - playing


def week_locked(games: list[dict[str, Any]]) -> bool:
    """True once every game that week has at least started -- a provider
    locks a team's actual starters at that team's own kickoff, not at the
    game's final whistle, so "started" (not "finished") is the point past
    which no further lineup action is possible. An empty list (no schedule
    data at all -- see the graceful-degradation note above) is never
    considered locked."""
    return bool(games) and all(g.get("status") != "pre_game" for g in games)
