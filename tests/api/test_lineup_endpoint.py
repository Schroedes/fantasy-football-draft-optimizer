"""`GET /api/leagues/{league_key}/lineup` -- this week's optimal-lineup diff."""

from fastapi.testclient import TestClient

from ffdo.api import app as app_mod
from ffdo.api.app import create_app
from ffdo.api.lineup_ledger import LineupLedger
from ffdo.api.store import LeagueStore
from ffdo.ingest.client import PROJECTIONS, V1
from tests.api.test_season_endpoint import _PLAYERS, _ROSTERS, _STATE, _USERS, _tracked

SCHEDULE = "https://api.sleeper.app/schedule/nfl/regular"

_WEEKLY_PROJ = [
    {"player_id": "p_qb", "stats": {"pass_yd": 260.0, "pass_td": 2.0}},
    {"player_id": "p_rb", "stats": {"rush_yd": 90.0, "rush_td": 1.0, "rec": 3.0, "rec_yd": 20.0}},
    {"player_id": "p_wr", "stats": {"rec": 7.0, "rec_yd": 95.0, "rec_td": 1.0}},
    {"player_id": "p_rb2", "stats": {"rush_yd": 60.0, "rush_td": 0.0, "rec": 2.0, "rec_yd": 15.0}},
]
# p_qb -> AAA (unlocked), p_rb -> BBB (locked), p_wr -> CCC (unlocked),
# p_rb2 -> DDD (locked) -- see _PLAYERS in test_season_endpoint for team assignment.
_SCHEDULE_2026 = [
    {"status": "pre_game", "date": "2026-11-15", "home": "AAA", "away": "CCC",
     "week": 10, "game_id": "1"},
    {"status": "complete", "date": "2026-11-12", "home": "BBB", "away": "DDD",
     "week": 10, "game_id": "2"},
]


def _recording_client(extra=None):
    resp = {
        f"{V1}/state/nfl": _STATE,
        f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS,
        f"{V1}/players/nfl": _PLAYERS,
        f"{PROJECTIONS}/2026/10": _WEEKLY_PROJ,
        f"{SCHEDULE}/2026": _SCHEDULE_2026,
    }
    resp.update(extra or {})

    class _C:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            for key, val in resp.items():
                if key in url:
                    return val
            return []
        def close(self): pass
    return _C


def _seed(monkeypatch, tmp_path, tracked, extra=None):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(tracked)
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr(app_mod, "_LINEUP_LEDGER", LineupLedger(tmp_path / "ffdo.db"))
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _recording_client(extra))


def test_lineup_payload_is_well_formed(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _tracked())
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/lineup")
    assert res.status_code == 200
    body = res.json()

    assert body["nfl_week"] == {"season": 2026, "week": 10}
    # starting_slots for _tracked()'s default roster_positions ("QB","RB","WR","FLEX","BN","BN")
    # is ("QB","RB","WR","FLEX") -- 4 rows.
    assert len(body["diff"]) == 4
    assert body["week_locked"] is False   # AAA/CCC are still pre_game
    assert body["ledger"]["followed"] is None
    assert "recorded_at" in body["ledger"]


def test_lineup_flags_a_swap_when_a_better_unlocked_bench_option_exists(monkeypatch, tmp_path):
    # The base fixture's roster 1 has no bench depth (all 3 rostered
    # players are already starting, FLEX sits empty with nothing to fill
    # it). This test adds two bench WRs (team AAA, still pre_game --
    # unlocked): p_wr2, projected worse than the current WR starter
    # (p_wr) but still worth starting, and p_wr3, projected worse still.
    #
    # p_wr3 exists purely to give `engine.vor.compute` a genuine
    # replacement level to measure against: with only two WRs in the pool
    # (p_wr, p_wr2) and this league's `num_teams=2`, the league-wide
    # replacement-level simulation (`engine.replacement.replacement_levels`,
    # run with `iterations=num_teams`) fully consumes a 2-deep position
    # pool, which pins the *worse* of the two at VOR 0 by construction --
    # there is no room for a genuine, positive-VOR "better bench option"
    # to exist with only 2 players at a position. A 3rd, clearly-worse
    # player breaks that tie so p_wr2's VOR is real and positive.
    #
    # p_wr2 must also rank BELOW the current starter p_wr: `optimal_slots`
    # fills the dedicated WR slot from the top of the WR pool first, so a
    # bench WR that outscores the current starter would take the
    # DEDICATED WR slot outright (displacing p_wr to FLEX or off the
    # lineup), not the empty FLEX slot this test is about.
    extended_players = {**_PLAYERS, "p_wr2": {
        "first_name": "W", "last_name": "R2", "position": "WR", "team": "AAA",
        "age": 23, "years_exp": 1, "active": True,
    }, "p_wr3": {
        "first_name": "W", "last_name": "R3", "position": "WR", "team": "AAA",
        "age": 30, "years_exp": 8, "active": True,
    }}
    custom_rosters = [
        {"roster_id": 1, "owner_id": "U1",
         "players": ["p_qb", "p_rb", "p_wr", "p_wr2", "p_wr3"],
         "starters": ["p_qb", "p_rb", "p_wr", "0"],
         "settings": {"wins": 6, "losses": 3, "fpts": 1284, "fpts_decimal": 0,
                      "fpts_against": 1244, "fpts_against_decimal": 0}},
    ]
    extended_proj = _WEEKLY_PROJ + [
        {"player_id": "p_wr2", "stats": {"rec": 5.0, "rec_yd": 60.0, "rec_td": 0.0}},
        {"player_id": "p_wr3", "stats": {"rec": 1.0, "rec_yd": 5.0, "rec_td": 0.0}},
    ]
    _seed(monkeypatch, tmp_path, _tracked(), extra={
        f"{V1}/league/L1/rosters": custom_rosters,
        f"{V1}/players/nfl": extended_players,
        f"{PROJECTIONS}/2026/10": extended_proj,
    })
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/lineup")
    body = res.json()
    flex_row = next(d for d in body["diff"] if d["slot_label"] == "FLEX")
    assert flex_row["status"] == "suggested_swap"
    assert flex_row["optimal"]["player_id"] == "p_wr2"
    assert flex_row["delta"] > 0


def test_lineup_missed_row_when_current_starters_team_already_locked(monkeypatch, tmp_path):
    # p_rb (roster 1's current RB starter) plays for BBB, which is
    # "complete" in _SCHEDULE_2026 -- locked. Add a 4th player, p_rb3
    # (team AAA, still pre_game), projected much better than p_rb, so
    # `optimal_slots` picks p_rb3 for the dedicated RB slot. Since the
    # CURRENT starter's (p_rb's) team already locked, this must read as
    # "missed", not "suggested_swap" -- the user can no longer bench p_rb.
    extended_players = {**_PLAYERS, "p_rb3": {
        "first_name": "R", "last_name": "B3", "position": "RB", "team": "AAA",
        "age": 22, "years_exp": 0, "active": True,
    }}
    custom_rosters = [
        {"roster_id": 1, "owner_id": "U1",
         "players": ["p_qb", "p_rb", "p_wr", "p_rb3"],
         "starters": ["p_qb", "p_rb", "p_wr", "0"],
         "settings": {"wins": 6, "losses": 3, "fpts": 1284, "fpts_decimal": 0,
                      "fpts_against": 1244, "fpts_against_decimal": 0}},
    ]
    extended_proj = _WEEKLY_PROJ + [
        {"player_id": "p_rb3", "stats": {"rush_yd": 200.0, "rush_td": 2.0}},
    ]
    _seed(monkeypatch, tmp_path, _tracked(), extra={
        f"{V1}/league/L1/rosters": custom_rosters,
        f"{V1}/players/nfl": extended_players,
        f"{PROJECTIONS}/2026/10": extended_proj,
    })
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/lineup")
    body = res.json()
    rb_row = next(d for d in body["diff"] if d["slot_label"] == "RB")
    assert rb_row["status"] == "missed"
    assert rb_row["current"]["player_id"] == "p_rb"
    assert rb_row["optimal"]["player_id"] == "p_rb3"


def test_lineup_week_locked_true_once_every_game_has_started(monkeypatch, tmp_path):
    all_locked = [{**g, "status": "complete"} for g in _SCHEDULE_2026]
    _seed(monkeypatch, tmp_path, _tracked(), extra={f"{SCHEDULE}/2026": all_locked})
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/lineup")
    assert res.json()["week_locked"] is True


def test_lineup_returns_400_for_a_non_sleeper_provider(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(provider="espn", league_key="espn:E1:2026"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    res = TestClient(create_app()).get("/api/leagues/espn:E1:2026/lineup")
    assert res.status_code == 400


def test_lineup_returns_404_for_an_unknown_league(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    monkeypatch.setattr(app_mod, "_STORE", store)
    res = TestClient(create_app()).get("/api/leagues/sleeper:UNKNOWN:2026/lineup")
    assert res.status_code == 404


def test_lineup_degrades_gracefully_when_schedule_fetch_fails(monkeypatch, tmp_path):
    """The schedule endpoint is unofficial -- a failure must not 502 the
    whole response, per the plan's Global Constraints. Everything just
    reads as unlocked."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr(app_mod, "_LINEUP_LEDGER", LineupLedger(tmp_path / "ffdo.db"))

    base_resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/players/nfl": _PLAYERS,
        f"{PROJECTIONS}/2026/10": _WEEKLY_PROJ,
    }

    class _FlakySchedule:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            if "/schedule/" in url:
                raise RuntimeError("GET ... failed after 4 attempts")
            for key, val in base_resp.items():
                if key in url:
                    return val
            return []
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FlakySchedule)
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/lineup")
    assert res.status_code == 200
    assert res.json()["week_locked"] is False


def test_lineup_ledger_is_written_on_first_view_and_not_overwritten_on_second(
        monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _tracked())
    client = TestClient(create_app())
    first = client.get("/api/leagues/sleeper:L1:2026/lineup").json()
    second = client.get("/api/leagues/sleeper:L1:2026/lineup").json()
    assert first["ledger"]["recorded_at"] == second["ledger"]["recorded_at"]


def test_lineup_ledger_resolves_once_the_week_is_fully_locked(monkeypatch, tmp_path):
    all_locked = [{**g, "status": "complete"} for g in _SCHEDULE_2026]
    _seed(monkeypatch, tmp_path, _tracked())
    client = TestClient(create_app())
    client.get("/api/leagues/sleeper:L1:2026/lineup")   # first view -- records the row

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient",
                        _recording_client({f"{SCHEDULE}/2026": all_locked}))
    resolved = client.get("/api/leagues/sleeper:L1:2026/lineup").json()
    assert resolved["ledger"]["followed"] in ("full", "partial", "none")
