from ffdo.api import board
from ffdo.domain.models import LeagueProfile, PlayerProfile, ValuedPlayer
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
