"""When will he be gone -- and what does waiting cost?

Survival is simulated rather than solved in closed form. Independent Gaussians
would let two players occupy the same pick, could not condition on who has
already gone, and would be blind to positional runs, which is exactly the
phenomenon this tool exists to surface.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np

from ffdo.domain.models import ValuedPlayer


def simulate_survival(
    adp: Mapping[str, float],
    available: Iterable[str],
    picks_until: int,
    *,
    sims: int = 2000,
    tau: float = 8.0,
    rng: np.random.Generator | None = None,
) -> dict[str, float]:
    """P(each available player is still there in `picks_until` picks).

    Each simulated pick draws from the remaining pool via Gumbel-max sampling
    (equivalent to Plackett-Luce), so exactly one player leaves per pick.
    """
    rng = rng or np.random.default_rng()
    ids = [pid for pid in available if pid in adp]
    if not ids or picks_until <= 0:
        return dict.fromkeys(ids, 1.0)

    values = np.array([adp[pid] for pid in ids], dtype=float)
    # Lower ADP => more desirable => higher log-weight.
    logits = -values / tau
    n = len(ids)
    take = min(picks_until, n)

    survived = np.zeros(n, dtype=np.int64)
    for _ in range(sims):
        gumbel = rng.gumbel(size=n)
        # Top-k by perturbed logit is an exact sample without replacement.
        gone = np.argpartition(-(logits + gumbel), take - 1)[:take]
        mask = np.ones(n, dtype=bool)
        mask[gone] = False
        survived += mask

    return {pid: float(survived[i]) / sims for i, pid in enumerate(ids)}


def cost_of_waiting(
    valued: Mapping[str, ValuedPlayer],
    survival: Mapping[str, float],
    available: Iterable[str],
) -> dict[str, dict[str, float]]:
    """Per position: best VOR now, expected best VOR at the next pick, and the gap.

    Expected best is computed over the ordered pool: a player is the best
    survivor exactly when he survives and everyone above him does not.
    """
    pool = set(available)
    by_position: dict[str, list[ValuedPlayer]] = {}
    for pid, vp in valued.items():
        if pid in pool:
            by_position.setdefault(vp.profile.position, []).append(vp)

    out: dict[str, dict[str, float]] = {}
    for position, players in by_position.items():
        players.sort(key=lambda v: v.vor, reverse=True)
        best_now = players[0].vor

        expected = 0.0
        none_better = 1.0
        for vp in players:
            p = survival.get(vp.profile.player_id, 0.0)
            expected += vp.vor * p * none_better
            none_better *= 1.0 - p

        out[position] = {
            "best_now": round(best_now, 2),
            "expected_next": round(expected, 2),
            "cost": round(best_now - expected, 2),
        }
    return out
