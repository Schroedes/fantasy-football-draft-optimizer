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


def _team_value(
    entry: RosterEntry,
    valued: Mapping[str, ValuedPlayer],
    league,
    *,
    position: str,
    scope: str,
) -> tuple[float, float]:
    """Returns (value, bench_value) for one team at one (position, scope)."""
    team_valued = {pid: valued[pid] for pid in entry.player_ids if pid in valued}
    lineup = team_lineup(team_valued, league)

    if position == "OVR":
        if scope == "full":
            return lineup.starting_vor + lineup.bench_vor, lineup.bench_vor
        return lineup.starting_vor, 0.0

    at_pos = {pid: vp for pid, vp in team_valued.items()
              if vp.profile.position == position}
    started = sum(vp.vor for pid, vp in at_pos.items() if pid in lineup.starters)
    if scope == "starters":
        return started, 0.0
    total = sum(vp.vor for vp in at_pos.values())
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
        value, bench = _team_value(entry, valued, league, position=position, scope=scope)
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
