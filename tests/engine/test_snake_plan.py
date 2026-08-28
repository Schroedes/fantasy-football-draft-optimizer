import numpy as np
import pytest

from ffdo.domain.models import DraftPick, DraftState, LeagueProfile, PlayerProfile, ValuedPlayer
from ffdo.engine import roster as roster_engine
from ffdo.engine import snake_plan


def _league_multi(roster_positions, n=12, budget=None):
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


def test_pick_no_for_standard_snake_order():
    assert snake_plan._pick_no_for(1, draft_slot=1, num_teams=4) == 1
    assert snake_plan._pick_no_for(1, draft_slot=4, num_teams=4) == 4
    assert snake_plan._pick_no_for(2, draft_slot=4, num_teams=4) == 5  # round 2 reverses
    assert snake_plan._pick_no_for(2, draft_slot=1, num_teams=4) == 8
    assert snake_plan._pick_no_for(3, draft_slot=1, num_teams=4) == 9  # round 3 reverses back


def test_slot_for_pick_round_trips_pick_no_for():
    for round_no in range(1, 6):
        for slot in range(1, 5):
            pick_no = snake_plan._pick_no_for(round_no, slot, num_teams=4)
            assert snake_plan._slot_for_pick(pick_no, num_teams=4) == slot


def test_your_draft_slot_reads_off_your_own_pick():
    picks = (DraftPick(pick_no=3, round=1, draft_slot=3, roster_id=7, picked_by="u",
                       player_id="p1", amount=None),)
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=10, rounds=15, budget=None, picks=picks)
    assert snake_plan._your_draft_slot(state, roster_id=7) == 3


def test_your_draft_slot_is_none_before_your_first_pick():
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=10, rounds=15, budget=None, picks=())
    assert snake_plan._your_draft_slot(state, roster_id=7) is None
    assert snake_plan._your_draft_slot(state, roster_id=None) is None


def test_your_draft_slot_uses_draft_order_before_your_first_pick():
    """ESPN (unlike Sleeper's real-league feed) knows every team's seat
    before the draft starts -- see DraftState.draft_order and
    ffdo.ingest.espn.draft.parse. When it's present, your seat must be
    knowable immediately, not only after your own first pick."""
    state = DraftState(draft_id="d", draft_type="snake", status="pre_draft",
                       num_teams=10, rounds=15, budget=None, picks=(),
                       draft_order={7: 3})
    assert snake_plan._your_draft_slot(state, roster_id=7) == 3


def test_your_draft_slot_prefers_draft_order_over_inferring_from_picks():
    """Same value either way in this fixture, but proves draft_order is
    consulted first rather than picks always winning -- a mismatch here
    would mean a stale or wrong draft_order silently overrides a real
    pick, which must never happen for any correct provider payload since
    they're required to describe the same seat."""
    picks = (DraftPick(pick_no=3, round=1, draft_slot=3, roster_id=7, picked_by="u",
                       player_id="p1", amount=None),)
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=10, rounds=15, budget=None, picks=picks,
                       draft_order={7: 3})
    assert snake_plan._your_draft_slot(state, roster_id=7) == 3


def test_your_draft_slot_falls_back_to_picks_when_draft_order_is_unset():
    """Sleeper's real-league draft feed never populates draft_order (only
    its own separate mock-draft-only signal does) -- this is the
    no-regression case: behavior for Sleeper must stay exactly what it was
    before draft_order existed."""
    picks = (DraftPick(pick_no=3, round=1, draft_slot=3, roster_id=7, picked_by="u",
                       player_id="p1", amount=None),)
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=10, rounds=15, budget=None, picks=picks)
    assert state.draft_order is None
    assert snake_plan._your_draft_slot(state, roster_id=7) == 3


def test_your_draft_slot_is_none_when_draft_order_exists_but_omits_this_roster():
    state = DraftState(draft_id="d", draft_type="snake", status="pre_draft",
                       num_teams=10, rounds=15, budget=None, picks=(),
                       draft_order={1: 1, 2: 2})
    assert snake_plan._your_draft_slot(state, roster_id=7) is None


def test_need_weights_full_for_open_dedicated_slot():
    league = _league_multi(("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN"))
    weights = snake_plan._need_weights({}, league)
    assert weights["QB"] == 1.0
    assert weights["RB"] == 1.0


def test_need_weights_reduced_once_only_flex_room_remains():
    league = _league_multi(("RB", "FLEX", "BN"))
    roster = _valued_positions({"rb1": ("RB", 50.0)})  # dedicated RB filled
    weights = snake_plan._need_weights(roster, league)
    assert weights["RB"] == 0.85


def test_need_weights_low_once_fully_staffed():
    league = _league_multi(("RB", "BN"))  # no flex at all in this league
    roster = _valued_positions({"rb1": ("RB", 50.0)})
    weights = snake_plan._need_weights(roster, league)
    assert weights["RB"] == 0.15


def test_need_weights_covers_def_and_k_not_just_offense_positions():
    """DEF/K aren't in OFFENSE_POSITIONS -- this proves the position
    universe is genuinely derived from league.roster_positions, not
    hardcoded, so a league that rosters them doesn't leave them silently
    unweighted (and therefore unpickable -- see the rollout-level
    regression test in Task 3)."""
    league = _league_multi(("QB", "DEF", "K", "BN"))
    weights = snake_plan._need_weights({}, league)
    assert weights["DEF"] == 1.0
    assert weights["K"] == 1.0


def test_need_weights_def_and_k_are_never_flex_eligible():
    league = _league_multi(("DEF", "K", "FLEX", "BN"))
    roster = _valued_positions({"def1": ("DEF", 50.0), "k1": ("K", 20.0)})
    weights = snake_plan._need_weights(roster, league)
    # Both dedicated slots are now filled; DEF/K never appear in any
    # FLEX_ELIGIBILITY value set, so the open FLEX slot must NOT grant
    # them the 0.85 flex-eligible weight -- straight to bench-tier 0.15.
    assert weights["DEF"] == 0.15
    assert weights["K"] == 0.15


def _empty_available_state(num_teams=12, rounds=15):
    return DraftState(draft_id="d", draft_type="snake", status="drafting",
                      num_teams=num_teams, rounds=rounds, budget=None, picks=())


def test_simulate_snake_plan_returns_none_before_your_first_pick():
    league = _league_multi(("QB", "RB", "BN"), n=2)
    state = _empty_available_state(num_teams=2, rounds=3)
    result = snake_plan.simulate_snake_plan({}, {}, state, league, roster_id=1)
    assert result is None


def test_simulate_snake_plan_is_deterministic_with_a_seeded_rng():
    league = _league_multi(("QB", "RB", "BN"), n=2)
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u",
                       player_id="qb1", amount=None),)
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=2, rounds=3, budget=None, picks=picks)
    valued = _valued_positions({
        "qb1": ("QB", 40.0), "rb1": ("RB", 50.0), "rb2": ("RB", 45.0),
        "wr1": ("WR", 30.0), "filler": ("RB", 5.0),
    })
    adp = {"rb1": 2.0, "rb2": 3.0, "wr1": 4.0, "filler": 40.0}

    result_a = snake_plan.simulate_snake_plan(
        valued, adp, state, league, roster_id=1, sims=20, rng=np.random.default_rng(7))
    result_b = snake_plan.simulate_snake_plan(
        valued, adp, state, league, roster_id=1, sims=20, rng=np.random.default_rng(7))
    assert result_a == result_b


def test_simulate_snake_plan_output_shape():
    league = _league_multi(("QB", "RB", "BN"), n=2)
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u",
                       player_id="qb1", amount=None),)
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=2, rounds=3, budget=None, picks=picks)
    # num_teams=2 means draft_slot 1's remaining picks (rounds 2, 3) land at
    # pick_no 4 then 5 -- a stretch of 2 opponent picks before pick 4, then a
    # back-to-back turn (0-gap) into pick 5. Simulating both of your future
    # picks therefore needs at least 4 distinct non-drafted players (2 for
    # the opponent stretch + 1 for each of your two picks); only 3 (rb1,
    # rb2, filler) would starve the second pick every trial, so a 4th
    # (filler2, deliberately absent from adp so it always survives the
    # opponent-stretch draw) is required for the pool to cover demand.
    valued = _valued_positions({
        "qb1": ("QB", 40.0), "rb1": ("RB", 50.0), "rb2": ("RB", 45.0),
        "filler": ("RB", 5.0), "filler2": ("RB", 1.0),
    })
    adp = {"rb1": 2.0, "rb2": 3.0, "filler": 40.0}

    result = snake_plan.simulate_snake_plan(
        valued, adp, state, league, roster_id=1, sims=20, rng=np.random.default_rng(1))

    assert result is not None
    assert len(result["picks"]) == 2  # rounds 2 and 3 remain
    for p in result["picks"]:
        assert 0.0 <= p["position_hit_rate"] <= 1.0
        assert 0.0 <= p["player_hit_rate"] <= 1.0
    assert result["sims_run"] == 20


def test_need_weighting_changes_the_simulated_pick_vs_raw_vor():
    """Prove the heuristic actually drives the rollout's choice, not just
    that it runs -- without need-weighting, raw VOR alone would pick the
    QB (VOR 100) over the RB (VOR 50); with it, the RB wins because QB is
    already fully staffed and RB is your only remaining real need. A
    single-team league with no gap before your next pick keeps this
    deterministic regardless of adp/rng."""
    league = _league_multi(("QB", "RB", "BN"), n=1)
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u",
                       player_id="qb_drafted", amount=None),)
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=1, rounds=3, budget=None, picks=picks)
    valued = _valued_positions({
        "qb_drafted": ("QB", 40.0), "qb_great": ("QB", 100.0),
        "rb_good": ("RB", 50.0), "filler": ("RB", 5.0),
    })
    adp = {"qb_great": 1.0, "rb_good": 5.0, "filler": 50.0}

    result = snake_plan.simulate_snake_plan(
        valued, adp, state, league, roster_id=1, sims=20, rng=np.random.default_rng(0))

    assert result is not None
    assert result["picks"][0]["most_likely_position"] == "RB"


def test_simulate_snake_plan_scores_final_roster_with_the_exact_function():
    """expected_starting_vor must come from roster.team_lineup(), not the
    cheap heuristic -- verified by checking it's a real, sane VOR number
    given the fixture's players, not just present."""
    league = _league_multi(("QB", "BN"), n=1)
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u",
                       player_id="qb1", amount=None),)
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=1, rounds=2, budget=None, picks=picks)
    valued = _valued_positions({"qb1": ("QB", 40.0), "filler": ("RB", 5.0)})
    adp = {"filler": 10.0}

    result = snake_plan.simulate_snake_plan(
        valued, adp, state, league, roster_id=1, sims=10, rng=np.random.default_rng(2))

    # Only qb1 can ever start (BN never counts toward starting_vor); filler
    # always ends up on the single BN slot regardless of the heuristic.
    assert result["expected_starting_vor"] == pytest.approx(40.0, abs=0.01)


def test_simulate_snake_plan_can_draft_a_kicker_when_its_the_only_real_need():
    """DEF/K must be pickable by the rollout, not silently invisible --
    regression test for the gap found when DEF/K scoring/VOR support was
    added to the rest of the app (see domain/constants.py's
    is_defense_scoring_key/is_kicking_scoring_key and vor.compute()'s own
    "no engine change needed" test) after this feature's design assumed
    OFFENSE_POSITIONS-only candidates. Single-team, no-gap setup keeps
    this deterministic regardless of adp/rng, same trick as the
    need-weighting test above."""
    league = _league_multi(("QB", "K", "BN"), n=1)
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u",
                       player_id="qb1", amount=None),)
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=1, rounds=3, budget=None, picks=picks)
    valued = _valued_positions({
        "qb1": ("QB", 40.0), "k_great": ("K", 20.0), "filler": ("RB", 5.0),
    })
    adp = {"k_great": 5.0, "filler": 50.0}

    result = snake_plan.simulate_snake_plan(
        valued, adp, state, league, roster_id=1, sims=20, rng=np.random.default_rng(0))

    assert result is not None
    assert result["picks"][0]["most_likely_position"] == "K"
