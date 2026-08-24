import pytest

from ffdo.api import board
from ffdo.domain.models import (
    DraftPick, DraftState, LeagueProfile, PlayerProfile, TeamProfile, ValuedPlayer,
)
from ffdo.engine import auction
from ffdo.ingest import draft, snapshot


def _league():
    return LeagueProfile(league_id="x", season=2025, num_teams=12,
                         roster_positions=("RB",) * 14,
                         scoring_settings={}, budget=200)


def _valued(ids):
    out = {}
    for i, pid in enumerate(ids):
        prof = PlayerProfile(player_id=pid, first_name="P", last_name=str(i),
                             position="RB", team="X", age=25, years_exp=3,
                             injury_status=None, active=True)
        out[pid] = ValuedPlayer(profile=prof, projected_points=100.0,
                                adjusted_points=100.0, vor=100.0 - i,
                                tier=1, adjustments={})
    return out


def test_board_marks_drafted_players_and_reports_inflation():
    hist = snapshot.load("league_history")["drafts"]["2025"]
    state = draft.parse(hist["meta"], hist["picks"][:40])
    ids = [p.player_id for p in draft.parse(hist["meta"], hist["picks"]).picks]
    valued = _valued(ids)
    baseline = {pid: 20.0 for pid in ids}

    out = board.build_auction_board(_league(), state, valued, baseline)
    assert out["format"] == "auction"
    assert isinstance(out["inflation"], float)
    drafted = {r["player_id"] for r in out["players"] if r["drafted"]}
    assert drafted == state.drafted_player_ids()


def test_board_rows_are_sorted_by_value_descending():
    ids = [f"p{i}" for i in range(10)]
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 14, "budget": 200}}, [])
    out = board.build_auction_board(_league(), state, _valued(ids),
                                    {pid: 10.0 for pid in ids})
    vors = [r["vor"] for r in out["players"]]
    assert vors == sorted(vors, reverse=True)


def test_max_bid_reflects_the_users_roster_state():
    """`auction.max_bid` was fully tested but never wired into the board --
    the per-player `max_bid` field and the budget strip's `your_*` fields
    must reflect the specific roster identified by `roster_id`, computed
    from that roster's actual spend and slots filled, not the room average.
    """
    league = _league()  # num_teams=12, roster_positions=("RB",)*14, budget=200
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1,
                 picked_by="u1", player_id="p0", amount=50),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=1,
                 picked_by="u1", player_id="p1", amount=0),
        DraftPick(pick_no=3, round=1, draft_slot=3, roster_id=1,
                 picked_by="u1", player_id="p2", amount=0),
        DraftPick(pick_no=4, round=1, draft_slot=4, roster_id=2,
                 picked_by="u2", player_id="p3", amount=30),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=14, budget=200, picks=picks)
    ids = [f"p{i}" for i in range(5, 10)]
    valued = _valued(ids)
    baseline = {pid: 10.0 for pid in ids}

    out = board.build_auction_board(league, state, valued, baseline, roster_id=1)

    expected_max = auction.max_bid(spent=50, slots_filled=3, league=league)
    assert out["budget"]["your_roster_id"] == 1
    assert out["budget"]["your_spent"] == 50
    assert out["budget"]["your_slots_left"] == 14 - 3
    assert out["budget"]["your_dollars_left"] == 200 - 50
    assert all(r["max_bid"] == expected_max for r in out["players"])


def test_max_bid_falls_back_to_a_fresh_roster_when_roster_id_is_unknown():
    """FFDO_ROSTER_ID is optional. Without it, `roster_id` is None and the
    board must show an honestly-labeled 'starting fresh' max bid rather
    than silently attributing another roster's spend to the user.
    """
    league = _league()
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 14, "budget": 200}}, [])
    ids = [f"p{i}" for i in range(3)]
    out = board.build_auction_board(league, state, _valued(ids),
                                    {pid: 10.0 for pid in ids})

    assert out["budget"]["your_roster_id"] is None
    assert out["budget"]["your_spent"] == 0
    assert out["budget"]["your_slots_left"] == league.roster_size
    expected_max = auction.max_bid(spent=0, slots_filled=0, league=league)
    assert all(r["max_bid"] == expected_max for r in out["players"])


def test_adjusted_price_never_drops_below_the_legal_minimum_bid():
    """Real replay hits inflation as low as ~0.345 by pick 120; a $1-baseline
    player must never render below $1, which the league cannot legally bid.
    """
    league = LeagueProfile(league_id="x", season=2026, num_teams=2,
                           roster_positions=("RB", "RB"),
                           scoring_settings={}, budget=1)
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 2, "rounds": 2, "budget": 1}}, [])
    ids = ["p0", "p1", "p2", "p3"]
    valued = _valued(ids)
    baseline = {pid: 1000.0 for pid in ids}

    out = board.build_auction_board(league, state, valued, baseline)

    assert out["inflation"] < 0.01, "factor must actually be tiny for this to test the floor"
    assert all(r["adjusted"] >= auction.MIN_BID for r in out["players"])


def test_live_nomination_is_surfaced_when_player_is_still_available():
    league = _league()
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=14, budget=200, picks=(),
                       nominated_player_id="p0", current_bid=42)
    ids = ["p0", "p1"]
    valued = _valued(ids)
    baseline = {pid: 10.0 for pid in ids}

    out = board.build_auction_board(_league(), state, valued, baseline)

    assert out["live_nomination"] == {"player_id": "p0", "bid": 42}


def test_live_nomination_is_suppressed_once_the_player_is_already_drafted():
    """Sleeper keeps the last nomination in its metadata for a beat after the
    player sells -- surfacing it as still 'on the block' would be stale and
    misleading, so the board omits it once that player shows up as drafted."""
    league = _league()
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1,
                       picked_by="u1", player_id="p0", amount=42),)
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=14, budget=200, picks=picks,
                       nominated_player_id="p0", current_bid=42)
    ids = ["p0", "p1"]
    valued = _valued(ids)
    baseline = {pid: 10.0 for pid in ids}

    out = board.build_auction_board(league, state, valued, baseline)

    assert out["live_nomination"] is None


def test_healthz_returns_ok():
    from fastapi.testclient import TestClient
    from ffdo.api.app import create_app
    client = TestClient(create_app())
    assert client.get("/healthz").json() == {"status": "ok"}


def _teams():
    return {1: TeamProfile(roster_id=1, display_name="Alpha"),
            2: TeamProfile(roster_id=2, display_name="Bravo")}


def test_rosters_payload_includes_every_known_team_even_with_zero_picks():
    league = _league()
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 14, "budget": 200}}, [])
    out = board.build_auction_board(league, state, {}, {}, teams=_teams())
    assert {r["roster_id"] for r in out["rosters"]} == {1, 2}
    assert all(r["starting_vor"] == 0.0 for r in out["rosters"])
    assert all(r["players"] == [] for r in out["rosters"])


def test_your_roster_is_flagged_is_you():
    league = _league()
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1,
                       picked_by="u1", player_id="p0", amount=10),)
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=14, budget=200, picks=picks)
    valued = _valued(["p0"])
    out = board.build_auction_board(league, state, valued, {"p0": 10.0},
                                    roster_id=1, teams=_teams())
    you = next(r for r in out["rosters"] if r["roster_id"] == 1)
    other = next(r for r in out["rosters"] if r["roster_id"] == 2)
    assert you["is_you"] is True
    assert other["is_you"] is False


def test_team_name_falls_back_to_roster_id_label_when_profile_missing():
    league = _league()
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=5,
                       picked_by="u5", player_id="p0", amount=10),)
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=14, budget=200, picks=picks)
    valued = _valued(["p0"])
    out = board.build_auction_board(league, state, valued, {"p0": 10.0})
    row = next(r for r in out["rosters"] if r["roster_id"] == 5)
    assert row["team_name"] == "Team 5"


def test_rosters_sorted_by_starting_vor_descending():
    league = _league()
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1,
                 picked_by="u1", player_id="p0", amount=10),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=2,
                 picked_by="u2", player_id="p1", amount=10),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=14, budget=200, picks=picks)
    valued = _valued(["p0", "p1"])  # p0 has higher VOR than p1, see _valued()
    out = board.build_auction_board(league, state, valued,
                                    {"p0": 10.0, "p1": 10.0}, teams=_teams())
    assert [r["roster_id"] for r in out["rosters"]] == [1, 2]


def test_roster_players_are_flagged_starter_or_bench():
    league = LeagueProfile(league_id="x", season=2026, num_teams=12,
                           roster_positions=("RB", "RB", "BN"),
                           scoring_settings={}, budget=200)
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1,
                 picked_by="u1", player_id="p0", amount=10),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=1,
                 picked_by="u1", player_id="p1", amount=10),
        DraftPick(pick_no=3, round=1, draft_slot=3, roster_id=1,
                 picked_by="u1", player_id="p2", amount=10),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=3, picks=picks, budget=200)
    valued = _valued(["p0", "p1", "p2"])  # descending VOR: p0 > p1 > p2
    out = board.build_auction_board(league, state, valued,
                                    {pid: 10.0 for pid in ("p0", "p1", "p2")})
    row = next(r for r in out["rosters"] if r["roster_id"] == 1)
    by_id = {p["player_id"]: p for p in row["players"]}
    assert by_id["p0"]["starter"] is True
    assert by_id["p1"]["starter"] is True
    assert by_id["p2"]["starter"] is False


def test_board_includes_positional_budget_recommendation():
    league = LeagueProfile(league_id="x", season=2025, num_teams=12,
                           roster_positions=("QB", "RB", "RB", "WR", "WR", "TE",
                                            "FLEX", "BN", "BN"),
                           scoring_settings={}, budget=200)
    state = draft.parse({"draft_id": "d", "type": "auction", "status": "drafting",
                         "settings": {"teams": 12, "rounds": 9, "budget": 200}}, [])
    ids = ["p0", "p1", "p2"]
    valued = _valued(ids)
    baseline = {pid: 10.0 for pid in ids}

    out = board.build_auction_board(league, state, valued, baseline, roster_id=None)

    by_pos = out["budget"]["by_position"]
    assert set(by_pos) == {"QB", "RB", "WR", "TE", "flex_bench_reserve", "flex_bench_slots_open"}
    assert by_pos["RB"]["slots_open"] == 2
    total = (by_pos["QB"]["recommended"] + by_pos["RB"]["recommended"]
             + by_pos["WR"]["recommended"] + by_pos["TE"]["recommended"]
             + by_pos["flex_bench_reserve"])
    assert total == pytest.approx(out["budget"]["your_dollars_left"], abs=0.5)


def test_positional_budget_slot_invariant_holds_for_a_real_roster():
    """The strip's per-position slot counts and the header's 'your slots
    left' stat render six inches apart on the same page -- nothing
    previously checked that they agree for a roster with real picks."""
    league = LeagueProfile(league_id="x", season=2025, num_teams=12,
                           roster_positions=("QB", "RB", "RB", "WR", "WR", "TE",
                                            "FLEX", "BN", "BN"),
                           scoring_settings={}, budget=200)
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1, picked_by="u1",
                 player_id="rb1", amount=50),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=9, budget=200, picks=picks)

    def _player(pid, pos, vor):
        prof = PlayerProfile(player_id=pid, first_name="P", last_name=pid,
                             position=pos, team="X", age=25, years_exp=3,
                             injury_status=None, active=True)
        return ValuedPlayer(profile=prof, projected_points=0.0,
                            adjusted_points=0.0, vor=vor, tier=1,
                            adjustments={})

    valued = {
        "rb1": _player("rb1", "RB", 80.0),
        "qb1": _player("qb1", "QB", 50.0),
        "rb2": _player("rb2", "RB", 60.0),
        "wr1": _player("wr1", "WR", 90.0),
        "wr2": _player("wr2", "WR", 85.0),
        "te1": _player("te1", "TE", 30.0),
    }
    baseline = {pid: max(1.0, vp.vor) for pid, vp in valued.items()}

    out = board.build_auction_board(league, state, valued, baseline, roster_id=1)

    by_pos = out["budget"]["by_position"]
    slots_accounted = (
        sum(by_pos[p]["slots_open"] for p in ("QB", "RB", "WR", "TE"))
        + by_pos["flex_bench_slots_open"])
    assert slots_accounted == out["budget"]["your_slots_left"]
    # roster 1 already drafted 1 of 2 dedicated RB slots
    assert by_pos["RB"]["slots_open"] == 1


def test_auction_history_is_newest_pick_first_with_grades():
    league = _league()
    picks = (
        DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1,
                 picked_by="u1", player_id="p0", amount=60),
        DraftPick(pick_no=2, round=1, draft_slot=2, roster_id=2,
                 picked_by="u2", player_id="p1", amount=130),
    )
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=14, budget=200, picks=picks)
    ids = ["p0", "p1"]
    valued = _valued(ids)
    baseline = {pid: 100.0 for pid in ids}

    out = board.build_auction_board(league, state, valued, baseline, teams=_teams())

    assert [h["player_id"] for h in out["history"]] == ["p1", "p0"]
    assert out["history"][0]["grade"] == "POOR"   # p1: paid 130 vs baseline 100
    assert out["history"][1]["grade"] == "GREAT"  # p0: paid 60 vs baseline 100
    assert out["history"][0]["team_name"] == "Bravo"
    assert out["history"][0]["amount"] == 130


def test_auction_history_grade_is_none_when_no_amount_was_recorded():
    """A keeper/commissioner pick can land with no bid amount -- there's
    nothing to compare against, so it must not fabricate a grade."""
    league = _league()
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=1,
                       picked_by="u1", player_id="p0", amount=None),)
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=14, budget=200, picks=picks)
    valued = _valued(["p0"])
    out = board.build_auction_board(league, state, valued, {"p0": 100.0})
    assert out["history"][0]["grade"] is None
    assert out["history"][0]["amount"] is None


def test_auction_history_team_name_falls_back_when_profile_missing():
    league = _league()
    picks = (DraftPick(pick_no=1, round=1, draft_slot=1, roster_id=5,
                       picked_by="u5", player_id="p0", amount=10),)
    state = DraftState(draft_id="d", draft_type="auction", status="drafting",
                       num_teams=12, rounds=14, budget=200, picks=picks)
    valued = _valued(["p0"])
    out = board.build_auction_board(league, state, valued, {"p0": 10.0})
    assert out["history"][0]["team_name"] == "Team 5"
