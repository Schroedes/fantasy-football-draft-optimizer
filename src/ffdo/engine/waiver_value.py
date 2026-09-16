"""Free-agent pool, positional roster caps, and the FAAB bid suggestion --
the swappable seam for sub-project #6, the way engine/pick_value.py and
engine/trade_value.py were for #5.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Final

from ffdo.domain.constants import INJURY_OUT_STATUSES
from ffdo.domain.models import PlayerProfile, RosterEntry, ValuedPlayer, WaiverRecommendation
from ffdo.engine.replacement import FLEX_ELIGIBILITY

POSITION_CAP_EXTRA: Final[dict[str, int]] = {"QB": 1, "TE": 1, "DEF": 0, "K": 0}


def suggested_bid(
    vor_gain: float,
    remaining_budget: float,
    curve: Mapping[int, float],
    *,
    bucket_width: int = 10,
) -> float:
    if not curve:
        return 0.0
    bucket = math.floor(vor_gain / bucket_width) * bucket_width
    candidates = [b for b in curve if b <= bucket]
    if not candidates:
        return 0.0
    pct = curve[max(candidates)]
    return round(remaining_budget * pct, 0)


def free_agents(all_player_ids: Iterable[str], rosters: Sequence) -> set[str]:
    rostered = {pid for r in rosters for pid in r.player_ids}
    return set(all_player_ids) - rostered


def droppable_player_ids(roster: "RosterEntry") -> tuple[str, ...]:
    """`roster.player_ids` minus IR ('reserve') and Taxi Squad slots -- both
    are restricted roster spots and must never be offered as a waiver drop
    candidate."""
    restricted = set(roster.reserve_ids) | set(roster.taxi_ids)
    return tuple(pid for pid in roster.player_ids if pid not in restricted)


def position_cap(position: str, league) -> int | None:
    if position not in POSITION_CAP_EXTRA:
        return None
    dedicated = sum(1 for s in league.roster_positions if s == position)
    flex_eligible = sum(
        1 for s in league.roster_positions
        if s in FLEX_ELIGIBILITY and position in FLEX_ELIGIBILITY[s])
    return dedicated + flex_eligible + POSITION_CAP_EXTRA[position]


def recommend_adds(
    free_agent_ids: Iterable[str],
    your_roster_ids: Iterable[str],
    valued: Mapping[str, "ValuedPlayer"],
    profiles: Mapping[str, "PlayerProfile"],
    league,
    faab_curve: Mapping[int, float] | None,
    remaining_budget: float | None,
    *,
    top_n: int = 10,
    min_vor_gain: float = 5.0,
) -> list["WaiverRecommendation"]:
    """`faab_curve`/`remaining_budget` are None for a non-FAAB
    (waiver-priority) league -- the add/drop recommendation itself
    (which free agent, who to drop, how much VOR it gains) doesn't
    depend on the acquisition model at all, only the dollar bid suggestion
    does, so every recommendation still ships with `suggested_bid=None`
    rather than being suppressed entirely."""
    your_ids = list(your_roster_ids)
    your_by_position: dict[str, list[str]] = {}
    for pid in your_ids:
        prof = profiles.get(pid)
        if prof is not None:
            your_by_position.setdefault(prof.position, []).append(pid)

    def _worst(pids: Sequence[str]) -> str | None:
        candidates = [pid for pid in pids if pid in valued]
        if not candidates:
            return None
        return min(candidates, key=lambda pid: valued[pid].vor)

    out: list[WaiverRecommendation] = []
    for fa_id in free_agent_ids:
        prof = profiles.get(fa_id)
        vp = valued.get(fa_id)
        if prof is None or vp is None:
            continue
        if not prof.active or prof.injury_status in INJURY_OUT_STATUSES:
            continue

        cap = position_cap(prof.position, league)
        at_cap = cap is not None and len(your_by_position.get(prof.position, [])) >= cap

        if at_cap:
            drop_id = _worst(your_by_position.get(prof.position, []))
        else:
            drop_id = _worst(your_ids)
        baseline = valued[drop_id].vor if drop_id is not None else 0.0

        vor_gain = vp.vor - baseline
        if vor_gain <= min_vor_gain:
            continue

        bid = (suggested_bid(vor_gain, remaining_budget, faab_curve)
              if faab_curve is not None and remaining_budget is not None else None)
        out.append(WaiverRecommendation(
            free_agent_id=fa_id, drop_player_id=drop_id, vor_gain=vor_gain,
            suggested_bid=bid))

    out.sort(key=lambda r: r.vor_gain, reverse=True)
    return out[:top_n]
