"""This week's lineup value and optimal slot assignment.

`weekly_value` scores a player's WEEKLY projection (not the rest-of-season
blend `engine.ros_value` computes) and feeds it into the same
`engine.vor.compute` #2 already reuses unchanged, after EXCLUDING players
who cannot play at all this week (on bye, or a hard-out injury status) --
exclusion, not a zero score. #2's "value 0 now, might recover later"
reasoning for a season-long asset doesn't apply here: a player who cannot
play this Sunday has no scenario in which starting them helps this week,
so they must not be eligible to fill a slot at all, not merely score low
enough to lose the comparison (a thin position with every remaining option
also scoring low could otherwise still "win" a slot by default).

`optimal_slots` is a slot-labeled sibling of `engine.roster.team_lineup`:
the IDENTICAL two-phase (dedicated-slots-first, then FLEX) greedy walk
`engine.replacement.greedy_fill_slots` already performs, reimplemented
here only to additionally record which specific `league.starting_slots`
index each pick fills -- `greedy_fill_slots` itself returns just the
aggregate starter set, which is enough for #2's power ranking but not for
a slot-by-slot diff against the provider's own positionally-aligned
starters array. Neither `greedy_fill_slots` nor `team_lineup` is modified;
for an identical roster and league this produces the identical starter
SET team_lineup would.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ffdo.domain.constants import INJURY_OUT_STATUSES
from ffdo.domain.models import PlayerProfile, ValuedPlayer, WeeklyProjection
from ffdo.engine import vor
from ffdo.engine.replacement import FLEX_ELIGIBILITY, rank_by_position
from ffdo.engine.scoring import score_stats


def weekly_value(
    player_ids: Iterable[str],
    league,
    *,
    weekly_points: Mapping[str, WeeklyProjection],
    profiles: Mapping[str, PlayerProfile],
    bye_teams: frozenset[str],
) -> dict[str, ValuedPlayer]:
    value_pts: dict[str, float] = {}
    for pid in player_ids:
        proj = weekly_points.get(pid)
        profile = profiles.get(pid)
        if proj is None or profile is None:
            continue
        if profile.team in bye_teams or profile.injury_status in INJURY_OUT_STATUSES:
            continue
        value_pts[pid] = score_stats(proj.stats, league.scoring_settings)
    return vor.compute(value_pts, profiles, league)


def optimal_slots(
    valued: Mapping[str, ValuedPlayer],
    league,
) -> dict[int, str | None]:
    vor_by_pid = {pid: vp.vor for pid, vp in valued.items()}
    positions = {pid: vp.profile.position for pid, vp in valued.items()}
    ranked = rank_by_position(vor_by_pid, positions)
    cursor: dict[str, int] = dict.fromkeys(ranked, 0)

    slots = league.starting_slots
    dedicated = [(i, s) for i, s in enumerate(slots) if s not in FLEX_ELIGIBILITY]
    flex = [(i, s) for i, s in enumerate(slots) if s in FLEX_ELIGIBILITY]

    result: dict[int, str | None] = {}
    for i, slot in dedicated:
        pool = ranked.get(slot, [])
        idx = cursor.get(slot, 0)
        if idx < len(pool):
            result[i] = pool[idx][1]
            cursor[slot] = idx + 1
        else:
            result[i] = None

    for i, slot in flex:
        eligible = FLEX_ELIGIBILITY[slot]
        best: tuple[float, str] | None = None
        for pos in eligible:
            pool = ranked.get(pos, [])
            idx = cursor.get(pos, 0)
            if idx < len(pool) and (best is None or pool[idx][0] > best[0]):
                best = pool[idx]
        if best is not None:
            pos = positions[best[1]]
            result[i] = best[1]
            cursor[pos] = cursor.get(pos, 0) + 1
        else:
            result[i] = None

    return result
