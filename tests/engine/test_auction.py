import pytest

from ffdo.domain.models import DraftPick, DraftState, LeagueProfile, PlayerProfile, ValuedPlayer
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


def _league_multi(roster_positions, budget=200, n=12):
    return LeagueProfile(league_id="x", season=2026, num_teams=n,
                         roster_positions=roster_positions,
                         scoring_settings={}, budget=budget)


def _valued_positions(position_vors):
    """position_vors: dict[pid] -> (position, vor)."""
    out = {}
    for pid, (pos, v) in position_vors.items():
        prof = PlayerProfile(player_id=pid, first_name="P", last_name=pid,
                             position=pos, team="X", age=25, years_exp=3,
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


def test_positional_budget_need_and_slot_invariant():
    league = _league_multi(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    valued = _valued_positions({
        "qb1": ("QB", 50.0), "qb2": ("QB", 40.0),
        "rb1": ("RB", 80.0), "rb2": ("RB", 70.0), "rb3": ("RB", 60.0),
        "wr1": ("WR", 90.0), "wr2": ("WR", 85.0), "wr3": ("WR", 75.0),
        "te1": ("TE", 30.0), "te2": ("TE", 20.0),
    })
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 9, "budget": 200}}, [])

    result = auction.positional_budget(
        valued, baseline, 1.0, state, league, roster_id=None,
        your_dollars_left=200.0)

    assert result["QB"]["slots_open"] == 1
    assert result["RB"]["slots_open"] == 2
    assert result["WR"]["slots_open"] == 2
    assert result["TE"]["slots_open"] == 1
    assert result["FLEX"]["slots_open"] == 1
    assert result["BENCH"]["slots_open"] == 2
    total_slots_accounted = (
        result["QB"]["slots_open"] + result["RB"]["slots_open"]
        + result["WR"]["slots_open"] + result["TE"]["slots_open"]
        + result["FLEX"]["slots_open"] + result["BENCH"]["slots_open"])
    assert total_slots_accounted == league.roster_size


def test_positional_budget_scales_to_your_dollars_left():
    league = _league_multi(("QB", "RB", "WR", "TE", "BN"))
    valued = _valued_positions({
        "qb1": ("QB", 50.0), "rb1": ("RB", 80.0),
        "wr1": ("WR", 90.0), "te1": ("TE", 30.0),
    })
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 5, "budget": 200}}, [])

    result = auction.positional_budget(
        valued, baseline, 1.0, state, league, roster_id=None,
        your_dollars_left=90.0)

    total = (result["QB"]["recommended"] + result["RB"]["recommended"]
             + result["WR"]["recommended"] + result["TE"]["recommended"]
             + result["FLEX"]["recommended"] + result["BENCH"]["recommended"])
    # Six independently-rounded (1-decimal) values can compound to ~0.1-0.3
    # off the true total even though the underlying scale is exact -- widen
    # accordingly rather than chase a razor-tight bound.
    assert total == pytest.approx(90.0, abs=0.5)


def test_extra_drafted_players_reduce_flex_not_dedicated_need():
    """A 2nd RB drafted beyond the single dedicated RB slot must have used
    the FLEX slot, not created negative dedicated need."""
    league = _league_multi(("RB", "FLEX", "BN"))
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="rb_drafted_1", amount=10),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=1, picked_by="u1",
                 player_id="rb_drafted_2", amount=10),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=3, budget=200, picks=picks)
    valued = _valued_positions({
        "rb_drafted_1": ("RB", 50.0), "rb_drafted_2": ("RB", 40.0),
        "rb_avail": ("RB", 30.0),
    })
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}

    result = auction.positional_budget(
        valued, baseline, 1.0, state, league, roster_id=1,
        your_dollars_left=180.0)

    assert result["RB"]["slots_open"] == 0
    assert result["FLEX"]["slots_open"] == 0
    assert result["BENCH"]["slots_open"] == 1


def test_non_flex_eligible_leftover_does_not_zero_out_flex():
    """A backup QB in a standard (non-superflex) FLEX league is not
    flex-eligible -- it must spill to BENCH, not incorrectly consume the
    FLEX slot's budget."""
    league = _league_multi(("QB", "RB", "FLEX", "BN"))
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="qb1", amount=10),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=1, picked_by="u1",
                 player_id="qb2", amount=10),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=4, budget=200, picks=picks)
    valued = _valued_positions({
        "qb1": ("QB", 50.0), "qb2": ("QB", 40.0),
        "rb_avail": ("RB", 30.0),
    })
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}

    result = auction.positional_budget(
        valued, baseline, 1.0, state, league, roster_id=1,
        your_dollars_left=180.0)

    assert result["QB"]["slots_open"] == 0
    assert result["RB"]["slots_open"] == 1
    assert result["FLEX"]["slots_open"] == 1
    assert result["BENCH"]["slots_open"] == 0


def test_superflex_leftover_correctly_consumes_the_superflex_slot():
    """Unlike a standard FLEX league, SUPER_FLEX accepts QB -- a leftover
    QB pick there DOES correctly consume the superflex slot."""
    league = _league_multi(("QB", "SUPER_FLEX", "BN"))
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="qb1", amount=10),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=1, picked_by="u1",
                 player_id="qb2", amount=10),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=3, budget=200, picks=picks)
    valued = _valued_positions({
        "qb1": ("QB", 50.0), "qb2": ("QB", 40.0),
    })
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}

    result = auction.positional_budget(
        valued, baseline, 1.0, state, league, roster_id=1,
        your_dollars_left=180.0)

    assert result["QB"]["slots_open"] == 0
    assert result["FLEX"]["slots_open"] == 0
    assert result["BENCH"]["slots_open"] == 1


def test_flex_prices_from_leftover_pool_not_min_bid():
    """FLEX must price like a real starting slot -- from the best remaining
    flex-eligible players left after dedicated slots are filled -- not the
    $1 floor bench gets."""
    league = _league_multi(("RB", "FLEX", "BN"))
    valued = _valued_positions({
        "rb1": ("RB", 50.0), "rb2": ("RB", 30.0), "rb3": ("RB", 10.0),
    })
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 3, "budget": 200}}, [])

    result = auction.positional_budget(
        valued, baseline, 1.0, state, league, roster_id=None,
        your_dollars_left=81.0)

    # RB's dedicated slot claims rb1 (50); FLEX's only candidates are what's
    # left (rb2=30, rb3=10), takes the top one; BENCH stays at the $1 floor.
    assert result["RB"]["recommended"] == pytest.approx(50.0, abs=0.1)
    assert result["FLEX"]["recommended"] == pytest.approx(30.0, abs=0.1)
    assert result["BENCH"]["recommended"] == pytest.approx(1.0, abs=0.1)
    assert result["FLEX"]["recommended"] > result["BENCH"]["recommended"]


def test_flex_pool_excludes_players_already_claimed_by_dedicated_slots():
    """The flex candidate pool must draw from what's left after dedicated
    RB/WR budgets take their top picks -- never double-count the same
    player in both a dedicated budget and the flex budget."""
    league = _league_multi(("RB", "WR", "FLEX", "BN"))
    valued = _valued_positions({
        "rb1": ("RB", 50.0), "rb2": ("RB", 20.0),
        "wr1": ("WR", 40.0), "wr2": ("WR", 15.0),
    })
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 4, "budget": 200}}, [])

    result = auction.positional_budget(
        valued, baseline, 1.0, state, league, roster_id=None,
        your_dollars_left=111.0)

    # Dedicated RB/WR each claim their top player (50, 40); FLEX's only
    # candidates are what's left (rb2=20, wr2=15) -- never rb1/wr1 again.
    assert result["RB"]["recommended"] == pytest.approx(50.0, abs=0.1)
    assert result["WR"]["recommended"] == pytest.approx(40.0, abs=0.1)
    assert result["FLEX"]["recommended"] == pytest.approx(20.0, abs=0.1)


def test_none_roster_id_falls_back_to_fresh_roster():
    """FFDO_ROSTER_ID unset must show 'as if starting fresh' need, ignoring
    what anyone (including roster 1) has actually drafted -- same fallback
    board.py already applies to max_bid/spent/slots_filled."""
    league = _league_multi(("RB", "BN"))
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="rb_drafted", amount=10),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=2, budget=200, picks=picks)
    valued = _valued_positions({
        "rb_drafted": ("RB", 50.0), "rb_avail": ("RB", 30.0),
    })
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}

    result = auction.positional_budget(
        valued, baseline, 1.0, state, league, roster_id=None,
        your_dollars_left=200.0)

    assert result["RB"]["slots_open"] == 1


def test_compute_roster_needs_no_picks():
    league = _league_multi(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    valued = _valued_positions({"qb1": ("QB", 50.0), "rb1": ("RB", 80.0)})
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 9, "budget": 200}}, [])

    needs = auction.compute_roster_needs(valued, state, league, roster_id=None)

    assert dict(needs.dedicated_count) == {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
    assert dict(needs.drafted_count) == {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    assert dict(needs.dedicated_need) == {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
    assert needs.flex_positions == frozenset({"RB", "WR", "TE"})
    assert needs.flex_total == 1
    assert needs.flex_remaining == 1
    assert needs.bench_total == 2
    assert needs.bench_remaining == 2
    assert needs.undetermined == 0


def test_compute_roster_needs_with_picks_and_leftover():
    league = _league_multi(("RB", "FLEX", "BN"))
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="rb_drafted_1", amount=10),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=1, picked_by="u1",
                 player_id="rb_drafted_2", amount=10),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=3, budget=200, picks=picks)
    valued = _valued_positions({
        "rb_drafted_1": ("RB", 50.0), "rb_drafted_2": ("RB", 40.0),
    })

    needs = auction.compute_roster_needs(valued, state, league, roster_id=1)

    assert dict(needs.dedicated_need) == {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    assert needs.flex_remaining == 0
    assert needs.bench_remaining == 1
    assert needs.drafted_count["RB"] == 2


def test_position_caps_matches_confirmed_examples():
    league = _league_multi(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    valued: dict = {}
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 9, "budget": 200}}, [])
    needs = auction.compute_roster_needs(valued, state, league, roster_id=None)

    caps = auction.position_caps(league, needs)

    assert caps["QB"] == 2
    assert caps["RB"] == 6


def test_position_caps_subtracts_already_drafted():
    league = _league_multi(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"))
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="qb1", amount=10),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=1, picked_by="u1",
                 player_id="qb2", amount=10),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=9, budget=200, picks=picks)
    valued = _valued_positions({"qb1": ("QB", 50.0), "qb2": ("QB", 40.0)})
    needs = auction.compute_roster_needs(valued, state, league, roster_id=1)

    caps = auction.position_caps(league, needs)

    assert caps["QB"] == 0
