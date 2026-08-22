"""Projections + ADP, with a hard guard against post-season contamination.

Sleeper's projections endpoint returns the LATEST state of a projection, not
its preseason state. For completed seasons the points have been WIPED, not
merely overwritten with a stale number: Nick Chubb's 2023 row carries no
`pts_half_ppr` key at all -- not even a stored 0.0 -- despite a preseason ADP
of 10.6 (a top-11 pick), because he tore his knee in week 2 and the season
that followed left nothing for Sleeper to report back. Ingest translates
wire format; it does not invent values, so that absence is preserved as an
absence all the way into `SeasonProjection.stats` rather than being defaulted
to a number nobody stored.

ADP is unaffected -- it is fixed at draft time -- and is therefore the only
historical market signal this project trusts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ffdo.domain.models import MarketADP, SeasonProjection

# NFL regular seasons open in the first full week of September.
SEASON_START: dict[int, datetime] = {
    2021: datetime(2021, 9, 9, tzinfo=UTC),
    2022: datetime(2022, 9, 8, tzinfo=UTC),
    2023: datetime(2023, 9, 7, tzinfo=UTC),
    2024: datetime(2024, 9, 5, tzinfo=UTC),
    2025: datetime(2025, 9, 4, tzinfo=UTC),
    2026: datetime(2026, 9, 9, tzinfo=UTC),
}

_ADP_PREFIX = "adp_"


class ContaminatedProjectionError(RuntimeError):
    """Raised when projection data postdates its own season's kickoff."""


def _last_modified(rows: list[dict[str, Any]]) -> datetime | None:
    stamps = [r["last_modified"] for r in rows if r.get("last_modified")]
    if not stamps:
        return None
    return datetime.fromtimestamp(max(stamps) / 1000, tz=UTC)


def _player_id(row: dict[str, Any]) -> str | None:
    # Snapshot rows carry player_id at the top level. The nested `player`
    # object does not repeat it, but we fall back to it defensively in case
    # a future snapshot shape omits the top-level field.
    return row.get("player_id") or (row.get("player") or {}).get("player_id")


def parse(
    raw: list[dict[str, Any]],
    season: int,
    *,
    allow_contaminated: bool = False,
) -> tuple[dict[str, SeasonProjection], dict[str, MarketADP]]:
    modified = _last_modified(raw)
    kickoff = SEASON_START.get(season)
    if (not allow_contaminated and modified and kickoff and modified > kickoff):
        raise ContaminatedProjectionError(
            f"{season} projections last modified {modified.date()}, after "
            f"kickoff {kickoff.date()}; points are post-hoc. Use ADP instead, "
            f"or pass allow_contaminated=True to inspect deliberately."
        )

    proj: dict[str, SeasonProjection] = {}
    adp: dict[str, MarketADP] = {}
    for row in raw:
        player_id = _player_id(row)
        stats = row.get("stats") or {}
        if not player_id or not stats:
            continue
        # bool is a subclass of int in Python; excluded explicitly so a JSON
        # boolean stat value is dropped rather than silently coerced to
        # 1.0/0.0 (mirrors ffdo.ingest.stats).
        numeric = {k: float(v) for k, v in stats.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)}
        proj[player_id] = SeasonProjection(
            player_id=player_id, season=season,
            stats={k: v for k, v in numeric.items()
                   if not k.startswith(_ADP_PREFIX)},
            last_modified=modified,
        )
        adp[player_id] = MarketADP(
            player_id=player_id, season=season,
            adp={k[len(_ADP_PREFIX):]: v for k, v in numeric.items()
                 if k.startswith(_ADP_PREFIX)},
        )
    return proj, adp
