"""Real per-week historical stats, from /v1/stats/nfl/regular/<season>/<week>
-- confirmed live to work for any past week, not just the current one
(unlike ingest.sleeper.weekly_projections, which is live-current-week
only). Used by scripts/fit_faab_curve.py to reconstruct what a historical
waiver claim was actually worth at the time, and by api.app's
GET /scorecard endpoint to score realized lineup-ledger outcomes against
what was actually recommended."""

from __future__ import annotations

from ffdo.ingest.client import V1, SleeperClient


def fetch(sleeper: SleeperClient, season: int, week: int) -> dict[str, dict[str, float]]:
    raw = sleeper.get_json(f"{V1}/stats/nfl/regular/{season}/{week}")
    out: dict[str, dict[str, float]] = {}
    for player_id, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        out[player_id] = {
            k: float(v) for k, v in rec.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
    return out
