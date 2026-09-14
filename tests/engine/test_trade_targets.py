from dataclasses import replace

import pytest

from ffdo.domain.models import DraftPickAsset, LeagueProfile, PlayerProfile, RosterEntry, ValuedPlayer
from ffdo.engine import trade_targets


def _league(roster_positions, num_teams=2):
    return LeagueProfile(league_id="x", season=2026, num_teams=num_teams,
                         roster_positions=roster_positions, scoring_settings={}, budget=200)


def _vp(pid, pos, v):
    prof = PlayerProfile(player_id=pid, first_name=pos, last_name=pid, position=pos,
                         team="X", age=26, years_exp=4, injury_status=None, active=True)
    return ValuedPlayer(profile=prof, projected_points=v, adjusted_points=v,
                        vor=v, tier=1, adjustments={})


def _entry(rid, players):
    return RosterEntry(roster_id=rid, team_name=f"T{rid}", player_ids=tuple(players),
                       starter_ids=(), wins=0, losses=0, ties=0,
                       points_for=0.0, points_against=0.0)


def _pick(season, rnd, roster_id):
    return DraftPickAsset(season=season, round=rnd, projected_slot=None,
                          current_owner_roster_id=roster_id, original_roster_id=roster_id,
                          via_team_name=None)


# Flat, hand-verifiable curve: any round-1 pick with projected_slot=None and
# season == current_season (years_out == 0, discount factor 1.0) values at
# exactly 15.0 -- avoids needing PICK_UNCERTAINTY_DISCOUNT_RATE's real value.
_CURVE = {1: {"round_avg": 15.0}}


# ---- _build_offer_package: the value-matching escalation (spec Step 3) ----

def test_build_offer_package_single_player_passes_when_already_fair():
    valued = {"p1": _vp("p1", "RB", 30)}
    built = trade_targets._build_offer_package(
        ["p1"], target_value=25.0, valued=valued, your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    assert built == (("p1",), (), 30.0)


def test_build_offer_package_adds_a_pick_to_bridge_the_gap():
    valued = {"p1": _vp("p1", "RB", 30)}
    pick = _pick(2026, 1, 1)  # slot_value == 15.0 under _CURVE
    built = trade_targets._build_offer_package(
        ["p1"], target_value=50.0, valued=valued, your_picks=[pick],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    # single player alone: |50-30|=20, not < 20 -- fails.
    # +pick: |50-45|=5 < 20 -- passes.
    assert built == (("p1",), (pick,), 45.0)


def test_build_offer_package_adds_a_second_player_when_no_picks_available():
    # Redraft leagues always pass your_picks=[] (the dynasty-vs-redraft
    # branch) -- the pick-bridging step must simply be skipped, not error.
    valued = {"p1": _vp("p1", "RB", 30), "p2": _vp("p2", "RB", 25)}
    built = trade_targets._build_offer_package(
        ["p1", "p2"], target_value=70.0, valued=valued, your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    # single (p1=30): |70-30|=40, fails. No picks -- skip. +p2: 55, |70-55|=15<20 -- passes.
    assert built == (("p1", "p2"), (), 55.0)


def test_build_offer_package_drops_the_pairing_when_nothing_closes_the_gap():
    valued = {"p1": _vp("p1", "RB", 30), "p2": _vp("p2", "RB", 25)}
    pick = _pick(2026, 1, 1)
    built = trade_targets._build_offer_package(
        ["p1", "p2"], target_value=200.0, valued=valued, your_picks=[pick],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    assert built is None


def test_build_offer_package_returns_none_with_no_candidates():
    assert trade_targets._build_offer_package(
        [], target_value=25.0, valued={}, your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10) is None


# ---- _net_value_gain: total roster value before vs. after, with waiver backfill (spec Step 4) ----

_NV_LEAGUE = _league(("QB", "RB", "WR", "BN"))


def test_net_value_gain_positive_with_no_backfill_needed():
    valued = {"t1": _vp("t1", "RB", 40), "o1": _vp("o1", "RB", 20)}
    roster = _entry(1, ["o1"])
    gain = trade_targets._net_value_gain(
        roster, target_ids=("t1",), offer_ids=("o1",), offer_picks=[],
        free_agent_ids=[], valued=valued, league=_NV_LEAGUE,
        pick_curve=_CURVE, current_season=2026, round_size=10)
    # before: 20 (o1). after: 40 (t1, o1 removed, no spot freed). gain = 20.
    assert gain == pytest.approx(20.0)


def test_net_value_gain_backfills_a_freed_spot_with_the_best_free_agent():
    # 2 players offered for 1 targeted -- one spot opens, backfilled with
    # the single best free agent by raw VOR (spec: "the single best
    # available free agent", not one per spot freed).
    valued = {
        "t1": _vp("t1", "RB", 30),
        "o1": _vp("o1", "RB", 20), "o2": _vp("o2", "WR", 15),
        "fa1": _vp("fa1", "WR", 5), "fa2": _vp("fa2", "RB", 8),
    }
    roster = _entry(1, ["o1", "o2"])
    gain = trade_targets._net_value_gain(
        roster, target_ids=("t1",), offer_ids=("o1", "o2"), offer_picks=[],
        free_agent_ids=["fa1", "fa2"], valued=valued, league=_NV_LEAGUE,
        pick_curve=_CURVE, current_season=2026, round_size=10)
    # before: 20+15=35. after: 30 (t1) + best FA (fa2=8) = 38. gain = 3.
    assert gain == pytest.approx(3.0)


def test_net_value_gain_negative_when_offer_outvalues_target():
    valued = {"t1": _vp("t1", "RB", 10), "o1": _vp("o1", "RB", 30)}
    roster = _entry(1, ["o1"])
    gain = trade_targets._net_value_gain(
        roster, target_ids=("t1",), offer_ids=("o1",), offer_picks=[],
        free_agent_ids=[], valued=valued, league=_NV_LEAGUE,
        pick_curve=_CURVE, current_season=2026, round_size=10)
    assert gain == pytest.approx(-20.0)


def test_net_value_gain_subtracts_the_cost_of_any_offered_picks():
    valued = {"t1": _vp("t1", "RB", 52), "o1": _vp("o1", "RB", 30)}
    roster = _entry(1, ["o1"])
    pick = _pick(2026, 1, 1)  # slot_value == 15.0 under _CURVE
    gain = trade_targets._net_value_gain(
        roster, target_ids=("t1",), offer_ids=("o1",), offer_picks=[pick],
        free_agent_ids=[], valued=valued, league=_NV_LEAGUE,
        pick_curve=_CURVE, current_season=2026, round_size=10)
    # player swap alone: 52-30=22. minus pick cost (15) = 7.
    assert gain == pytest.approx(7.0)


# ---- suggest_for_team: the full two-way-fit + dual-filter pipeline ----
#
# 2-team league, no FLEX (QB/RB/RB/WR/WR/TE dedicated + bench). Both teams
# roster exactly 1 QB and 1 TE -- _thinness's capped-position formula gives
# count=1 a floor of 1.0, so QB/TE never read as "Fine" for either team
# (score >= 0.5*1.0 = 0.5 regardless of rank) and never qualify as a target
# or offer position. RB is your surplus / their need; WR is your need /
# their surplus -- the two-way fit the algorithm should find.
_POS_LEAGUE = _league(("QB", "RB", "RB", "WR", "WR", "TE", "BN", "BN", "BN", "BN", "BN"))
_POS_VALUED = {
    "q1": _vp("q1", "QB", 20), "q2": _vp("q2", "QB", 15),
    "r1a": _vp("r1a", "RB", 30), "r1b": _vp("r1b", "RB", 25), "r1c": _vp("r1c", "RB", 5),
    "r2a": _vp("r2a", "RB", 10), "r2b": _vp("r2b", "RB", 8),
    "w1a": _vp("w1a", "WR", 8), "w1b": _vp("w1b", "WR", 6),
    "w2a": _vp("w2a", "WR", 32), "w2b": _vp("w2b", "WR", 20), "w2c": _vp("w2c", "WR", 5),
    "t1": _vp("t1", "TE", 8), "t2": _vp("t2", "TE", 6),
}
_YOUR = _entry(1, ["q1", "r1a", "r1b", "r1c", "w1a", "w1b", "t1"])
_THEIRS = _entry(2, ["q2", "r2a", "r2b", "w2a", "w2b", "w2c", "t2"])
_ALL_ROSTERS = [_YOUR, _THEIRS]


def test_suggest_for_team_picks_the_two_way_fit_and_passes_both_filters():
    suggestions = trade_targets.suggest_for_team(
        _YOUR, _THEIRS, _ALL_ROSTERS, _POS_VALUED, _POS_LEAGUE,
        free_agent_ids=[], your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.target_position == "WR"
    assert s.offer_position == "RB"
    assert s.target_player_ids == ("w2a",)   # their best WR: your worst-need position, their surplus
    assert s.offer_player_ids == ("r1a",)    # your best RB: your surplus, their need
    assert s.offer_picks == ()
    assert s.target_value == pytest.approx(32.0)
    assert s.offer_value == pytest.approx(30.0)
    assert s.net_value_gain == pytest.approx(2.0)
    assert s.differential == pytest.approx(2.0)
    assert s.partner_roster_id == 2
    assert s.partner_team_name == "T2"


def test_suggest_for_team_rejects_a_fair_but_value_negative_pairing():
    # |25-30|=5 is well within the fair band, but giving away your
    # more-valuable RB for a less-valuable WR makes your team worse --
    # net_value_gain must reject it regardless of fairness.
    valued = dict(_POS_VALUED)
    valued["w2a"] = _vp("w2a", "WR", 25)
    suggestions = trade_targets.suggest_for_team(
        _YOUR, _THEIRS, _ALL_ROSTERS, valued, _POS_LEAGUE,
        free_agent_ids=[], your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    assert suggestions == []


def test_suggest_for_team_rejects_a_value_positive_but_lopsided_pairing():
    # Their target WR is worth far more than anything at RB you could
    # offer (single best + a second player still isn't close) -- net value
    # would be positive, but no fair-band package can be built, so no
    # pick-bridge exists here (redraft-shaped: your_picks=[]) and the
    # pairing must be dropped.
    valued = dict(_POS_VALUED)
    valued["w2a"] = _vp("w2a", "WR", 90)
    suggestions = trade_targets.suggest_for_team(
        _YOUR, _THEIRS, _ALL_ROSTERS, valued, _POS_LEAGUE,
        free_agent_ids=[], your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    assert suggestions == []


def test_suggest_for_team_includes_a_bridging_pick_for_dynasty_leagues():
    # A single RB alone (30) isn't fair against a 52-value target
    # (gap=22 >= 20); adding the pick (15) closes it (gap=7 < 20). This is
    # the dynasty-vs-redraft branch actually reachable end-to-end: with
    # your_picks=[] (redraft) this same target would fall through to
    # test_suggest_for_team_rejects_a_value_positive_but_lopsided_pairing's
    # style rejection instead, since the escalation has no pick to reach for.
    valued = dict(_POS_VALUED)
    valued["w2a"] = _vp("w2a", "WR", 52)
    pick = _pick(2026, 1, 1)
    suggestions = trade_targets.suggest_for_team(
        _YOUR, _THEIRS, _ALL_ROSTERS, valued, _POS_LEAGUE,
        free_agent_ids=[], your_picks=[pick],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.offer_player_ids == ("r1a",)
    assert s.offer_picks == (pick,)
    assert s.offer_value == pytest.approx(45.0)
    assert s.target_value == pytest.approx(52.0)
    assert s.net_value_gain == pytest.approx(7.0)


def test_suggest_for_team_folds_already_selected_into_the_hypothetical_roster():
    # w2a (their best WR, the position's usual target) is already checked
    # in the in-progress trade. Once excluded from their candidate pool,
    # their remaining WR depth (w2b=20, w2c=5) sits exactly at
    # starting_reach with no cushion left -- thinness=1.0 pushes their WR
    # need to Moderate, so WR stops qualifying as a target position
    # (target requires the partner to read as Fine/surplus there).
    # already_selected must be folded in before target/offer positions are
    # chosen, not applied as a post-hoc filter on the result.
    suggestions = trade_targets.suggest_for_team(
        _YOUR, _THEIRS, _ALL_ROSTERS, _POS_VALUED, _POS_LEAGUE,
        free_agent_ids=[], your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10,
        already_selected_theirs=frozenset({"w2a"}))
    assert suggestions == []


def test_suggest_for_team_excludes_already_selected_yours_from_target_candidacy():
    # r1a (your best RB, currently checked as something you're ALREADY
    # offering the partner in this in-progress trade) must never be
    # suggested back as a NEW target -- even though folding it into
    # their_hypo for needs-scoring purposes is correct, it must not also
    # become eligible as a candidate the partner's own side could "give"
    # you again. Before the fix, this fixture reproduced target_player_ids
    # == ("r1a",) -- your own player suggested as the ask.
    suggestions = trade_targets.suggest_for_team(
        _YOUR, _THEIRS, _ALL_ROSTERS, _POS_VALUED, _POS_LEAGUE,
        free_agent_ids=[], your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10,
        already_selected_yours=frozenset({"r1a"}))
    for s in suggestions:
        assert "r1a" not in s.target_player_ids
        assert "r1a" not in s.offer_player_ids


def test_suggest_for_team_returns_empty_with_no_two_way_fit():
    # Neither team has any surplus position (everyone rosters exactly
    # their starters, nothing more) -- the >=2-rostered gate excludes
    # every position from both target_positions and offer_positions.
    league = _league(("QB", "RB", "WR", "TE", "BN"))
    valued = {
        "q1": _vp("q1", "QB", 20), "r1": _vp("r1", "RB", 15),
        "w1": _vp("w1", "WR", 10), "t1": _vp("t1", "TE", 5),
        "q2": _vp("q2", "QB", 12), "r2": _vp("r2", "RB", 8),
        "w2": _vp("w2", "WR", 6), "t2": _vp("t2", "TE", 3),
    }
    your_roster = _entry(1, ["q1", "r1", "w1", "t1"])
    their_roster = _entry(2, ["q2", "r2", "w2", "t2"])
    suggestions = trade_targets.suggest_for_team(
        your_roster, their_roster, [your_roster, their_roster], valued, league,
        free_agent_ids=[], your_picks=[],
        pick_curve=_CURVE, current_season=2026, round_size=10)
    assert suggestions == []
