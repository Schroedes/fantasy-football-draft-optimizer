"""This week's projected points per player, from Sleeper's weekly
projections feed. Unlike `ingest/projections.py`'s season-total feed, a
single week's projection is meant to be refined right up to kickoff -- a
Tuesday projection being less accurate than Sunday morning's is the
projection doing its job, not corruption -- so there is no
ContaminatedProjectionError-style guard here."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import WeeklyProjection
from ffdo.ingest.client import PROJECTIONS, SleeperClient


def fetch(sleeper: SleeperClient, season: int, week: int) -> dict[str, WeeklyProjection]:
    raw: list[dict[str, Any]] = sleeper.get_json(
        f"{PROJECTIONS}/{season}/{week}"
        "?season_type=regular&position[]=QB&position[]=RB"
        "&position[]=WR&position[]=TE&position[]=DEF"
        "&position[]=K")

    out: dict[str, WeeklyProjection] = {}
    for row in raw or []:
        player_id = row.get("player_id")
        stats = row.get("stats") or {}
        if not player_id or not stats:
            continue
        # bool is a subclass of int; excluded so a JSON boolean stat value
        # is dropped rather than silently coerced to 1.0/0.0 (mirrors
        # ffdo.ingest.projections.parse).
        numeric = {k: float(v) for k, v in stats.items()
                  if isinstance(v, (int, float)) and not isinstance(v, bool)}
        # A row with nothing but `gp` carries no scoring signal -- same
        # "not actually projected" filter ingest/projections.py applies.
        if not (numeric.keys() - {"gp"}):
            continue
        out[str(player_id)] = WeeklyProjection(
            player_id=str(player_id), season=season, week=week, stats=numeric)
    return out
