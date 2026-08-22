from ffdo.api import board
from ffdo.domain.models import LeagueProfile, PlayerProfile, ValuedPlayer
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


def test_healthz_returns_ok():
    from fastapi.testclient import TestClient
    from ffdo.api.app import create_app
    client = TestClient(create_app())
    assert client.get("/healthz").json() == {"status": "ok"}
