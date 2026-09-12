#!/usr/bin/env python
"""Fits PICK_VALUE_CURVE from the user's own tracked dynasty leagues' real
rookie-draft history -- prints the result as a Python literal for a human
to review and paste into domain/constants.py by hand. Deliberately does
NOT write the file itself, same reasoning as fit_age_curve.py.

Usage: uv run python scripts/fit_pick_value.py <snapshot-dir> <league-id> [<league-id> ...]

<league-id> is a Sleeper league_id for the CURRENT season of a tracked
dynasty/keeper league (e.g. GDK's or Room Temp IQ's current league_id) --
this script walks each one's own previous_league_id chain internally, so
pass only the current-season id per league, not every historical id.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ffdo.domain.constants import STANDARD_HALF_PPR
from ffdo.domain.models import LeagueProfile
from ffdo.engine import vor
from ffdo.engine.scoring import score_stats
from ffdo.ingest import players as players_mod
from ffdo.ingest import snapshot
from ffdo.ingest import stats as stats_mod
from ffdo.ingest.client import SleeperClient
from ffdo.ingest.sleeper import rookie_drafts

MIN_SAMPLE = 3  # controller judgment call -- see the plan's Global Constraints

_REFERENCE_ROSTER = (
    "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX",
    "BN", "BN", "BN", "BN", "BN", "BN", "BN",
)


def _tertile(pick_in_round: int, round_size: int) -> str:
    third = max(1, round(round_size / 3))
    if pick_in_round <= third:
        return "early"
    if pick_in_round <= 2 * third:
        return "mid"
    return "late"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit PICK_VALUE_CURVE from real rookie-draft history")
    parser.add_argument("snapshot_dir", type=Path)
    parser.add_argument("league_ids", nargs="+")
    args = parser.parse_args()

    all_profiles = players_mod.parse(snapshot.load("players_nfl", args.snapshot_dir))

    sleeper = SleeperClient()
    try:
        all_picks: list[rookie_drafts.RookiePick] = []
        round_sizes: dict[int, int] = {}   # draft season -> num teams, from Task 1's raw fetch
        for league_id in args.league_ids:
            chain = rookie_drafts.league_seasons(sleeper, league_id)
            for link in chain:
                picks = rookie_drafts.rookie_picks(sleeper, link)
                all_picks.extend(picks)
                for p in picks:
                    round_sizes[p.season] = max(round_sizes.get(p.season, 0), p.pick_in_round)
    finally:
        sleeper.close()

    if not all_picks:
        print("No completed rookie drafts found for the given leagues.", file=sys.stderr)
        sys.exit(1)

    seasons_needed = sorted({p.season for p in all_picks})
    points_by_season: dict[int, dict[str, float]] = {}
    for season in seasons_needed:
        stat_lines = stats_mod.parse(snapshot.load(f"stats_{season}", args.snapshot_dir), season)
        points_by_season[season] = {
            pid: score_stats(line.stats, STANDARD_HALF_PPR)
            for pid, line in stat_lines.items()
        }

    # (round, pick_in_round) -> list of realized VOR values
    exact: dict[tuple[int, int], list[float]] = {}
    for pick in all_picks:
        season_points = points_by_season.get(pick.season, {})
        if pick.player_id not in season_points:
            continue  # no production data for this player that season -- skip, not zero
        reference = LeagueProfile(
            league_id="reference", season=pick.season, num_teams=12,
            roster_positions=_REFERENCE_ROSTER, scoring_settings=STANDARD_HALF_PPR,
            budget=None)
        valued = vor.compute(season_points, all_profiles, reference)
        vp = valued.get(pick.player_id)
        if vp is None:
            continue  # e.g. a position this reference league doesn't start
        exact.setdefault((pick.round, pick.pick_in_round), []).append(vp.vor)

    curve: dict[int, dict] = {}
    for rnd in sorted({r for r, _ in exact}):
        round_size = max(round_sizes.values()) if round_sizes else 12
        round_entries = {pn: vals for (r, pn), vals in exact.items() if r == rnd}
        round_all = [v for vals in round_entries.values() for v in vals]
        tiers: dict = {"exact": {}}
        for pn, vals in round_entries.items():
            if len(vals) >= MIN_SAMPLE:
                tiers["exact"][pn] = round(sum(vals) / len(vals), 2)
        for label in ("early", "mid", "late"):
            tertile_vals = [
                v for (r, pn), vals in exact.items() if r == rnd
                and _tertile(pn, round_size) == label for v in vals
            ]
            if len(tertile_vals) >= MIN_SAMPLE:
                tiers[label] = round(sum(tertile_vals) / len(tertile_vals), 2)
        if round_all:
            tiers["round_avg"] = round(sum(round_all) / len(round_all), 2)
        curve[rnd] = tiers

    print(f"# Fit from {args.snapshot_dir.name}, leagues {args.league_ids}, "
          f"seasons {seasons_needed}, MIN_SAMPLE={MIN_SAMPLE}")
    print("PICK_VALUE_CURVE: Final[dict[int, dict]] = {")
    for rnd in sorted(curve):
        print(f"    {rnd}: {curve[rnd]!r},")
    print("}")


if __name__ == "__main__":
    main()
