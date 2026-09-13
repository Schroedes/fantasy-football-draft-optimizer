#!/usr/bin/env python
"""Fits FAAB_BID_CURVE from the user's own tracked leagues' real waiver-
claim history -- prints the result as a Python literal for review, never
writes the file itself (same posture as fit_age_curve.py/fit_pick_value.py).

Usage: uv run python scripts/fit_faab_curve.py <league-id> <season> [<league-id> <season> ...]

Each <league-id>/<season> pair identifies one real historical season to
fit from (a league_id is season-specific on Sleeper, unlike a league's
persistent identity -- pass the exact league_id for each season you want
included, not just the current-season id).
"""

from __future__ import annotations

import argparse
import math
import sys

from ffdo.domain.constants import STANDARD_HALF_PPR, SEASON_LENGTH
from ffdo.domain.models import LeagueProfile
from ffdo.engine import vor
from ffdo.engine.scoring import score_stats
from ffdo.ingest import players as players_mod
from ffdo.ingest.client import V1, SleeperClient
from ffdo.ingest.sleeper import historical_weekly_stats, waivers

MIN_SAMPLE = 3
BUCKET_WIDTH = 10

_REFERENCE_ROSTER = (
    "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX",
    "BN", "BN", "BN", "BN", "BN", "BN", "BN",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit FAAB_BID_CURVE from real historical waiver claims")
    parser.add_argument("league_season_pairs", nargs="+",
                        help="alternating league_id season league_id season ...")
    args = parser.parse_args()
    pairs = args.league_season_pairs
    if len(pairs) % 2 != 0:
        print("Pass league_id/season pairs, e.g. L1 2025 L2 2025", file=sys.stderr)
        sys.exit(1)

    sleeper = SleeperClient()
    try:
        profiles = players_mod.parse(sleeper.get_json(f"{V1}/players/nfl"))

        observations: list[tuple[float, float]] = []  # (vor_gain, bid_pct)
        for i in range(0, len(pairs), 2):
            league_id, season = pairs[i], int(pairs[i + 1])
            league_raw = sleeper.get_json(f"{V1}/league/{league_id}")
            waiver_budget = float(league_raw.get("settings", {}).get("waiver_budget") or 0)
            if waiver_budget <= 0:
                print(f"skipping {league_id}/{season}: no FAAB budget configured",
                      file=sys.stderr)
                continue
            season_weeks = len(SEASON_LENGTH) and SEASON_LENGTH.get(season, 18)
            claims = waivers.fetch_waivers(sleeper, league_id, season=season,
                                            through_week=season_weeks)
            # Cache each week's stats ONCE per league-season and reuse
            # across every claim that needs it, rather than re-fetching
            # the same week many times over -- a league-season with dozens
            # of claims spread across the year would otherwise make many
            # redundant live HTTP calls for the same (season, week) pair.
            week_stats_cache: dict[int, dict] = {}

            def _week_stats(wk: int) -> dict:
                if wk not in week_stats_cache:
                    week_stats_cache[wk] = historical_weekly_stats.fetch(sleeper, season, wk)
                return week_stats_cache[wk]
            # This re-walks chronologically rather than calling
            # waivers.remaining_budget(): that function only returns each
            # roster's FINAL remaining total after every claim, but fitting
            # needs the INTERMEDIATE remaining-before-THIS-claim value at
            # every step, one fitting observation per claim -- a different
            # need from the live endpoint's "what's left right now."
            budgets_after_each: dict[int, float] = {}
            for claim in sorted(claims, key=lambda c: c.created_ms):
                remaining_before = waiver_budget - budgets_after_each.get(claim.roster_id, 0.0)
                if remaining_before <= 0:
                    budgets_after_each[claim.roster_id] = (
                        budgets_after_each.get(claim.roster_id, 0.0) + claim.bid_amount)
                    continue
                bid_pct = claim.bid_amount / remaining_before

                rest_of_season_points: dict[str, float] = {}
                for wk in range(claim.week + 1, season_weeks + 1):
                    week_stats = _week_stats(wk)
                    for pid, stat_line in week_stats.items():
                        rest_of_season_points[pid] = (
                            rest_of_season_points.get(pid, 0.0)
                            + score_stats(stat_line, STANDARD_HALF_PPR))

                reference = LeagueProfile(
                    league_id="reference", season=season, num_teams=12,
                    roster_positions=_REFERENCE_ROSTER,
                    scoring_settings=STANDARD_HALF_PPR, budget=None)
                valued = vor.compute(rest_of_season_points, profiles, reference)
                vp = valued.get(claim.player_id)
                if vp is None:
                    budgets_after_each[claim.roster_id] = (
                        budgets_after_each.get(claim.roster_id, 0.0) + claim.bid_amount)
                    continue

                observations.append((vp.vor, bid_pct))
                budgets_after_each[claim.roster_id] = (
                    budgets_after_each.get(claim.roster_id, 0.0) + claim.bid_amount)
    finally:
        sleeper.close()

    if not observations:
        print("No usable waiver observations found.", file=sys.stderr)
        sys.exit(1)

    buckets: dict[int, list[float]] = {}
    for vor_gain, bid_pct in observations:
        bucket = math.floor(vor_gain / BUCKET_WIDTH) * BUCKET_WIDTH
        buckets.setdefault(bucket, []).append(bid_pct)

    curve = {
        bucket: round(sum(pcts) / len(pcts), 4)
        for bucket, pcts in buckets.items()
        if len(pcts) >= MIN_SAMPLE
    }

    print(f"# Fit from {len(observations)} real waiver observations across "
          f"{len(pairs) // 2} league-seasons, MIN_SAMPLE={MIN_SAMPLE}, "
          f"BUCKET_WIDTH={BUCKET_WIDTH}")
    print("FAAB_BID_CURVE: Final[dict[int, float]] = {")
    for bucket in sorted(curve):
        print(f"    {bucket}: {curve[bucket]},")
    print("}")


if __name__ == "__main__":
    main()
