"""Coarse dynasty age/experience multiplier -- a PLACEHOLDER.

Sub-project #4 (post-draft valuation model) replaces this with a
data-driven aging model (breakout curves, position-specific decline
rates fit to real data, contract/keeper cost). Until then this is a
hand-drawn per-position curve: enough to stop a dynasty power ranking
from treating a 31-year-old RB and a 24-year-old RB as equal, not
enough to trust to a tenth of a point.

`engine.ros_value.roster_value` is the seam #4 swaps; this module is
called only from its dynasty branch.
"""

from __future__ import annotations

# (peak_lo, peak_hi, decline_per_year_after_peak_hi, floor)
_CURVE: dict[str, tuple[int, int, float, float]] = {
    "RB": (23, 26, 0.09, 0.55),
    "WR": (25, 28, 0.05, 0.60),
    "TE": (25, 29, 0.04, 0.70),
    "QB": (26, 33, 0.03, 0.75),
}
_PEAK = 1.10
_MIN, _MAX = 0.55, 1.15


def multiplier(position: str, age: int | None, years_exp: int | None) -> float:
    curve = _CURVE.get(position)
    if curve is None:            # K, DEF, anything unmapped
        return 1.0

    peak_lo, peak_hi, decline, floor = curve
    if age is None:
        base = _PEAK
    elif age < peak_lo:
        # ramping up to peak: 0.04/yr below peak_lo, so a 21-yo RB ~ 1.02
        base = _PEAK - 0.04 * (peak_lo - age)
    elif age <= peak_hi:
        base = _PEAK
    else:
        base = _PEAK - decline * (age - peak_hi)

    base = max(floor, base)

    if years_exp == 0:
        base *= 0.92
    elif years_exp == 1:
        base *= 0.98

    return max(_MIN, min(_MAX, base))
