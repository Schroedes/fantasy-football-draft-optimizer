from ffdo.api import board
from ffdo.domain.models import (
    DraftPick, DraftState, LeagueProfile, PlayerProfile, ValuedPlayer,
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


def test_healthz_returns_ok():
    from fastapi.testclient import TestClient
    from ffdo.api.app import create_app
    client = TestClient(create_app())
    assert client.get("/healthz").json() == {"status": "ok"}
