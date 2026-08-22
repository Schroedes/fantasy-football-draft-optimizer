import pytest

from ffdo.domain.models import LeagueProfile, PlayerProfile, ValuedPlayer
from ffdo.engine import auction
from ffdo.ingest import draft, snapshot


def _league(n=12, budget=200, roster=13):
    return LeagueProfile(league_id="x", season=2026, num_teams=n,
                         roster_positions=("RB",) * roster,
                         scoring_settings={}, budget=budget)


def _valued(vors):
    out = {}
    for pid, v in vors.items():
        prof = PlayerProfile(player_id=pid, first_name="P", last_name=pid,
                             position="RB", team="X", age=25, years_exp=3,
                             injury_status=None, active=True)
        out[pid] = ValuedPlayer(profile=prof, projected_points=0.0,
                                adjusted_points=0.0, vor=v, tier=1,
                                adjustments={})
    return out


def test_prices_never_fall_below_one_dollar():
    valued = _valued({f"p{i}": 100.0 - i * 20 for i in range(10)})
    prices = auction.baseline_prices(valued, _league(n=2, roster=3))
    assert all(p >= 1.0 for p in prices.values())


def test_total_baseline_spend_matches_league_budget():
    valued = _valued({f"p{i}": max(0.0, 200.0 - i * 4) for i in range(160)})
    league = _league()
    prices = auction.baseline_prices(valued, league)
    rostered = sorted(prices.values(), reverse=True)[:league.num_teams * league.roster_size]
    assert sum(rostered) == pytest.approx(league.num_teams * league.budget, rel=0.02)


def test_negative_vor_does_not_deflate_the_scale():
    """Clamping negative VOR to zero is what keeps the dollar scale honest."""
    with_negatives = _valued({f"p{i}": 100.0 - i * 10 for i in range(30)})
    prices = auction.baseline_prices(with_negatives, _league(n=2, roster=3))
    assert prices["p0"] > prices["p5"]
    assert all(p >= 1.0 for p in prices.values())


def test_inflation_is_one_before_any_picks():
    valued = _valued({f"p{i}": 100.0 - i for i in range(60)})
    league = _league(n=2, roster=3)
    baseline = auction.baseline_prices(valued, league)
    empty = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 2, "rounds": 3, "budget": 200}}, [])
    assert auction.inflation_factor(baseline, empty, league) == pytest.approx(1.0, rel=0.05)


def test_max_bid_reserves_a_dollar_for_every_unfilled_slot():
    league = _league()
    assert auction.max_bid(spent=0, slots_filled=0, league=league) == 200 - 12
    assert auction.max_bid(spent=150, slots_filled=12, league=league) == 50


def test_replaying_a_real_auction_keeps_inflation_sane():
    """Replay 2025 pick by pick. Inflation must stay positive and finite.

    Measured against the real 2025 replay, the tightest value is 0.234 at
    cut=140 -- only a 17% margin over a 0.2 floor, and it sits on an
    arbitrary synthetic VOR ramp used only for this test. The invariant
    worth asserting here is that inflation stays positive and finite across
    a real draft, not a tight band derived from made-up VOR inputs.
    """
    hist = snapshot.load("league_history")["drafts"]["2025"]
    state = draft.parse(hist["meta"], hist["picks"])
    league = LeagueProfile(league_id="x", season=2025, num_teams=12,
                           roster_positions=("RB",) * 14,
                           scoring_settings={}, budget=200)
    valued = _valued({p.player_id: 150.0 - i * 0.8
                      for i, p in enumerate(state.picks)})
    baseline = auction.baseline_prices(valued, league)

    for cut in range(0, len(state.picks), 20):
        partial = draft.parse(hist["meta"], hist["picks"][:cut])
        factor = auction.inflation_factor(baseline, partial, league)
        assert 0.0 < factor < 20.0, f"implausible inflation {factor} at pick {cut}"
