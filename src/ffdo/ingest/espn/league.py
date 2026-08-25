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
# settings.scoringSettings.scoringItems.
#
# Several defense/kicking categories carry their real weight in
# `pointsOverrides["16"]` (ESPN's D/ST lineup-slot id) rather than the
# base `points` field -- `_scoring_settings` below reads the override when
# present. Categories investigated and deliberately left unmapped:
#   - statId 63 (fumbleRecoveredForTD): no D/ST-slot override in the real
#     fixture, meaning it doesn't apply to team defense; Sleeper's
#     `fum_rec_td` key is already excluded from offense scoring too (see
#     `domain.constants._DEFENSIVE_ONLY`), so mapping it here would credit
#     no one and risk colliding with a future offense-side use of the key.
#   - statId 93 (defensiveBlockedKickForTouchdowns): would collide with
#     statId 103's `def_td` mapping below (both score 6.0 in the real
#     league) with no distinct Sleeper key confirmed for this narrower
#     category -- left out rather than silently overwritten or guessed.
#   - statId 198, 209: not identified against the community-documented
#     ESPN stat ID reference (github.com/cwendt94/espn-api); worth
#     revisiting if a golden-test comparison against a real graded week
#     shows a material gap for this league.
#   - statId 19, 26, 44 (2pt conversions), 206 (defensive 2pt return): a
#     pre-existing offense-scoring gap (19/26/44) and a niche category with
#     no confirmed Sleeper-vocabulary equivalent (206) -- out of scope for
#     this DEF/K feature.
#   - Every points-allowed/yards-allowed bracket statId (89-92, 121-136):
#     deliberately excluded, matching `is_defense_scoring_key`'s exclusion
#     of `pts_allow_*`/`yds_allow_*` -- see design doc §3.2.
ESPN_STAT_ID_TO_SLEEPER_KEY: dict[int, str] = {
    3: "pass_yd", 4: "pass_td", 20: "pass_int",
    24: "rush_yd", 25: "rush_td",
    42: "rec_yd", 43: "rec_td", 53: "rec",
    72: "fum_lost",
    # Kicking
    77: "fgm_40_49",
    # ESPN's "under 40" bucket doesn't distinguish 20-29 from 30-39 yards;
    # this crosswalk maps it to fgm_20_29 only, so a real 30-39 yard make
    # is under-counted. Known simplification -- revisit if a golden-test
    # comparison against a real graded week shows this matters.
    80: "fgm_20_29",
    201: "fgm_60p",
    85: "fgmiss", 86: "xpm",
    # Defense
    95: "int", 96: "fum_rec", 97: "blk_kick", 98: "safe", 99: "sack",
    101: "def_kr_td", 102: "def_pr_td", 103: "def_td", 104: "def_fum_td",
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
            # The D/ST lineup slot (16) can carry a different weight than
            # the base `points` field via `pointsOverrides` -- e.g. a real
            # defensive-interception entry is `{"points": 0.0,
            # "pointsOverrides": {"16": 2.0}}`. Prefer the override when
            # present; it's how ESPN encodes "this category scores
            # differently (or only) for a team defense."
            override = item.get("pointsOverrides", {}).get("16")
            out[key] = float(override if override is not None else item["points"])
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
