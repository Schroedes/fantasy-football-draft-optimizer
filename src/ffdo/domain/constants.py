"""Constants that encode verified facts about Sleeper's data."""

from typing import Final

# Regular-season game count by season. The NFL moved 17 -> 18 games in 2024.
# Availability rates MUST normalize against this, never a constant.
SEASON_LENGTH: Final[dict[int, int]] = {
    2021: 17, 2022: 17, 2023: 17, 2024: 18, 2025: 18, 2026: 18,
}

# NFL bye weeks by season. Hand-maintained -- update each August when the
# schedule is released. Team abbreviations match Sleeper's `team` field on
# PlayerProfile (e.g. "ARI", "BAL", "LAR"). PLACEHOLDER for 2026: drawn from
# a plausible distribution, NOT yet verified against the real published
# schedule -- treat every 2026 bye-week value below as provisional until
# checked.
NFL_BYE_WEEKS: Final[dict[int, dict[str, int]]] = {
    2026: {
        "ARI": 8, "ATL": 5, "BAL": 7, "BUF": 7, "CAR": 14, "CHI": 5, "CIN": 10,
        "CLE": 9, "DAL": 10, "DEN": 12, "DET": 8, "GB": 5, "HOU": 6, "IND": 11,
        "JAX": 8, "KC": 10, "LAC": 12, "LAR": 8, "LV": 8, "MIA": 12, "MIN": 6,
        "NE": 14, "NO": 11, "NYG": 11, "NYJ": 9, "PHI": 9, "PIT": 5, "SEA": 8,
        "SF": 14, "TB": 9, "TEN": 10, "WAS": 12,
    },
}

# Injury statuses that mean a player cannot take the field at all, shared
# by engine/ros_value.py (zeroes a rest-of-season value for these) and
# engine/weekly_lineup.py (excludes these entirely from a weekly solve --
# see that module's docstring for why exclusion, not zeroing, is correct
# there).
INJURY_OUT_STATUSES: Final[frozenset[str]] = frozenset({"IR", "PUP", "Out", "Sus"})

OFFENSE_POSITIONS: Final[frozenset[str]] = frozenset({"QB", "RB", "WR", "TE"})

# Empirically fit from real historical Sleeper stats via
# scripts/fit_age_curve.py -- mean change in points-per-game (under
# STANDARD_HALF_PPR scoring) from age N to age N+1, delta-method
# (survivorship-bias-aware; see engine/adjustments.py::fit_age_curve).
# Repurposed here for dynasty multi-year valuation (engine/dynasty_value.py)
# from its ORIGINAL use as a rejected redraft points-adjustment -- see
# docs/superpowers/specs/2026-09-12-valuation-model-design.md §0 for why
# a "no" for one question doesn't invalidate the data for a different one.
# Re-fit periodically via scripts/refresh_snapshot.py + scripts/fit_age_curve.py
# as more seasons of real data accumulate.
# Fit from data/snapshots/2026-09-12, seasons [2021, 2022, 2023, 2024, 2025, 2026].
# That snapshot directory is NOT tracked in git (see the 2026-09-12 sub-project's
# SDD ledger, Task 1's ruling) -- re-fitting from the older, tracked
# 2026-08-22-draft-day snapshot will NOT reproduce these exact values, since it
# covers different/fewer seasons. This is expected, not a bug.
DYNASTY_AGE_CURVE: Final[dict[str, dict[int, float]]] = {
    "C": {
        21: 0.0, 22: 0.0, 23: 0.0, 24: 0.0, 25: 0.0, 26: -0.0182,
        27: 0.0, 28: 0.0, 29: 0.0, 30: 0.0, 31: 0.0, 32: 0.0,
    },
    "CB": {
        19: 0.0, 20: 0.0, 21: 0.0, 22: 0.0319, 23: -0.0194, 24: 0.007,
        25: -0.0176, 26: 0.0008, 27: -0.0085, 28: 0.0072, 29: -0.0089,
        30: 0.0179, 31: 0.0, 32: 0.0, 33: 0.0, 34: 0.0,
    },
    "DB": {
        19: 0.0, 20: 0.0, 21: 0.0094, 22: -0.0077, 23: 0.0112, 24: -0.0056,
        25: -0.0023, 26: 0.0037, 27: -0.0032, 28: 0.0065, 29: -0.0126,
        30: 0.0, 31: 0.0441, 32: 0.0, 33: 0.0, 34: 0.0, 35: 0.0,
    },
    "DE": {
        21: 0.0, 22: 0.0, 23: -0.0093, 24: 0.0022, 25: 0.0064, 26: 0.0162,
        27: 0.0, 28: 0.0, 29: 0.0, 30: 0.0, 31: 0.0, 32: 0.0, 33: 0.0,
        34: 0.0, 35: -0.0392, 36: 0.0, 37: 0.0, 38: 0.0,
    },
    "DL": {
        21: 0.0, 22: 0.0268, 23: 0.0098, 24: 0.0141, 25: 0.0, 26: 0.0,
        27: 0.0, 29: 0.0, 30: 0.0, 31: 0.0,
    },
    "DT": {
        20: 0.0, 21: 0.0, 22: -0.0107, 23: 0.0141, 24: -0.0017, 25: 0.0103,
        26: -0.0128, 27: 0.0116, 28: -0.0033, 29: 0.0, 30: 0.0, 31: 0.0,
        32: 0.0, 33: 0.0, 34: 0.0, 35: 0.0,
    },
    "FB": {
        18: 0.0, 22: -0.675, 23: 0.1575, 24: 0.3689, 25: -0.1523,
        26: -0.3339, 27: -0.0053, 28: -0.4279, 29: -0.2142, 30: -0.0766,
        31: 0.1101, 32: 0.9765, 33: -0.1824,
    },
    "FS": {25: 0.0},
    "G": {
        21: 0.0, 22: 0.0, 23: 0.0, 24: 0.0014, 25: -0.014, 26: -0.0022,
        27: 0.0, 28: 0.0, 29: 0.0, 30: 0.0, 31: 0.0,
    },
    "K": {
        21: 0.0, 22: 0.0, 23: 0.0, 24: 0.0, 25: 0.0, 26: 0.0, 27: 0.0,
        28: 0.0, 29: 0.0025, 30: 0.0, 31: 0.0, 32: 0.0, 33: 0.0, 34: 0.0,
        35: 0.0, 36: 0.0, 37: 0.0, 38: 0.0, 39: 0.0,
    },
    "LB": {
        19: 0.0, 20: 0.0, 21: 0.001, 22: 0.0, 23: 0.0007, 24: 0.0025,
        25: -0.001, 26: -0.0057, 27: -0.0035, 28: -0.0015, 29: 0.0,
        30: 0.0, 31: 0.0, 32: -0.0038, 33: -0.0027, 34: 0.0, 35: 0.0,
    },
    "LS": {
        20: 0.0, 21: 0.0, 23: 0.0, 24: 0.0, 25: 0.0, 26: 0.0, 27: 0.0,
        28: 0.0, 29: 0.0, 30: 0.0, 31: 0.0, 32: 0.0, 33: 0.0, 34: 0.0,
        35: 0.0, 36: 0.0, 37: 0.0, 38: 0.0,
    },
    "NT": {21: 0.0, 22: 0.0, 23: 0.0, 24: 0.0, 28: 0.0, 29: 0.0},
    "OG": {
        23: 0.0, 24: 0.0, 25: 0.0, 26: 0.0, 27: 0.0, 28: 0.0, 29: 0.0,
        30: 0.0, 31: 0.0, 32: -0.0294, 33: 0.0441, 34: 0.0,
    },
    "OL": {
        21: -0.0025, 22: 0.01, 23: -0.0037, 24: -0.0016, 25: 0.0034,
        26: 0.0, 27: -0.0231, 28: 0.0, 29: 0.0, 30: 0.0, 31: 0.0015,
        32: -0.0024, 33: 0.0, 34: 0.0, 38: 0.0, 39: 0.0,
    },
    "OT": {
        22: 0.0, 23: 0.0, 24: 0.0493, 25: -0.0986, 26: 0.0, 27: 0.0,
        28: 0.0, 29: 0.0, 30: 0.0, 31: 0.0, 32: 0.0,
    },
    "P": {
        20: 0.0, 21: 0.0, 22: -0.0722, 23: 0.0301, 24: 0.0074, 25: -0.0327,
        26: -0.0166, 27: 0.0225, 28: -0.0165, 29: -0.0053, 30: 0.004,
        31: 0.0212, 32: -0.0207, 33: 0.0709, 34: -0.0216, 35: 0.0558,
        36: -0.0424, 37: 0.0,
    },
    "QB": {
        21: -0.8013, 22: 3.1739, 23: -1.6631, 24: -0.4403, 25: -0.4058,
        26: 0.3809, 27: 0.5588, 28: -1.3864, 29: -1.8028, 30: -1.1651,
        31: 1.8465, 32: -0.3352, 33: -1.8307, 34: 1.2926, 35: -5.3298,
        36: -1.3033, 37: 2.5832, 38: -1.6835, 39: -1.2042, 40: -3.6906,
    },
    "RB": {
        19: 0.2222, 20: 1.1309, 21: 0.169, 22: 0.0228, 23: 0.0565,
        24: -0.6424, 25: -0.697, 26: -0.9339, 27: -1.4228, 28: -0.3564,
        29: -0.1183, 30: -3.5313, 31: -2.3644,
    },
    "SS": {24: 0.0, 25: 0.0, 26: 0.0, 27: 0.0, 28: 0.0},
    "T": {
        20: 0.0412, 21: -0.0412, 22: -0.0573, 23: 0.0, 24: -0.0137,
        25: 0.0162, 26: -0.0246, 27: -0.0013, 28: 0.0139, 29: -0.0036,
        30: -0.0431, 31: -0.1077, 32: 0.0, 33: 0.0, 34: 0.0, 35: 0.0,
        36: 0.0,
    },
    "TE": {
        20: -2.2282, 21: -0.608, 22: 0.5444, 23: 0.2101, 24: 0.1225,
        25: -0.415, 26: -0.4551, 27: -0.1471, 28: -0.6691, 29: -0.9826,
        30: -0.0482, 31: -0.9445, 32: 0.5468, 33: -3.9096, 34: -0.0518,
        36: -0.5471, 37: -0.7471, 38: -0.6, 39: -0.0412,
    },
    "WR": {
        19: -1.4534, 20: 0.7248, 21: 0.3995, 22: -0.1336, 23: 0.1011,
        24: -0.0639, 25: -1.041, 26: -0.4242, 27: -0.5784, 28: -1.7157,
        29: -1.9996, 30: -1.1826, 31: -2.3586, 32: -0.3354, 33: -4.8344,
        34: 0.0,
    },
}

# Fit from Task 2's real run -- paste the EXACT printed output here,
# including its leading comment line recording which leagues/seasons/
# MIN_SAMPLE it was fit from. Do not hand-edit the pasted values.
# Fit from 2026-09-12, leagues ['1312210128811872256', '1312145369592766464'], seasons [2025, 2026], MIN_SAMPLE=3
PICK_VALUE_CURVE: Final[dict[int, dict]] = {
    1: {'exact': {1: 37.4, 2: -11.55, 3: 15.3, 4: 17.1, 5: 7.99, 6: 8.25, 7: 16.98, 8: -53.6, 9: 14.45, 10: 15.58, 11: -32.85, 12: 21.22}, 'early': 14.51, 'mid': -5.99, 'late': 4.6, 'round_avg': 4.38},
    2: {'exact': {1: -20.9, 2: -18.9, 3: -34.22, 4: 9.42, 5: -17.57, 6: -38.47, 7: -46.08, 8: -35.0, 9: -26.98, 10: -23.1, 11: -51.95, 12: -52.42}, 'early': -16.15, 'mid': -34.28, 'late': -38.61, 'round_avg': -29.68},
    3: {'exact': {1: -80.03, 2: -14.48, 3: -27.43, 4: -48.65, 5: -19.2, 6: -36.45, 7: -51.8, 8: -43.77, 9: -56.1, 10: -16.6, 11: -33.15, 12: -40.27}, 'early': -42.65, 'mid': -37.81, 'late': -36.53, 'round_avg': -39.0},
    4: {'exact': {}, 'early': -26.42, 'mid': -60.37, 'late': -69.29, 'round_avg': -52.03},
    5: {'exact': {}, 'early': -46.47, 'mid': -51.75, 'late': -47.35, 'round_avg': -48.53},
}

# Scoring keys excluded from offense classification even when they match an
# offense-vocabulary prefix or bare key. `fum_rec`/`fum_rec_td` are credited
# to defensive/special-teams units, never to an offensive player. `pass_int_td`
# is different in kind -- it IS a passer (offensive) stat by name, but is
# excluded anyway because it also appears on real DEF rows (cross-position
# ambiguity), matching the identical exclusion already applied in
# `is_defense_scoring_key`'s docstring below. In all cases: never credited,
# even when the key appears in a league's scoring settings.
_DEFENSIVE_ONLY: Final[frozenset[str]] = frozenset({
    "fum_rec", "fum_rec_td", "pass_int_td",
})

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
#
# `def_kr_td` / `def_pr_td` (kickoff/punt-return touchdown) are
# deliberately NOT recognized, despite being real Sleeper-vocabulary keys
# a league can weight nonzero. Verified against the real 2026 projections
# snapshot (`data/snapshots/2026-08-22-draft-day/projections_2026.json.gz`):
# `def_kr_td` appears on 8 real rostered WR/RB rows (e.g. Rashid Shaheed,
# KaVontae Turpin, Jadarian Price) -- Sleeper credits kickoff/punt-return
# touchdowns to the individual returner, not exclusively to the team-DEF
# entity, so this key can fire for BOTH a DEF row and a real offensive
# skill-position player. `score_stats` has no position parameter (the
# whole point of Approach A), so there is no safe way to recognize this key
# only for DEF -- recognizing it here would silently give a return
# specialist +6 (or whatever a league weights it) they aren't owed. The
# real Sleeper-vocabulary key for punt returns is bare `pr_td` (not
# `def_pr_td`, which never appears in real data at all, in projections or
# actuals) -- `pr_td` was independently verified to leak onto real
# offensive WR rows the same way (e.g. Marvin Mims, 2026 projections), so
# it is excluded for the identical reason and is not listed here either.
# Net effect: a league's return-TD weight for team DEF goes unused, a
# real, documented trade-off (same shape as the points-allowed exclusion
# above), not a silent gap.
#
# `def_st_ff` / `def_st_fum_rec` were investigated (per design doc §6.2's
# mandated golden test) as candidate additions -- they are real Sleeper
# keys, appear at nonzero weight in a real connected league, and (unlike
# `def_kr_td`) never appear on offensive rows. They are deliberately NOT
# added, however: verified against every real 2025 DEF stat line
# (`stats_2025.json.gz`), `def_st_ff` is always <= the bare `ff` value for
# the same team-week, and `def_st_fum_rec` is always <= the bare `fum_rec`
# value, with zero exceptions -- they are the special-teams-play SUBSET of
# the already-recognized `ff`/`fum_rec` totals, not an additive category.
# Confirmed empirically: recomputing the golden test with `def_st_ff`/
# `def_st_fum_rec` added on top of `ff`/`fum_rec` makes the reproduction
# of Sleeper's own `pts_half_ppr` measurably WORSE (mean unexplained
# residual rises from ~2.9 to ~4.2 points/team after backing out the
# points-allowed component), confirming double-counting rather than a
# missing category.
_DEFENSE_BARE: Final[frozenset[str]] = frozenset({
    "sack", "int", "fum_rec", "blk_kick", "safe", "ff",
    "def_td", "def_st_td", "def_fum_td",
})


def is_defense_scoring_key(key: str) -> bool:
    """True if `key` scores for a team-defense (`DEF`) player.

    `pass_int_td` (an interception returned for a touchdown, credited
    against the passer) is also deliberately unrecognized here and in
    `is_offense_scoring_key`. Verified against real data: it appears on 24
    real QB rows in the 2025 actual-stats snapshot AND on real DEF rows in
    the 2026 projections snapshot -- the same cross-position ambiguity as
    `def_kr_td`/`pr_td` above, just smaller in magnitude (rare, 1-2 points
    typically). Excluded rather than guessed onto one side or the other.
    """
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
