# scripts/backtest_scarcity.py
"""One-off backtest: does positional-scarcity-weighted VOR rank players
closer to their real final-season outcome than plain VOR does? Prints
results for human review -- SCARCITY_STRENGTH in engine/vor.py is only
ever promoted by hand, the same posture DURABILITY_WEIGHT was promoted
under in sub-project #4 (see backtest/harness.py, whose real-data-loading
this script's evaluate_season mirrors).

Baseline here is plain VOR (scarcity_strength=0.0), not ADP -- VOR already
beats ADP (see harness.py's own baseline_rho against ADP); the question
this script answers is narrower: does scarcity improve on top of VOR that
already exists.

Run: uv run python scripts/backtest_scarcity.py [season ...]
     (defaults to 2023 2024 2025 if no seasons given)
"""

from __future__ import annotations

import sys

import numpy as np

from ffdo.backtest.harness import spearman
from ffdo.domain.constants import STANDARD_HALF_PPR
from ffdo.domain.models import LeagueProfile
from ffdo.engine import vor as vor_mod
from ffdo.ingest import players as players_mod
from ffdo.ingest import projections as proj_mod
from ffdo.ingest import snapshot
from ffdo.ingest import stats as stats_mod

OFFENSE = {"QB", "RB", "WR", "TE"}
_ADP_KEY = "half_ppr"
_REFERENCE_ROSTER = (
    "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX",
    "BN", "BN", "BN", "BN", "BN", "BN", "BN",
)


def evaluate_season(season: int, scarcity_strength: float) -> dict:
    profiles = players_mod.parse(snapshot.load("players_nfl"))
    actual = stats_mod.parse(snapshot.load(f"stats_{season}"), season)
    _proj, adp = proj_mod.parse(
        snapshot.load(f"projections_{season}_CONTAMINATED"),
        season, allow_contaminated=True)

    ids, adp_values, truth = [], [], []
    for pid, market in adp.items():
        value = market.adp.get(_ADP_KEY, 999.0)
        prof = profiles.get(pid)
        if value >= 999 or prof is None or prof.position not in OFFENSE:
            continue
        line = actual.get(pid)
        if line is None:
            continue
        ids.append(pid)
        adp_values.append(value)
        truth.append(line.stats.get("pts_half_ppr", 0.0))

    # Same ADP-rank-to-pseudo-points mapping harness.py's evaluate_season
    # uses, so plain VOR here is computed on the same scale a real
    # draft-day valuation would see.
    order = np.argsort(adp_values)
    pseudo = np.empty(len(ids), dtype=float)
    pseudo[order] = np.linspace(300.0, 20.0, len(ids))
    points = dict(zip(ids, pseudo, strict=True))

    subset = {pid: profiles[pid] for pid in ids}
    reference = LeagueProfile(
        league_id="reference", season=season, num_teams=12,
        roster_positions=_REFERENCE_ROSTER, scoring_settings=STANDARD_HALF_PPR,
        budget=None)

    baseline_valued = vor_mod.compute(points, subset, reference, scarcity_strength=0.0)
    model_valued = vor_mod.compute(points, subset, reference, scarcity_strength=scarcity_strength)

    baseline = [baseline_valued[pid].vor if pid in baseline_valued else 0.0 for pid in ids]
    model = [model_valued[pid].vor if pid in model_valued else 0.0 for pid in ids]

    baseline_rho = spearman(baseline, truth)
    model_rho = spearman(model, truth)
    return {
        "season": season, "scarcity_strength": scarcity_strength, "n": len(ids),
        "baseline_rho": round(baseline_rho, 4), "model_rho": round(model_rho, 4),
        "improvement": round(model_rho - baseline_rho, 4),
    }


def sweep(season: int, strengths: list[float]) -> list[dict]:
    return [evaluate_season(season, s) for s in strengths]


if __name__ == "__main__":
    seasons = [int(s) for s in sys.argv[1:]] or [2023, 2024, 2025]
    strengths = [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0]
    for season in seasons:
        print(f"=== season {season} ===")
        for row in sweep(season, strengths):
            print(row)
