import numpy as np
import pytest

from ffdo.domain.models import DraftPick, DraftState, LeagueProfile, PlayerProfile, ValuedPlayer
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
