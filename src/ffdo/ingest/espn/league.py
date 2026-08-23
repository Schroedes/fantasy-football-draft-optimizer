"""Translates ESPN's `mSettings` view into LeagueProfile."""

from __future__ import annotations

from typing import Any

from ffdo.domain.models import LeagueProfile

# Verified live 2026-08-23 against a real league's
# settings.rosterSettings.lineupSlotCounts. Note "DEF", not ESPN's own
# "D/ST" label -- this must match Sleeper's PlayerProfile.position
# vocabulary, since Sleeper's player pool is the valuation source
# regardless of provider (Sleeper's own DEF entries use "DEF").
ESPN_SLOT_ID_TO_POSITION: dict[int, str] = {
    0: "QB", 2: "RB", 4: "WR", 6: "TE", 23: "FLEX",
    16: "DEF", 17: "K", 20: "BN", 21: "IR",
}

# Verified live 2026-08-23 against a real league's
# settings.scoringSettings.scoringItems. Only the core offense categories
# are mapped so far; defense/kicking point-bracket categories
# (recognizable by a pointsOverrides dict keyed to "16") are documented,
# intentional follow-up work -- see the design doc §5 for why that's not a
# blocker to this feature.
ESPN_STAT_ID_TO_SLEEPER_KEY: dict[int, str] = {
    3: "pass_yd", 4: "pass_td", 20: "pass_int",
    24: "rush_yd", 25: "rush_td",
    42: "rec_yd", 43: "rec_td", 53: "rec",
    72: "fum_lost",
}


def _roster_positions(lineup_slot_counts: dict[str, int]) -> tuple[str, ...]:
    positions: list[str] = []
    for slot_id_str, count in lineup_slot_counts.items():
        if count <= 0:
            continue
        position = ESPN_SLOT_ID_TO_POSITION.get(int(slot_id_str))
        if position is None:
            raise ValueError(
                f"ESPN lineup slot id {slot_id_str} (count={count}) has no "
                "entry in ESPN_SLOT_ID_TO_POSITION -- an unsupported roster "
                "slot type, not something safe to silently drop")
        positions.extend([position] * count)
    return tuple(positions)


def _scoring_settings(scoring_items: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in scoring_items:
        key = ESPN_STAT_ID_TO_SLEEPER_KEY.get(item["statId"])
        if key is not None:
            out[key] = float(item["points"])
    return out


def draft_type(raw: dict[str, Any]) -> str:
    """'SNAKE' -> 'snake', matching DraftState.draft_type's vocabulary."""
    espn_type = (raw["settings"].get("draftSettings") or {}).get("type", "")
    return espn_type.lower()


def parse(raw: dict[str, Any]) -> LeagueProfile:
    settings = raw["settings"]
    draft_settings = settings.get("draftSettings") or {}
    return LeagueProfile(
        league_id=str(raw["id"]),
        season=int(raw["seasonId"]),
        num_teams=int(settings["size"]),
        roster_positions=_roster_positions(settings["rosterSettings"]["lineupSlotCounts"]),
        scoring_settings=_scoring_settings(settings["scoringSettings"]["scoringItems"]),
        budget=draft_settings.get("auctionBudget"),
        name=settings.get("name") or "",
        status="",  # ESPN's `status` is a nested object, not a simple
                    # string like Sleeper's -- no clean single-string
                    # equivalent exists, so this is left blank rather than
                    # coerced into something misleading.
    )
