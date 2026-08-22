"""Constants that encode verified facts about Sleeper's data."""

from typing import Final

# Regular-season game count by season. The NFL moved 17 -> 18 games in 2024.
# Availability rates MUST normalize against this, never a constant.
SEASON_LENGTH: Final[dict[int, int]] = {
    2021: 17, 2022: 17, 2023: 17, 2024: 18, 2025: 18, 2026: 18,
}

OFFENSE_POSITIONS: Final[frozenset[str]] = frozenset({"QB", "RB", "WR", "TE"})

# Scoring keys credited to defensive/special-teams units, never to an
# offensive player, even when the key appears in a league's scoring settings.
_DEFENSIVE_ONLY: Final[frozenset[str]] = frozenset({"fum_rec", "fum_rec_td"})

_OFFENSE_PREFIXES: Final[tuple[str, ...]] = ("pass_", "rush_", "rec_", "bonus_")

# Bare keys (no prefix) that still score for an offensive player. `st_td`
# is included because Sleeper credits return touchdowns to the returner,
# which is verified by the Task 5 golden test.
_OFFENSE_BARE: Final[frozenset[str]] = frozenset({"rec", "fum", "fum_lost", "st_td"})


def is_offense_scoring_key(key: str) -> bool:
    """True if `key` scores for an offensive player."""
    if key in _DEFENSIVE_ONLY:
        return False
    return key.startswith(_OFFENSE_PREFIXES) or key in _OFFENSE_BARE


# Verified to reproduce Sleeper's 2025 `pts_half_ppr` for >=98% of players
# scoring 50+ points. Used ONLY as a golden-test target (Task 5).
STANDARD_HALF_PPR: Final[dict[str, float]] = {
    "pass_yd": 0.04, "pass_td": 4, "pass_int": -1, "pass_2pt": 2,
    "rush_yd": 0.1, "rush_td": 6, "rush_2pt": 2,
    "rec": 0.5, "rec_yd": 0.1, "rec_td": 6, "rec_2pt": 2,
    "fum_lost": -2, "st_td": 6,
}
