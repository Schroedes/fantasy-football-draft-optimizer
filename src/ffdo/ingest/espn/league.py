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
#   - statId 63 (fumbleRecoveredForTD): `points: 6.0`, no `"16"` entry in
#     `pointsOverrides` in the real fixture. NOTE this does NOT mean "no
#     override means it doesn't apply to D/ST" -- ESPN's actual model is
#     the reverse: `points` is the base value applied to every slot by
#     default, and `pointsOverrides` narrows it for slots that score
#     differently (see `_scoring_settings` below, which already reads it
#     that way). The real reason this is left unmapped: Sleeper's
#     `fum_rec_td` key never appears on a real DEF stat line at all
#     (verified against every row of `stats_2025.json.gz` with
#     position == "DEF" -- zero hits; the two rows that DO carry it are a
#     real RB and a real WR), and it's already excluded from offense
#     scoring too (see `domain.constants._DEFENSIVE_ONLY`). Mapping it
#     here would credit no one under real data and risks colliding with
#     a future offense-side use of the key. The *decision* (leave
#     unmapped) is unchanged from the original comment; only the
#     reasoning was wrong.
#   - statId 93 (defensiveBlockedKickForTouchdowns): would collide with
#     statId 103's `def_td` mapping below (both score 6.0 in the real
#     league) with no distinct Sleeper key confirmed for this narrower
#     category -- left out rather than silently overwritten or guessed.
#   - statId 209: `points: 1.0`, `pointsOverrides: {"16": 1.0}` in the real
#     fixture -- a real, D/ST-scored category, but not identified against
#     the community-documented ESPN stat ID reference
#     (github.com/cwendt94/espn-api) or against any real DEF stat line's
#     vocabulary gap found during this fix wave. Low weight (1.0), so left
#     unmapped rather than guessed; worth revisiting if a future golden
#     test surfaces a matching real gap.
#   - statId 19, 26, 44 (2pt conversions), 206 (defensive 2pt return): a
#     pre-existing offense-scoring gap (19/26/44) and a niche category with
#     no confirmed Sleeper-vocabulary equivalent (206) -- out of scope for
#     this DEF/K feature.
#   - Every points-allowed/yards-allowed bracket statId (89-92, 121-136):
#     deliberately excluded, matching `is_defense_scoring_key`'s exclusion
#     of `pts_allow_*`/`yds_allow_*` -- see design doc §3.2.
#
# `_scoring_settings` below applies the `pointsOverrides["16"]` preference
# uniformly to every mapped statId, not just the defense/kicking ones --
# currently harmless (no offense or kicking statId in the real fixture
# carries a slot-16 override), but a future league where one did would
# silently have its offense/kicking weight replaced by the D/ST-slot
# value. Flagged here rather than fixed, since there is no real fixture
# evidence yet that it happens.
ESPN_STAT_ID_TO_SLEEPER_KEY: dict[int, str] = {
    3: "pass_yd", 4: "pass_td", 20: "pass_int",
    24: "rush_yd", 25: "rush_td",
    42: "rec_yd", 43: "rec_td", 53: "rec",
    72: "fum_lost",
    # Kicking. Real 2026 K projections (the primary consumer of this
    # crosswalk -- see ffdo.api.app, which scores `SeasonProjection.stats`,
    # not actual-stats lines) only ever populate two distance buckets:
    # `fgm_40_49` and the aggregate `fgm_50p` (all 50+ yard makes, no
    # separate 50-59/60+ split at projection time -- confirmed against
    # `data/snapshots/2026-08-22-draft-day/projections_2026.json.gz`).
    # Real *actual* stat lines (`stats_2025.json.gz`) do carry the finer
    # `fgm_0_19`/`fgm_20_29`/`fgm_30_39`/`fgm_50_59`/`fgm_60p` breakdown,
    # and `fgm_50p` there is exactly `fgm_50_59 + fgm_60p` (verified
    # against Brandon Aubrey's real 2025 line: 8 + 3 = 11).
    77: "fgm_40_49",
    # ESPN's "under 40" bucket doesn't distinguish 20-29 from 30-39 yards;
    # this crosswalk maps it to fgm_20_29 only, so a real 30-39 yard make
    # is under-counted -- known simplification, unchanged from the
    # original crosswalk work. IMPORTANT: this mapping is currently DEAD
    # against real 2026 K projections -- `fgm_20_29` never appears in that
    # snapshot's `stats` dicts at all (projections don't break out
    # sub-40-yard makes by distance), so this league's real <40-yard-make
    # weight (3.0) has no effect on projection-based valuation today. It
    # still applies to `fgm_20_29` wherever it DOES appear (real actual
    # stat lines), so the mapping is kept rather than dropped. Revisit if
    # Sleeper ever starts projecting a sub-40 breakdown.
    80: "fgm_20_29",
    # statId 198 (`points: 5.0`, no override) fills the previously-missing
    # 50+ yard bucket. Verified 2026-08-25 against the real fixture: the
    # four FG-distance statIds here (80, 77, 198, 201) form a
    # monotonically increasing points ladder (3.0, 4.0, 5.0, 6.0), the
    # expected shape for a <40 / 40-49 / 50-59 / 60+ split -- strongly
    # suggesting 198 is the 50-59 bucket and 201 (below) is 60+. Since
    # real K *projections* only carry the aggregate `fgm_50p` (see comment
    # above -- no separate 50-59/60+ split at projection time), and most
    # of a real kicker's 50+ makes fall in 50-59 rather than 60+ (Aubrey's
    # real 2025 split: 8 of 11, ~73%), mapping statId 198 -> `fgm_50p`
    # gives the more representative of the two candidate weights for the
    # aggregate bucket. This was verified to matter: Brandon Aubrey's real
    # 2025 season, scored under this real league's settings, moves from
    # 129.6 (pre-fix, 50-59-yard makes entirely unscored) to 166.6
    # (post-fix), much closer to Sleeper's own `pts_half_ppr` of 180.6 for
    # the same season (remaining gap is this league's other weights
    # diverging from Sleeper's own half-PPR preset, not a vocabulary gap).
    198: "fgm_50p",
    # statId 201 (`points: 6.0`, no override, the 60+ bucket per the
    # ladder above) is deliberately left UNMAPPED, not remapped to
    # `fgm_60p`. `fgm_60p` never appears in real K projections (see
    # comment above), so mapping it there would be exactly as dead as the
    # mapping it's replacing. Mapping it to `fgm_50p` instead (the same
    # key as statId 198, immediately above) would silently overwrite
    # whichever of the two statIds is processed second in
    # `_scoring_settings`'s dict-assignment loop -- order-dependent and
    # wrong either way, since ESPN's two real buckets (50-59 at 5.0, 60+
    # at 6.0) cannot both be represented by one aggregate Sleeper key with
    # one weight. Net effect: the rare 60+ yard make is scored at the 5.0
    # rate (via statId 198's `fgm_50p` mapping) instead of its "true" 6.0
    # -- a small, real, documented under-count, not a silent gap.
    85: "fgmiss", 86: "xpm",
    # Defense
    95: "int", 96: "fum_rec", 97: "blk_kick", 98: "safe", 99: "sack",
    101: "def_kr_td",
    # statId 102 (real weight 6.0, via pointsOverrides["16"]) is the
    # punt-return-touchdown category. The Sleeper-vocabulary key it
    # produces is bare `pr_td`, NOT `def_pr_td` -- verified against real
    # data: `def_pr_td` never appears anywhere in either the 2026
    # projections or the 2025 actual-stats snapshot, while `pr_td` does
    # (on both real offensive WR rows and real DEF rows). This was
    # previously mapped to the wrong (nonexistent) key name; fixed here.
    # `pr_td`, like `def_kr_td` above, is NOT recognized by
    # `is_defense_scoring_key` -- see the leakage comment on
    # `domain.constants._DEFENSE_BARE` -- so this weight still has no
    # effect on scoring today. The crosswalk now at least emits the
    # correct, real key rather than a dead one, which matters if a future
    # fix adds position-aware handling for return TDs.
    102: "pr_td",
    103: "def_td", 104: "def_fum_td",
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
