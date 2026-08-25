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


# Points-allowed and yards-allowed brackets (`pts_allow_*`, `yds_allow_*`)
# are deliberately NOT recognized here. Sleeper's season *projections* for
# these brackets look like placeholder noise rather than a real weekly
# forecast -- e.g. the top-projected 2026 DEF unit shows real turnover
# projections (52 sacks, 15 INTs) alongside `pts_allow_0: 1.0` and `gp:
# 1.0`, inconsistent with a genuine per-week bracket forecast for a
# projected starter. A league's points/yards-allowed scoring weights go
# unused for projection-based valuation as a result -- excluded rather
# than guessed wrong, the same philosophy `vor.compute` already applies to
# a position with no replacement level. See design doc §3.2.
_DEFENSE_BARE: Final[frozenset[str]] = frozenset({
    "sack", "int", "fum_rec", "blk_kick", "safe", "ff",
    "def_td", "def_st_td", "def_kr_td", "def_pr_td", "def_fum_td",
})


def is_defense_scoring_key(key: str) -> bool:
    """True if `key` scores for a team-defense (`DEF`) player."""
    return key in _DEFENSE_BARE


_KICKING_PREFIXES: Final[tuple[str, ...]] = ("fgm_", "fgmiss_")
_KICKING_BARE: Final[frozenset[str]] = frozenset({
    "fgm", "fga", "fgmiss", "xpm", "xpa", "xpmiss",
})


def is_kicking_scoring_key(key: str) -> bool:
    """True if `key` scores for a kicker (`K`) player.

    Prefix-matching `fgm_`/`fgmiss_` sweeps in every distance bracket
    (`fgm_20_29`, `fgm_50p`, `fgm_60p`, ...) plus a few non-scoring
    magnitude fields (`fgm_yds`, `fgm_lng`, `fgm_pct`) that happen to share
    the prefix. That over-match is harmless: `score_stats` only sums keys a
    league's `scoring_settings` actually assigns a weight to, and no real
    league scores kicking by total FG yardage or long-FG distance rather
    than by make/miss count -- so those extra keys are never weighted in
    practice.
    """
    return key.startswith(_KICKING_PREFIXES) or key in _KICKING_BARE


# Verified to reproduce Sleeper's 2025 `pts_half_ppr` for >=98% of players
# scoring 50+ points. Used ONLY as a golden-test target (Task 5).
STANDARD_HALF_PPR: Final[dict[str, float]] = {
    "pass_yd": 0.04, "pass_td": 4, "pass_int": -1, "pass_2pt": 2,
    "rush_yd": 0.1, "rush_td": 6, "rush_2pt": 2,
    "rec": 0.5, "rec_yd": 0.1, "rec_td": 6, "rec_2pt": 2,
    "fum_lost": -2, "st_td": 6,
}
