import pytest

from ffdo.api import board
from ffdo.domain.models import DraftPick, DraftState, LeagueProfile, PlayerProfile, ValuedPlayer
from ffdo.ingest import draft


def _league():
    return LeagueProfile(league_id="x", season=2026, num_teams=12,
                         roster_positions=("QB", "RB", "RB", "WR", "WR", "BN"),
                         scoring_settings={}, budget=None)


def _valued():
    out = {}
    for i in range(6):
        for pos in ("RB", "WR"):
            pid = f"{pos}{i}"
            prof = PlayerProfile(player_id=pid, first_name=pos, last_name=str(i),
                                 position=pos, team="X", age=25, years_exp=3,
                                 injury_status=None, active=True)
            out[pid] = ValuedPlayer(profile=prof, projected_points=100.0 - i * 10,
                                    adjusted_points=100.0 - i * 10,
                                    vor=100.0 - i * 10, tier=1, adjustments={})
    return out


def _state():
    return draft.parse({"draft_id": "d", "type": "snake", "status": "drafting",
                        "settings": {"teams": 12, "rounds": 6}}, [])


def test_snake_board_exposes_cost_of_waiting_and_survival():
    valued = _valued()
    survival = {pid: 0.5 for pid in valued}
    cow = {"RB": {"best_now": 100.0, "expected_next": 80.0, "cost": 20.0},
           "WR": {"best_now": 100.0, "expected_next": 95.0, "cost": 5.0}}
    out = board.build_snake_board(_league(), _state(), valued, survival, cow)
    assert out["format"] == "snake"
    assert out["cost_of_waiting"]["RB"]["cost"] == 20.0
    assert all("survival" in r for r in out["players"])


def test_snake_board_has_no_dollar_fields():
    out = board.build_snake_board(_league(), _state(), _valued(),
                                  {pid: 0.5 for pid in _valued()}, {})
    assert "baseline" not in out["players"][0]


def test_players_absent_from_survival_default_to_certain_survival():
    """`simulate_survival` only returns entries for players who carry ADP.
    A player missing from that mapping has no ADP -- not a 0% chance of
    surviving to the next pick. Defaulting to 0.0 rendered "definitely
    gone" for players who are actually certain to still be there, which is
    backwards; the honest default for "no signal" is 1.0.
    """
    valued = _valued()
    pid = next(iter(valued))
    survival_missing_one = {p: 0.5 for p in valued if p != pid}

    out = board.build_snake_board(_league(), _state(), valued,
                                  survival_missing_one, {})
    row = next(r for r in out["players"] if r["player_id"] == pid)
    assert row["survival"] == 1.0


def test_snake_board_also_exposes_rosters():
    from ffdo.domain.models import TeamProfile

    teams = {1: TeamProfile(roster_id=1, display_name="Alpha")}
    out = board.build_snake_board(_league(), _state(), _valued(),
                                  {pid: 0.5 for pid in _valued()}, {},
                                  roster_id=1, teams=teams)
    assert out["rosters"][0]["roster_id"] == 1
    assert out["rosters"][0]["is_you"] is True


def test_snake_board_has_no_budget_field():
    out = board.build_snake_board(_league(), _state(), _valued(),
                                  {pid: 0.5 for pid in _valued()}, {})
    assert "budget" not in out


def test_snake_board_exposes_lineup_value_favoring_unfilled_positions():
    """Reproduces the reported bug: a roster already stacked at RB should
    show near-zero lineup value for another RB, while an unfilled WR slot
    shows full value for a WR candidate -- even though the RB candidate has
    higher raw VOR."""
    league = LeagueProfile(league_id="x", season=2026, num_teams=12,
                           roster_positions=("RB", "RB", "WR", "BN"),
                           scoring_settings={}, budget=None)
    valued = _valued()
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="RB0", amount=None),
        DraftPick(pick_no=2, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="RB1", amount=None),
    )
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=12, rounds=4, budget=None, picks=picks)

    out = board.build_snake_board(league, state, valued,
                                  {pid: 0.5 for pid in valued}, {},
                                  roster_id=1)

    rows = {r["player_id"]: r for r in out["players"]}
    assert rows["WR0"]["lineup_value"] == pytest.approx(rows["WR0"]["vor"])
    assert rows["RB2"]["lineup_value"] == pytest.approx(0.0)


def test_snake_board_lineup_value_falls_back_to_fresh_roster_when_unset():
    """FFDO_ROSTER_ID unset must show 'as if starting fresh' lineup value,
    not silently attach to whichever roster_id happens to be None on a
    commissioner/keeper pick -- same fallback the rest of the board applies
    elsewhere (auction's positional_budget, max_bid, etc.)."""
    league = _league()
    valued = _valued()
    out = board.build_snake_board(league, _state(), valued,
                                  {pid: 0.5 for pid in valued}, {},
                                  roster_id=None)
    rows = {r["player_id"]: r for r in out["players"]}
    assert rows["RB0"]["lineup_value"] == pytest.approx(rows["RB0"]["vor"])


def test_snake_history_is_newest_pick_first_with_grades():
    from ffdo.domain.models import TeamProfile

    valued = _valued()  # RB0..RB5, WR0..WR5; vor = 100 - i*10 within each position
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="RB0", amount=None),  # best VOR (100) on the board -> GREAT
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=2, picked_by="u2",
                 player_id="RB5", amount=None),  # vor=50, but WR0 (vor=100) still on board -> POOR
    )
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=12, rounds=6, budget=None, picks=picks)
    teams = {1: TeamProfile(roster_id=1, display_name="Alpha"),
             2: TeamProfile(roster_id=2, display_name="Bravo")}

    out = board.build_snake_board(_league(), state, valued,
                                  {pid: 0.5 for pid in valued}, {}, teams=teams)

    assert [h["player_id"] for h in out["history"]] == ["RB5", "RB0"]
    assert out["history"][1]["grade"] == "GREAT"
    assert out["history"][0]["grade"] == "POOR"
    assert out["history"][0]["amount"] is None
    assert out["history"][1]["team_name"] == "Alpha"


def test_snake_history_pool_shrinks_as_earlier_picks_are_replayed():
    """The pool a pick is graded against must exclude players already taken
    earlier in the same draft, not just the one player being graded."""
    valued = _valued()
    # Take the top-VOR RB and WR first, then take the (now second-best) RB.
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="RB0", amount=None),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=2, picked_by="u2",
                 player_id="WR0", amount=None),
        DraftPick(pick_no=3, round=1, draft_slot=3, roster_id=1, picked_by="u1",
                 player_id="RB1", amount=None),
    )
    state = DraftState(draft_id="d", draft_type="snake", status="drafting",
                       num_teams=12, rounds=6, budget=None, picks=picks)

    out = board.build_snake_board(_league(), state, valued,
                                  {pid: 0.5 for pid in valued}, {})

    by_id = {h["player_id"]: h for h in out["history"]}
    # RB1 (vor=90) is now the best VOR left on the board -- RB0 and WR0 are
    # already gone -- so it grades GREAT, not merely "good".
    assert by_id["RB1"]["grade"] == "GREAT"
