"""When will he be gone -- and what does waiting cost?

Survival is simulated rather than solved in closed form. Independent Gaussians
would let two players occupy the same pick, could not condition on who has
already gone, and would be blind to positional runs, which is exactly the
phenomenon this tool exists to surface.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

import numpy as np

from ffdo.domain.models import ValuedPlayer


def gone_this_stretch(
    ids: Sequence[str], adp: Mapping[str, float], take: int,
    tau: float, rng: np.random.Generator,
) -> frozenset[str]:
    """One Gumbel-max draw: up to `take` ids removed from `ids`, weighted
    by ADP (lower ADP -> more desirable -> more likely to be taken). Ids
    absent from `adp` are never drawn as "gone" -- the same limitation
    `simulate_survival` already has, not new here.
    """
    eligible = [pid for pid in ids if pid in adp]
    if take <= 0 or not eligible:
        return frozenset()
    take = min(take, len(eligible))
    logits = np.array([-adp[pid] / tau for pid in eligible])
    gumbel = rng.gumbel(size=len(eligible))
    gone_idx = np.argpartition(-(logits + gumbel), take - 1)[:take]
    return frozenset(eligible[i] for i in gone_idx)


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

    n = len(ids)
    id_index = {pid: i for i, pid in enumerate(ids)}
    survived = np.zeros(n, dtype=np.int64)
    for _ in range(sims):
        gone = gone_this_stretch(ids, adp, picks_until, tau, rng)
        mask = np.ones(n, dtype=bool)
        for pid in gone:
            mask[id_index[pid]] = False
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
