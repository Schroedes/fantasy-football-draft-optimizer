"""Shapes engine output into the JSON the board renders."""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.models import DraftState, ValuedPlayer
from ffdo.engine import auction


def build_auction_board(
    league,
    state: DraftState,
    valued: Mapping[str, ValuedPlayer],
    baseline: Mapping[str, float],
) -> dict:
    factor = auction.inflation_factor(baseline, state, league)
    drafted = state.drafted_player_ids()
    spent = state.spent_by_roster()

    rows = []
    for pid, vp in valued.items():
        base = baseline.get(pid, 1.0)
        rows.append({
            "player_id": pid,
            "name": vp.profile.full_name,
            "position": vp.profile.position,
            "team": vp.profile.team,
            "age": vp.profile.age,
            "vor": round(vp.vor, 1),
            "tier": vp.tier,
            "baseline": round(base, 1),
            "adjusted": round(base * factor, 1),
            "drafted": pid in drafted,
        })
    rows.sort(key=lambda r: r["vor"], reverse=True)

    return {
        "format": "auction",
        "inflation": round(factor, 3),
        "budget": {
            "total": league.num_teams * league.budget,
            "spent": sum(spent.values()),
            "by_roster": spent,
        },
        "picks_made": len(state.picks),
        "players": rows,
    }
