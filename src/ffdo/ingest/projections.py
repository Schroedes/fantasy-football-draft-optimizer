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

    # The guard's job is to refuse when it cannot prove the data is clean --
    # not to pass it through whenever an input is unexpected. `kickoff and
    # modified > kickoff` used to short-circuit to False (silently skipping
    # the check) whenever `season` was unknown or no row carried a
    # `last_modified` timestamp, which is the exact opposite of "refuse
    # rather than trust." Both cases now raise unless the caller explicitly
    # opts in with `allow_contaminated=True`.
    if not allow_contaminated:
        if season not in SEASON_START:
            raise ContaminatedProjectionError(
                f"season {season} has no known kickoff date in SEASON_START; "
                f"cannot verify these projections predate the season. Add "
                f"{season} to SEASON_START, or pass allow_contaminated=True "
                f"to inspect deliberately."
            )
        if raw and modified is None:
            raise ContaminatedProjectionError(
                f"{season} projections carry no last_modified timestamp on "
                f"any row; cannot verify these projections predate kickoff "
                f"({kickoff.date()}). Pass allow_contaminated=True to "
                f"inspect deliberately."
            )
        if modified and kickoff and modified > kickoff:
            raise ContaminatedProjectionError(
                f"{season} projections last modified {modified.date()}, "
                f"after kickoff {kickoff.date()}; points are post-hoc. Use "
                f"ADP instead, or pass allow_contaminated=True to inspect "
                f"deliberately."
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
        adp[player_id] = MarketADP(
            player_id=player_id, season=season,
            adp={k[len(_ADP_PREFIX):]: v for k, v in numeric.items()
                 if k.startswith(_ADP_PREFIX)},
        )
        projection_stats = {k: v for k, v in numeric.items()
                            if not k.startswith(_ADP_PREFIX)}
        # Sleeper returns a row for every player who carries an ADP, whether
        # or not it actually projected them -- hundreds come back with
        # nothing but `gp` once the adp_ keys are stripped (roughly 120 of
        # its 153 "kickers", plus deep-bench skill players). A row with no
        # projected stat carries no information for valuation: it would
        # score 0 and land at a large negative VOR, cluttering the board
        # with players nobody projected. Emit its ADP (an independent
        # signal, fixed at draft time) but no projection. The
        # `allow_contaminated` inspection path keeps every row -- a wiped
        # historical projection (`gp`-only, e.g. Nick Chubb 2023) is
        # exactly what that path exists to examine.
        if not allow_contaminated and not (projection_stats.keys() - {"gp"}):
            continue
        proj[player_id] = SeasonProjection(
            player_id=player_id, season=season,
            stats=projection_stats,
            last_modified=modified,
        )
    return proj, adp
