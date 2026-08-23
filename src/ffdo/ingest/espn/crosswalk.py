"""ESPN player_id -> Sleeper player_id resolution.

Two independent lookups feed the individual-player crosswalk: `espn_id`
already present on many Sleeper player records (free, no extra network
call, but only ~46% coverage on active skill players -- verified
2026-08-23), and a normalized-name+position fallback for everyone that
misses. Team defenses need a third, completely different lookup (verified
live 2026-08-23) since Sleeper represents them by team abbreviation, not a
numbered player, and ESPN represents them by a stable pro-team ID with no
useful "name" to normalize-match against.

See docs/superpowers/specs/2026-08-23-espn-league-support-design.md §4 for
the full rationale, including why this module's two ID tables
(ESPN_PLAYER_POSITION_ID_TO_POSITION here vs. ESPN_SLOT_ID_TO_POSITION in
ingest/espn/league.py) must not be confused with each other -- they use
different numbering despite some coincidentally-shared values.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ffdo.domain.models import PlayerProfile

_logger = logging.getLogger(__name__)

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    """Lowercase; strip periods/apostrophes and suffixes; collapse whitespace."""
    cleaned = re.sub(r"[.']", "", name.lower())
    words = [w for w in cleaned.split() if w not in _SUFFIXES]
    return " ".join(words)


@dataclass(frozen=True, slots=True)
class Crosswalk:
    espn_to_sleeper: Mapping[str, str]
    unmatched: tuple[str, ...]


# Verified live 2026-08-23 against ESPN's real player-pool response
# (/seasons/{season}/players?view=kona_player_info): each entry's
# `defaultPositionId` uses ESPN's classic per-player position numbering,
# confirmed against six real players by name (Nick Chubb=2/RB, DeAndre
# Hopkins=3/WR, Travis Vokolek=4/TE, Geno Smith=1/QB, Dustin Hopkins=5/K,
# Falcons D/ST=16/DEF). This is NOT the same table as
# ingest.espn.league.ESPN_SLOT_ID_TO_POSITION (roster lineup slots) --
# they diverge (WR is slot 4 but position 3; TE is slot 6 but position 4).
ESPN_PLAYER_POSITION_ID_TO_POSITION: dict[int, str] = {
    1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF",
}

# Verified live 2026-08-23: fetched all 32 real ESPN team defenses
# (proTeamId + name) and cross-referenced each against Sleeper's own DEF
# entries in players_nfl.json.gz (which use the team abbreviation itself as
# player_id). ESPN's pro-team IDs skip 31/32 and place the Ravens/Texans at
# 33/34 -- an artifact of when those franchises were assigned IDs, not a
# bug in this table.
ESPN_PRO_TEAM_ID_TO_SLEEPER_DEF_ID: dict[int, str] = {
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR",
    15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
    21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA",
    27: "TB", 28: "WAS", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}


def parse_player_pool(raw: list[dict[str, Any]]) -> dict[str, tuple[str, str, int]]:
    """`raw` is the flat list of player objects ESPN's player-pool endpoint
    returns (verified live 2026-08-23 -- each entry has `id`, `fullName`,
    `defaultPositionId`, `proTeamId` directly on it, no wrapper). Entries
    at a position this project doesn't map (IDP categories like LB/DE/CB,
    which a real unfiltered response includes) are silently skipped, same
    as engine.vor excludes a position with no replacement level.
    """
    out: dict[str, tuple[str, str, int]] = {}
    for entry in raw:
        espn_id = entry.get("id")
        position = ESPN_PLAYER_POSITION_ID_TO_POSITION.get(entry.get("defaultPositionId"))
        full_name = entry.get("fullName")
        pro_team_id = entry.get("proTeamId")
        if espn_id is not None and position is not None and full_name:
            out[str(espn_id)] = (full_name, position, pro_team_id)
    return out


def build(
    espn_id_index: Mapping[str, str],
    profiles: Mapping[str, PlayerProfile],
    espn_players: Mapping[str, tuple[str, str, int]],
) -> Crosswalk:
    sleeper_by_espn_id = {espn_id: sleeper_id
                          for sleeper_id, espn_id in espn_id_index.items()}

    by_name_position: dict[tuple[str, str], list[str]] = {}
    for sleeper_id, profile in profiles.items():
        key = (normalize_name(profile.full_name), profile.position)
        by_name_position.setdefault(key, []).append(sleeper_id)

    resolved: dict[str, str] = {}
    unmatched: list[str] = []
    for espn_id, (full_name, position, pro_team_id) in espn_players.items():
        if position == "DEF":
            sleeper_id = ESPN_PRO_TEAM_ID_TO_SLEEPER_DEF_ID.get(pro_team_id)
            if sleeper_id is not None:
                resolved[espn_id] = sleeper_id
            else:
                unmatched.append(espn_id)
                _logger.warning(
                    "ESPN crosswalk: no Sleeper DEF for pro_team_id=%s "
                    "(espn_id=%s, %r)", pro_team_id, espn_id, full_name)
            continue

        sleeper_id = sleeper_by_espn_id.get(espn_id)
        if sleeper_id is not None:
            resolved[espn_id] = sleeper_id
            continue

        candidates = by_name_position.get((normalize_name(full_name), position), [])
        if len(candidates) == 1:
            resolved[espn_id] = candidates[0]
        else:
            unmatched.append(espn_id)
            # Explicit and logged, never silent -- same discipline the
            # original design applies to its market-calibration fallback.
            # `unmatched` (returned below) is the structured form a caller
            # can act on; this warning is what makes a real gap visible in
            # the server's own logs at connect/poll time, not just
            # theoretically inspectable.
            _logger.warning(
                "ESPN crosswalk: %s match for espn_id=%s (%r, %s) -- %d "
                "candidate(s) in Sleeper's pool",
                "ambiguous" if candidates else "no", espn_id, full_name,
                position, len(candidates))

    return Crosswalk(espn_to_sleeper=resolved, unmatched=tuple(unmatched))
