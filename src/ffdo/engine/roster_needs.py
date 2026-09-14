# src/ffdo/engine/roster_needs.py
"""How urgently a team needs help at each position -- QB/RB/WR/TE, blending
two signals into one severity score: how weak the team's starters are at
that position league-wide (reusing power_ranking's existing ranking), and
how thin their bench depth is there RELATIVE TO WHAT THIS LEAGUE'S OWN
ROSTER CONSTRUCTION ACTUALLY EXPECTS -- reusing sub-project #6's
POSITION_CAP_EXTRA (waiver_value.position_cap), so a league with little
bench room anywhere never reads DEF/K as "thin" just because nobody
carries a backup there.

Used by api.app's POST /trade/evaluate to show how a hypothetical trade
would change either side's needs -- see position_needs's docstring for how
a hypothetical (post-trade) roster gets scored without touching any other
team's real, current standing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ffdo.domain.models import RosterEntry, ValuedPlayer
from ffdo.engine import power_ranking
from ffdo.engine.replacement import FLEX_ELIGIBILITY
from ffdo.engine.waiver_value import position_cap

_POSITIONS = ("QB", "RB", "WR", "TE")


@dataclass(frozen=True, slots=True)
class NeedScore:
    rank: int
    severity: str  # "Severe" | "Moderate" | "Fine"


def _weakness(rank: int, num_teams: int) -> float:
    if num_teams <= 1:
        return 0.0
    return (rank - 1) / (num_teams - 1)


def _thinness(position: str, your_count: int, league) -> float:
    dedicated = sum(1 for s in league.roster_positions if s == position)
    flex_eligible = sum(
        1 for s in league.roster_positions
        if s in FLEX_ELIGIBILITY and position in FLEX_ELIGIBILITY[s])
    starting_reach = dedicated + flex_eligible
    cap = position_cap(position, league)
    if cap is not None:
        if cap <= starting_reach:
            # This league expects zero bench depth here (e.g. DEF/K) --
            # never penalize for lacking a backup nobody expects you to
            # roster.
            return 0.0 if your_count >= starting_reach else 1.0
        return max(0.0, min(1.0, 1.0 - (your_count - starting_reach) / (cap - starting_reach)))
    # No fixed "ideal" bench count exists for an uncapped position (RB/WR)
    # -- thinness decays smoothly with each extra player instead of
    # demanding a specific target.
    extra = max(0, your_count - starting_reach)
    return 1.0 / (1.0 + extra)


def _severity(score: float) -> str:
    if score >= 0.66:
        return "Severe"
    if score >= 0.33:
        return "Moderate"
    return "Fine"


def position_needs(
    entry: RosterEntry,
    all_rosters: list[RosterEntry],
    valued: Mapping[str, ValuedPlayer],
    league,
) -> dict[str, NeedScore]:
    """`entry` may be a real, unmodified RosterEntry (to score a team's
    current needs) or a hypothetical one built via `dataclasses.replace`
    with a different `player_ids` set (to score a "what if" roster). Either
    way, `entry` is substituted into `all_rosters` at its own `roster_id`
    before ranking -- reusing `power_ranking.rank` directly rather than
    re-deriving its sort -- so every OTHER team's value stays frozen at its
    real, current state. `entry.roster_id` must already exist somewhere in
    `all_rosters`; every caller passes a roster list that includes the team
    being scored.
    """
    substituted = [entry if r.roster_id == entry.roster_id else r for r in all_rosters]
    num_teams = len(substituted)
    out: dict[str, NeedScore] = {}
    for position in _POSITIONS:
        rows = power_ranking.rank(
            substituted, valued, league, {}, None, position=position, scope="starters")
        rank = next(r.power_rank for r in rows if r.roster_id == entry.roster_id)
        your_count = sum(
            1 for pid in entry.player_ids
            if pid in valued and valued[pid].profile.position == position)
        score = 0.5 * _weakness(rank, num_teams) + 0.5 * _thinness(position, your_count, league)
        out[position] = NeedScore(rank=rank, severity=_severity(score))
    return out
