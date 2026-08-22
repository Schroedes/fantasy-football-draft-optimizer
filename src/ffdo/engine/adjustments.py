"""Age and durability adjustments. Both default OFF until backtested.

Sleeper projects every player at a full healthy season, so availability is the
input its board structurally ignores. Age is priced by the market already, so
it only pays if the market UNDER-discounts it -- which Task 14 decides.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ffdo.domain.constants import SEASON_LENGTH
from ffdo.domain.models import PlayerProfile, SeasonStatLine
from ffdo.engine.scoring import score_stats

# Promoted above zero only on out-of-sample improvement (Task 14).
AGE_WEIGHT: float = 0.0
DURABILITY_WEIGHT: float = 0.0

# Beta-Binomial prior strength, in pseudo-seasons. A player with one season of
# history stays close to the positional prior; five seasons dominate it.
_PRIOR_STRENGTH = 2.0

_DEFAULT_MISS_RATE: dict[str, float] = {
    "QB": 0.10, "RB": 0.18, "WR": 0.14, "TE": 0.15,
}

# `profiles` is always the single `players_nfl` snapshot, which carries
# `age` as of THIS season and has no per-season history -- there is no
# 2023-vintage or 2024-vintage players file. Converting a profile's age to
# the age a player had during any other season Y is therefore always
# `age - (_PROFILE_SNAPSHOT_SEASON - Y)`, regardless of what season is
# under evaluation (e.g. during a Task 14 backtest). This anchor must stay
# fixed at 2026 everywhere it's used -- it is NOT the same quantity as a
# function's `current_season` parameter (the season being evaluated/
# projected for), which varies per call. Conflating the two was the root
# cause of the age train/apply mismatch fixed by `build()`'s lookup below.
_PROFILE_SNAPSHOT_SEASON = 2026


def expected_games_missed(
    history: Sequence[SeasonStatLine],
    position: str,
    *,
    prior_rate: float | None = None,
    current_season: int = 2026,
) -> float:
    """Expected games missed next season, shrunk toward a positional prior.

    Recent seasons carry more weight than old ones.
    """
    rate = prior_rate if prior_rate is not None else _DEFAULT_MISS_RATE.get(position, 0.15)
    length = SEASON_LENGTH[current_season]
    if not history:
        return rate * length

    missed = played = 0.0
    for line in history:
        # Halve the weight for each season further back.
        recency = 0.5 ** (current_season - line.season - 1)
        missed += recency * line.games_missed
        played += recency * line.games_played

    observed = missed + played
    prior_games = _PRIOR_STRENGTH * length
    blended = (missed + rate * prior_games) / (observed + prior_games)
    return blended * length


def fit_age_curve(
    history_by_player: Mapping[str, Sequence[SeasonStatLine]],
    profiles: Mapping[str, PlayerProfile],
    weights: Mapping[str, float],
) -> dict[str, dict[int, float]]:
    """Delta-method aging curves: mean change in points-per-game from age a to a+1.

    Cross-sectional averages are badly survivorship-biased -- declining players
    leave the league -- so consecutive-season deltas are used instead.

    Points are recomputed from raw component stats via `score_stats`, never
    read from Sleeper's precomputed `pts_*` fields: that field's definition
    changed between 2021 and 2023 (see ffdo.engine.scoring), and differencing
    it across consecutive seasons -- exactly what this function does -- is
    the case that definitional drift corrupts most directly. `weights`
    should be a fixed, comparable scoring standard (e.g.
    `ffdo.domain.constants.STANDARD_HALF_PPR`), not any one league's custom
    settings, since this curve must be comparable across historical seasons
    and leagues.
    """
    deltas: dict[str, dict[int, list[float]]] = {}
    for player_id, lines in history_by_player.items():
        prof = profiles.get(player_id)
        if prof is None or prof.age is None:
            continue
        ordered = sorted(lines, key=lambda s: s.season)
        for prev, curr in zip(ordered, ordered[1:], strict=False):
            if prev.games_played < 4 or curr.games_played < 4:
                continue
            prev_ppg = score_stats(prev.stats, weights) / prev.games_played
            curr_ppg = score_stats(curr.stats, weights) / curr.games_played
            age_then = prof.age - (_PROFILE_SNAPSHOT_SEASON - prev.season)
            deltas.setdefault(prof.position, {}).setdefault(age_then, []).append(
                curr_ppg - prev_ppg)

    return {
        position: {age: sum(vals) / len(vals) for age, vals in by_age.items() if vals}
        for position, by_age in deltas.items()
    }


def build(
    profiles: Mapping[str, PlayerProfile],
    history: Mapping[str, Sequence[SeasonStatLine]],
    points: Mapping[str, float],
    replacement_ppg: Mapping[str, float],
    *,
    age_weight: float = AGE_WEIGHT,
    durability_weight: float = DURABILITY_WEIGHT,
    age_curve: Mapping[str, Mapping[int, float]] | None = None,
    current_season: int = 2026,
) -> dict[str, dict[str, float]]:
    """Per-player point deltas, keyed by adjustment name, for the audit trail."""
    length = SEASON_LENGTH[current_season]
    out: dict[str, dict[str, float]] = {}

    for player_id, prof in profiles.items():
        entry: dict[str, float] = {}
        projected = points.get(player_id)

        if durability_weight and projected:
            missed = expected_games_missed(
                history.get(player_id, ()), prof.position,
                current_season=current_season)
            player_ppg = projected / length
            gap = player_ppg - replacement_ppg.get(prof.position, 0.0)
            # Waivers exist: a missed game costs the gap to a streamer.
            entry["durability"] = -durability_weight * max(0.0, gap) * missed

        if age_weight and age_curve and prof.age is not None:
            # prof.age is anchored to _PROFILE_SNAPSHOT_SEASON, not to
            # current_season -- shift it to the player's age AT the season
            # being evaluated before looking up the curve. This is a no-op
            # for live use (current_season == _PROFILE_SNAPSHOT_SEASON) but
            # is required for a backtest over a past current_season.
            age_at_eval = prof.age - (_PROFILE_SNAPSHOT_SEASON - current_season)
            delta_ppg = (age_curve.get(prof.position) or {}).get(age_at_eval)
            if delta_ppg:
                entry["age"] = age_weight * delta_ppg * length

        out[player_id] = entry
    return out
