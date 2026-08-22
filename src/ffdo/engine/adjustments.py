"""Age and durability adjustments. Both default OFF until backtested.

Sleeper projects every player at a full healthy season, so availability is the
input its board structurally ignores. Age is priced by the market already, so
it only pays if the market UNDER-discounts it -- which Task 14 decides.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ffdo.domain.constants import SEASON_LENGTH
from ffdo.domain.models import PlayerProfile, SeasonStatLine

# Promoted above zero only on out-of-sample improvement (Task 14).
AGE_WEIGHT: float = 0.0
DURABILITY_WEIGHT: float = 0.0

# Beta-Binomial prior strength, in pseudo-seasons. A player with one season of
# history stays close to the positional prior; five seasons dominate it.
_PRIOR_STRENGTH = 2.0

_DEFAULT_MISS_RATE: dict[str, float] = {
    "QB": 0.10, "RB": 0.18, "WR": 0.14, "TE": 0.15,
}


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
    *,
    points_key: str = "pts_half_ppr",
) -> dict[str, dict[int, float]]:
    """Delta-method aging curves: mean change in points-per-game from age a to a+1.

    Cross-sectional averages are badly survivorship-biased -- declining players
    leave the league -- so consecutive-season deltas are used instead.
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
            prev_ppg = prev.stats.get(points_key, 0.0) / prev.games_played
            curr_ppg = curr.stats.get(points_key, 0.0) / curr.games_played
            age_then = prof.age - (2026 - prev.season)
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
            delta_ppg = (age_curve.get(prof.position) or {}).get(prof.age)
            if delta_ppg:
                entry["age"] = age_weight * delta_ppg * length

        out[player_id] = entry
    return out
