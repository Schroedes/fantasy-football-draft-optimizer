"""Gives a draft pick a VOR-equivalent value, fit from real rookie-draft
history (see scripts/fit_pick_value.py, domain/constants.py::PICK_VALUE_CURVE).
The swappable seam for sub-project #5, the way engine/dynasty_value.py was
for #4."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from ffdo.domain.models import DraftPickAsset

PICK_UNCERTAINTY_DISCOUNT_RATE: Final[float] = 0.20  # per year; a separate,
    # larger discount than dynasty_value.DISCOUNT_RATE (0.15) -- that
    # discounts a KNOWN player's future production, this discounts not
    # knowing who the pick even becomes yet. Not backtested (no ground
    # truth exists for "value assigned at trade time" vs. "value once
    # realized"), same honest posture as DISCOUNT_RATE.


def _tertile(pick_in_round: int, round_size: int) -> str:
    third = max(1, round(round_size / 3))
    if pick_in_round <= third:
        return "early"
    if pick_in_round <= 2 * third:
        return "mid"
    return "late"


def slot_value(
    pick: DraftPickAsset,
    curve: Mapping[int, Mapping],
    *,
    current_season: int,
    round_size: int,
    discount_rate: float = PICK_UNCERTAINTY_DISCOUNT_RATE,
) -> float:
    round_data = curve.get(pick.round, {})
    base = None
    if pick.projected_slot is not None:
        base = round_data.get("exact", {}).get(pick.projected_slot)
        if base is None:
            tertile = _tertile(pick.projected_slot, round_size)
            base = round_data.get(tertile)
    if base is None:
        base = round_data.get("round_avg", 0.0)
    years_out = int(pick.season) - current_season
    return base / (1.0 + discount_rate) ** max(0, years_out)
