"""Multi-season historical stat lines per player, from Sleeper's
/v1/stats/nfl/regular/<season> endpoint -- the same wire format
ingest.stats.parse (already existing, unmodified) already translates, just
fetched live instead of from a frozen snapshot.

Sleeper's stats endpoint returns every player league-wide in one call, not
filtered by player_id -- `fetch_season` is therefore the unit that's
actually cacheable (one entry per season, shared across every league and
request that needs it, since finished-season stats never change).
`history_for` is a pure, uncached reshaping step: given some
already-fetched seasons' full-league stats, pick out just the requested
players across however many of those seasons they actually appear in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ffdo.domain.models import SeasonStatLine
from ffdo.ingest import stats as stats_mod
from ffdo.ingest.client import V1, SleeperClient


def fetch_season(sleeper: SleeperClient, season: int) -> dict[str, SeasonStatLine]:
    raw = sleeper.get_json(f"{V1}/stats/nfl/regular/{season}")
    return stats_mod.parse(raw, season)


def history_for(
    player_ids: Iterable[str],
    season_stats: Mapping[int, Mapping[str, SeasonStatLine]],
) -> dict[str, list[SeasonStatLine]]:
    ids = set(player_ids)
    out: dict[str, list[SeasonStatLine]] = {pid: [] for pid in ids}
    for lines in season_stats.values():
        for pid, line in lines.items():
            if pid in ids:
                out[pid].append(line)
    return out
