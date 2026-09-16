"""League power ranking -- teams ordered by roster value, luck removed.

Overall value uses the existing greedy starting-lineup fill
(`engine.roster.team_lineup`), which handles FLEX / superflex from
`roster_positions` alone. Position rankings sum a team's value at one
position; under scope="starters" only players the lineup fill actually
started count (a WR started in FLEX counts toward WR)."""

from __future__ import annotations

from collections.abc import Mapping

from ffdo.domain.models import PowerRow, RosterEntry, ValuedPlayer
from ffdo.engine.roster import team_lineup

_POSITIONS = ("QB", "RB", "WR", "TE")


def team_value(
    entry: RosterEntry,
    valued: Mapping[str, ValuedPlayer],
    league,
    *,
    position: str,
    scope: str,
    clip_negative: bool = False,
) -> tuple[float, float]:
    """Returns (value, bench_value) for one team at one (position, scope).

    `clip_negative` floors each player's own VOR at 0.0 before summing --
    display-only, for screens where a below-replacement bench player
    dragging the team *total* negative reads as broken rather than as the
    (correct) fact that he's worth less than a free-agent pickup. Who
    starts (`lineup.starters`, from `team_lineup`) is still decided by the
    real, unclipped VOR -- only the printed sum changes. Defaults to False
    so every existing caller (`rank()`, and through it `roster_needs.py`
    and `trade_targets.py`, whose before/after trade deltas need the real
    zero-anchored numbers to stay additive) is unaffected.
    """
    team_valued = {pid: valued[pid] for pid in entry.player_ids if pid in valued}
    lineup = team_lineup(team_valued, league)

    def contrib(vp: ValuedPlayer) -> float:
        return max(vp.vor, 0.0) if clip_negative else vp.vor

    if position == "OVR":
        starting = sum(contrib(vp) for pid, vp in team_valued.items() if pid in lineup.starters)
        if scope == "full":
            bench = sum(contrib(vp) for pid, vp in team_valued.items() if pid not in lineup.starters)
            return starting + bench, bench
        return starting, 0.0

    at_pos = {pid: vp for pid, vp in team_valued.items() if vp.profile.position == position}
    started = sum(contrib(vp) for pid, vp in at_pos.items() if pid in lineup.starters)
    if scope == "starters":
        return started, 0.0
    total = sum(contrib(vp) for vp in at_pos.values())
    return total, total - started


def rank(
    rosters: list[RosterEntry],
    valued: Mapping[str, ValuedPlayer],
    league,
    standings_rank: Mapping[int, int],
    your_roster_id: int | None,
    *,
    position: str,
    scope: str,
) -> list[PowerRow]:
    scored = []
    for entry in rosters:
        value, bench = team_value(entry, valued, league, position=position, scope=scope)
        scored.append((entry, value, bench))

    scored.sort(key=lambda t: (-t[1], t[0].roster_id))   # value desc, roster_id for determinism

    return [
        PowerRow(
            roster_id=entry.roster_id,
            team_name=entry.team_name,
            is_you=entry.roster_id == your_roster_id,
            value=round(value, 1),
            bench_value=round(bench, 1),
            power_rank=i + 1,
            standings_rank=standings_rank.get(entry.roster_id, i + 1),
        )
        for i, (entry, value, bench) in enumerate(scored)
    ]
