"""Translates /v1/stats/nfl/regular/<season> into SeasonStatLine."""

from __future__ import annotations

from typing import Any

from ffdo.domain.constants import SEASON_LENGTH
from ffdo.domain.models import SeasonStatLine


def parse(raw: dict[str, Any], season: int) -> dict[str, SeasonStatLine]:
    length = SEASON_LENGTH[season]
    out: dict[str, SeasonStatLine] = {}
    for player_id, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        numeric = {k: float(v) for k, v in rec.items()
                   if isinstance(v, (int, float))}
        out[player_id] = SeasonStatLine(
            player_id=player_id,
            season=season,
            games_played=int(numeric.get("gp", 0)),
            season_length=length,
            stats=numeric,
        )
    return out
