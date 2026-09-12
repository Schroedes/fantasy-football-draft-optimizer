"""Sums player VOR (unchanged from #4) plus draft-pick value (new, see
engine/pick_value.py) for both sides of a hypothetical trade. Deliberately
produces raw numbers only -- no fairness verdict, no roster-context
awareness (spec §2, §5): those need the roster-needs modeling a future
sub-project will build for trade-target suggestions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ffdo.domain.models import DraftPickAsset, TradeEvaluation, ValuedPlayer
from ffdo.engine import pick_value


def _side_value(
    side: Mapping[str, Sequence],
    valued_players: Mapping[str, ValuedPlayer],
    pick_curve: Mapping[int, Mapping],
    *,
    current_season: int,
    round_size: int,
) -> float:
    player_total = sum(
        valued_players[pid].vor for pid in side.get("player_ids", ())
        if pid in valued_players)
    pick_total = sum(
        pick_value.slot_value(p, pick_curve, current_season=current_season,
                              round_size=round_size)
        for p in side.get("picks", ()))
    return player_total + pick_total


def evaluate_trade(
    side_a: Mapping[str, Sequence],
    side_b: Mapping[str, Sequence],
    *,
    valued_players: Mapping[str, ValuedPlayer],
    pick_curve: Mapping[int, Mapping],
    current_season: int,
    round_size: int,
) -> TradeEvaluation:
    a_value = _side_value(side_a, valued_players, pick_curve,
                          current_season=current_season, round_size=round_size)
    b_value = _side_value(side_b, valued_players, pick_curve,
                          current_season=current_season, round_size=round_size)
    differential = a_value - b_value
    smaller = min(a_value, b_value)
    differential_pct = (differential / smaller) if smaller != 0.0 else None
    return TradeEvaluation(
        side_a_value=a_value, side_b_value=b_value,
        differential=differential, differential_pct=differential_pct)
