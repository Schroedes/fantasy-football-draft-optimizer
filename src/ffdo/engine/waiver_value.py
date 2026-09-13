"""Free-agent pool, positional roster caps, and the FAAB bid suggestion --
the swappable seam for sub-project #6, the way engine/pick_value.py and
engine/trade_value.py were for #5.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Final

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


def position_cap(position: str, league) -> int | None:
    if position not in POSITION_CAP_EXTRA:
        return None
    dedicated = sum(1 for s in league.roster_positions if s == position)
    flex_eligible = sum(
        1 for s in league.roster_positions
        if s in FLEX_ELIGIBILITY and position in FLEX_ELIGIBILITY[s])
    return dedicated + flex_eligible + POSITION_CAP_EXTRA[position]
