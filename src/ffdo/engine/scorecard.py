"""Outcome scorecard: how good FFDO's recommendations have actually been,
per recommendation type. Pure functions over lightweight outcome
dataclasses this module defines itself -- NOT the ledgers' own
storage-shaped dataclasses (api.lineup_ledger.LineupRecord etc.), since
engine/ modules depend only on domain/ and other engine/ modules, never
api/. api.app.py (the composition root) converts ledger rows into these
outcome objects before calling in here, the same way it already shapes
every other endpoint's JSON response.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ffdo.engine.scoring import score_stats


@dataclass(frozen=True, slots=True)
class LineupOutcome:
    season: int
    week: int
    recommended: Mapping[int, str | None]
    actual: tuple[str | None, ...]
    followed: str  # "full" | "partial" | "none" -- only resolved rows are passed in


def lineup_metric(
    outcomes: Sequence[LineupOutcome],
    weekly_points: Mapping[tuple[int, int], Mapping[str, dict[str, float]]],
    scoring_settings: Mapping[str, float],
) -> dict:
    """weekly_points is keyed (season, week) -> {player_id: raw stat dict},
    the exact shape ingest.sleeper.historical_weekly_stats.fetch returns --
    the caller fetches only the (season, week) pairs these outcomes
    actually need, not every week that ever existed."""
    full = sum(1 for o in outcomes if o.followed == "full")
    partial = sum(1 for o in outcomes if o.followed == "partial")
    none_ = sum(1 for o in outcomes if o.followed == "none")

    points_left = 0.0
    for o in outcomes:
        if o.followed == "full":
            continue
        stats = weekly_points.get((o.season, o.week), {})
        for i, want in o.recommended.items():
            got = o.actual[i] if i < len(o.actual) else None
            if got == want or want is None:
                continue
            want_pts = score_stats(stats[want], scoring_settings) if want in stats else 0.0
            got_pts = (score_stats(stats[got], scoring_settings)
                      if got is not None and got in stats else 0.0)
            points_left += max(0.0, want_pts - got_pts)

    return {
        "weeks_resolved": len(outcomes),
        "weeks_full": full, "weeks_partial": partial, "weeks_none": none_,
        "points_left_on_bench": round(points_left, 1),
    }


@dataclass(frozen=True, slots=True)
class TradeOutcome:
    transaction_id: str
    roster_a_id: int
    roster_b_id: int
    side_a_value_at_trade: float
    side_b_value_at_trade: float
    side_a_current_value: float
    side_b_current_value: float


def trade_metric(outcomes: Sequence[TradeOutcome], your_roster_id: int) -> dict:
    your_trades = [o for o in outcomes if your_roster_id in (o.roster_a_id, o.roster_b_id)]
    gained = lost = unchanged = 0
    for o in your_trades:
        if o.roster_a_id == your_roster_id:
            at_trade, current = o.side_a_value_at_trade, o.side_a_current_value
        else:
            at_trade, current = o.side_b_value_at_trade, o.side_b_current_value
        delta = current - at_trade
        if delta > 0.5:
            gained += 1
        elif delta < -0.5:
            lost += 1
        else:
            unchanged += 1
    return {
        "total_trades_in_league": len(outcomes),
        "your_trades": len(your_trades),
        "gained_value": gained, "lost_value": lost, "unchanged": unchanged,
    }
