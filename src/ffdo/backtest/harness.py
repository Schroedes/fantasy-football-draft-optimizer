"""Out-of-sample validation against the ADP baseline.

Historical projections are contaminated (see ingest/projections.py), so ADP is
the only clean preseason signal available -- and it is what the room actually
drafts on, which makes beating it the operational definition of edge.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ffdo.domain.constants import SEASON_LENGTH, STANDARD_HALF_PPR
from ffdo.engine import adjustments as adj
from ffdo.ingest import players as players_mod
from ffdo.ingest import projections as proj_mod
from ffdo.ingest import snapshot
from ffdo.ingest import stats as stats_mod

OFFENSE = {"QB", "RB", "WR", "TE"}
_ADP_KEY = "half_ppr"


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    ra = np.argsort(np.argsort(np.asarray(a, dtype=float)))
    rb = np.argsort(np.argsort(np.asarray(b, dtype=float)))
    return float(np.corrcoef(ra, rb)[0, 1])


def evaluate_season(
    season: int,
    *,
    age_weight: float,
    durability_weight: float,
) -> dict:
    """Score ADP alone against ADP plus adjustments, using only prior data."""
    profiles = players_mod.parse(snapshot.load("players_nfl"))
    actual = stats_mod.parse(snapshot.load(f"stats_{season}"), season)

    # ADP survives contamination, so reading this file is deliberate and safe.
    _proj, adp = proj_mod.parse(
        snapshot.load(f"projections_{season}_CONTAMINATED"),
        season, allow_contaminated=True)

    history: dict[str, list] = {}
    for past in range(2021, season):
        for pid, line in stats_mod.parse(
                snapshot.load(f"stats_{past}"), past).items():
            history.setdefault(pid, []).append(line)

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

    # Lower ADP means better, so negate to align direction with points.
    baseline = [-v for v in adp_values]
    baseline_rho = spearman(baseline, truth)

    if not age_weight and not durability_weight:
        return {"n": len(ids), "baseline_rho": round(baseline_rho, 4),
                "model_rho": round(baseline_rho, 4), "improvement": 0.0}

    length = SEASON_LENGTH[season]
    # Map ADP rank onto a points-like scale so adjustments are commensurate.
    order = np.argsort(adp_values)
    pseudo = np.empty(len(ids), dtype=float)
    pseudo[order] = np.linspace(300.0, 20.0, len(ids))
    points = dict(zip(ids, pseudo, strict=True))

    subset = {pid: profiles[pid] for pid in ids}
    curve = adj.fit_age_curve(
        {pid: history.get(pid, []) for pid in ids}, subset,
        STANDARD_HALF_PPR) if age_weight else None

    built = adj.build(
        subset, {pid: history.get(pid, []) for pid in ids}, points,
        replacement_ppg={"QB": 14.0, "RB": 8.0, "WR": 8.0, "TE": 6.0},
        age_weight=age_weight, durability_weight=durability_weight,
        age_curve=curve, current_season=season,
    )
    model = [points[pid] + sum(built.get(pid, {}).values()) for pid in ids]
    model_rho = spearman(model, truth)

    return {
        "n": len(ids),
        "baseline_rho": round(baseline_rho, 4),
        "model_rho": round(model_rho, 4),
        "improvement": round(model_rho - baseline_rho, 4),
        "season_length": length,
    }
