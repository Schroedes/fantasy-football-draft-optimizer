"""Per-team starting-lineup value.

Same greedy slot-fill as league-wide replacement level (see
engine/replacement.py), run once per team on that team's own drafted
players, ranked by VOR instead of raw points.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ffdo.domain.models import ValuedPlayer
from ffdo.engine.replacement import greedy_fill_slots, rank_by_position


@dataclass(frozen=True, slots=True)
class TeamLineup:
    starting_vor: float
    bench_vor: float
    by_position: Mapping[str, float]
    starters: frozenset[str]


def team_lineup(roster: Mapping[str, ValuedPlayer], league) -> TeamLineup:
    """`roster` is one team's drafted, valued players (player_id -> ValuedPlayer).

    Ranking by VOR rather than raw points is required correctness, not a
    style choice: replacement level differs by position, so a FLEX
    comparison between (say) an RB and a WR is only meaningful once both
    are already expressed on the same value-above-replacement scale.
    Ranking by raw points here could seat the wrong player in FLEX -- one
    with more total production but less marginal value than the
    alternative.
    """
    vor = {pid: vp.vor for pid, vp in roster.items()}
    positions = {pid: vp.profile.position for pid, vp in roster.items()}
    ranked = rank_by_position(vor, positions)
    taken, _ = greedy_fill_slots(ranked, positions, league.starting_slots, iterations=1)

    by_position: dict[str, float] = {}
    for pid in taken:
        pos = positions[pid]
        by_position[pos] = by_position.get(pos, 0.0) + vor[pid]

    starting_vor = sum(by_position.values())
    bench_vor = sum(v for pid, v in vor.items() if pid not in taken)
    return TeamLineup(starting_vor=starting_vor, bench_vor=bench_vor,
                      by_position=by_position, starters=frozenset(taken))
