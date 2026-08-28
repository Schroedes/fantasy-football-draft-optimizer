import numpy as np
import pytest

from ffdo.domain.models import PlayerProfile, ValuedPlayer
from ffdo.engine import market


def _valued(spec):
    out = {}
    for pid, (pos, v) in spec.items():
        prof = PlayerProfile(player_id=pid, first_name="P", last_name=pid,
                             position=pos, team="X", age=25, years_exp=3,
                             injury_status=None, active=True)
        out[pid] = ValuedPlayer(profile=prof, projected_points=v,
                                adjusted_points=v, vor=v, tier=1,
                                adjustments={})
    return out


def test_survival_probabilities_are_bounded():
    adp = {f"p{i}": float(i + 1) for i in range(40)}
    surv = market.simulate_survival(adp, set(adp), picks_until=10,
                                    sims=500, rng=np.random.default_rng(0))
    assert all(0.0 <= v <= 1.0 for v in surv.values())


def test_early_adp_players_are_less_likely_to_survive():
    adp = {f"p{i}": float(i + 1) for i in range(40)}
    surv = market.simulate_survival(adp, set(adp), picks_until=12,
                                    sims=2000, rng=np.random.default_rng(0))
    assert surv["p0"] < surv["p30"]


def test_exactly_one_player_is_taken_per_pick():
    """The whole reason for simulating rather than using independent Gaussians."""
    adp = {f"p{i}": float(i + 1) for i in range(40)}
    surv = market.simulate_survival(adp, set(adp), picks_until=10,
                                    sims=1000, rng=np.random.default_rng(1))
    expected_gone = sum(1 - v for v in surv.values())
    assert expected_gone == pytest.approx(10.0, abs=0.001)


def test_longer_waits_reduce_survival():
    adp = {f"p{i}": float(i + 1) for i in range(40)}
    rng = np.random.default_rng(3)
    short = market.simulate_survival(adp, set(adp), picks_until=5, sims=2000, rng=rng)
    long = market.simulate_survival(adp, set(adp), picks_until=20, sims=2000,
                                    rng=np.random.default_rng(3))
    assert long["p10"] < short["p10"]


def test_cost_of_waiting_is_higher_for_a_thin_position():
    """One elite WR and a cliff behind him; RB is deep and flat."""
    valued = _valued({
        "wr_elite": ("WR", 90.0),
        **{f"wr{i}": ("WR", 20.0) for i in range(10)},
        **{f"rb{i}": ("RB", 60.0 - i) for i in range(10)},
    })
    survival = {"wr_elite": 0.05, **{f"wr{i}": 0.9 for i in range(10)},
                **{f"rb{i}": 0.9 for i in range(10)}}
    cow = market.cost_of_waiting(valued, survival, set(valued))
    assert cow["WR"]["cost"] > cow["RB"]["cost"]
    assert cow["WR"]["best_now"] == 90.0


def test_cost_of_waiting_ignores_drafted_players():
    valued = _valued({"a": ("RB", 100.0), "b": ("RB", 50.0)})
    cow = market.cost_of_waiting(valued, {"b": 0.5}, available={"b"})
    assert cow["RB"]["best_now"] == 50.0


def test_gone_this_stretch_removes_exactly_take_players():
    adp = {f"p{i}": float(i + 1) for i in range(20)}
    rng = np.random.default_rng(5)
    gone = market.gone_this_stretch(list(adp), adp, take=4, tau=8.0, rng=rng)
    assert len(gone) == 4
    assert gone <= set(adp)


def test_gone_this_stretch_never_removes_players_without_adp():
    adp = {"p0": 1.0, "p1": 2.0}
    rng = np.random.default_rng(5)
    gone = market.gone_this_stretch(["p0", "p1", "no_adp"], adp, take=5, tau=8.0, rng=rng)
    assert "no_adp" not in gone
    assert gone == {"p0", "p1"}  # take clamps to eligible count, not requested count
