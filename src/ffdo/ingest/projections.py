"""Projections + ADP, with a hard guard against post-season contamination.

Sleeper's projections endpoint returns the LATEST state of a projection, not
its preseason state. For completed seasons the points have been overwritten
with in-season information: Nick Chubb's stored 2023 projection carries no
`pts_half_ppr` figure at all -- the key is simply absent from the row --
despite a preseason ADP of 10.6, because he tore his knee in week 2 and
never accumulated the season of production Sleeper would otherwise report.
A missing points figure for a player with a top-11 ADP is exactly the signal
that the season played out and wiped the projection; we normalize that
absence to 0.0 in the domain model (see `_PTS_KEYS` below) so downstream
code sees an honest zero instead of a `KeyError`.

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

# The three fantasy-point totals central to valuation. Sleeper omits these
# keys entirely for a player who recorded no scoring stats (rather than
# writing an explicit 0.0), so we default them in rather than let a busted
# preseason favorite silently vanish from every points-keyed lookup.
_PTS_KEYS = ("pts_std", "pts_ppr", "pts_half_ppr")


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
        for key in _PTS_KEYS:
            numeric.setdefault(key, 0.0)
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
