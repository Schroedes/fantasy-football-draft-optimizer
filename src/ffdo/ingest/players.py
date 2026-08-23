"""Translates /v1/players/nfl into PlayerProfile. Sleeper keys stop here."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import PlayerProfile


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse(raw: dict[str, Any]) -> dict[str, PlayerProfile]:
    out: dict[str, PlayerProfile] = {}
    for player_id, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        position = rec.get("position")
        if position is None:
            continue
        out[player_id] = PlayerProfile(
            player_id=player_id,
            first_name=rec.get("first_name") or "",
            last_name=rec.get("last_name") or "",
            position=position,
            team=rec.get("team"),
            age=_as_int(rec.get("age")),
            years_exp=_as_int(rec.get("years_exp")),
            injury_status=rec.get("injury_status") or None,
            active=bool(rec.get("active")),
        )
    return out


def espn_id_index(raw: dict[str, Any]) -> dict[str, str]:
    """player_id (Sleeper) -> espn_id, for every record that has one.

    Coverage is real but incomplete -- verified 2026-08-23 against a real
    snapshot: ~46% of active skill-position players, skewed toward
    established veterans over recent draftees. See
    docs/superpowers/specs/2026-08-23-espn-league-support-design.md §4.1.
    """
    out: dict[str, str] = {}
    for player_id, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        espn_id = rec.get("espn_id")
        if espn_id:
            out[player_id] = str(espn_id)
    return out
