"""`GET /api/leagues/{league_key}/season` -- the season-view payload.

Every test drives a fake `SleeperClient` that answers by URL substring, so
the endpoint's real control flow (nfl state -> players -> projection anchor
-> rosters -> actuals -> traded picks -> valuation -> power ranking) runs
end to end with no network.
"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ffdo.api import app as app_mod
from ffdo.api.app import create_app
from ffdo.api.store import LeagueStore
from ffdo.domain.models import TrackedLeague
from ffdo.ingest.client import V1


def _tracked(**over):
    base = dict(
        league_key="sleeper:L1:2026", provider="sleeper", provider_league_id="L1",
        season=2026, name="Test League", user_id="U1", roster_id=1,
        draft_id="D1", draft_type="snake", draft_status="complete",
        num_teams=2, budget=None, rounds=15,
        roster_positions=("QB", "RB", "WR", "FLEX", "BN", "BN"),
        scoring_settings={"pass_yd": 0.04, "pass_td": 4.0, "rush_yd": 0.1,
                          "rush_td": 6.0, "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0},
        fmt="redraft", format_override=None, raw_settings={"draft_rounds": 4},
        is_mock=False, tracked_at="2026-09-03T00:00:00+00:00",
        last_refreshed_at="2026-09-03T00:00:00+00:00",
    )
    return TrackedLeague(**{**base, **over})


_STATE = {"season": "2026", "season_type": "regular", "week": 10, "display_week": 10}
_PLAYERS = {
    "p_qb": {"first_name": "Q", "last_name": "B", "position": "QB", "team": "AAA",
             "age": 27, "years_exp": 5, "active": True},
    "p_rb": {"first_name": "R", "last_name": "B", "position": "RB", "team": "BBB",
             "age": 24, "years_exp": 3, "active": True},
    "p_wr": {"first_name": "W", "last_name": "R", "position": "WR", "team": "CCC",
             "age": 26, "years_exp": 4, "active": True},
    "p_rb2": {"first_name": "R", "last_name": "B2", "position": "RB", "team": "DDD",
              "age": 29, "years_exp": 7, "active": True},
}
_PROJ = [
    {"player_id": "p_qb", "last_modified": int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
     "stats": {"pass_yd": 4200.0, "pass_td": 30.0}},
    {"player_id": "p_rb", "last_modified": int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
     "stats": {"rush_yd": 1100.0, "rush_td": 9.0, "rec": 40.0, "rec_yd": 300.0}},
    {"player_id": "p_wr", "last_modified": int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
     "stats": {"rec": 90.0, "rec_yd": 1200.0, "rec_td": 8.0}},
    {"player_id": "p_rb2", "last_modified": int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
     "stats": {"rush_yd": 900.0, "rush_td": 6.0, "rec": 30.0, "rec_yd": 200.0}},
]
_ROSTERS = [
    {"roster_id": 1, "owner_id": "U1", "players": ["p_qb", "p_rb", "p_wr"],
     "starters": ["p_qb", "p_rb", "p_wr", "0"],
     "settings": {"wins": 6, "losses": 3, "fpts": 1284, "fpts_decimal": 0,
                  "fpts_against": 1244, "fpts_against_decimal": 0}},
    {"roster_id": 2, "owner_id": "U2", "players": ["p_rb2"],
     "starters": ["p_rb2"], "settings": {"wins": 3, "losses": 6, "fpts": 1100, "fpts_against": 1250}},
]
_USERS = [{"user_id": "U1", "display_name": "You"}, {"user_id": "U2", "display_name": "Them"}]
_MATCHUPS = [{"roster_id": 1, "players_points": {"p_rb": 90.0}},
             {"roster_id": 2, "players_points": {"p_rb2": 88.0}}]


def _recording_client(extra=None):
    resp = {
        f"{V1}/state/nfl": _STATE,
        f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS,
        f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS,
        "/projections/": _PROJ,
        "/matchups/": _MATCHUPS,
    }
    resp.update(extra or {})

    class _C:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass
    return _C


def _seed(monkeypatch, tmp_path, tracked):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(tracked)
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _recording_client())


def test_season_payload_is_well_formed(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _tracked())
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season")
    assert res.status_code == 200
    body = res.json()
    assert body["nfl_week"]["week"] == 10
    assert body["your_roster"]["team_name"] == "You"
    assert len(body["your_roster"]["players"]) == 3
    assert all("value" in p and "starter" in p for p in body["your_roster"]["players"])
    pr = body["power_ranking"]
    assert set(pr["overall"]) == {"starters", "full"}
    assert set(pr["by_position"]) == {"QB", "RB", "WR", "TE"}
    assert len(pr["overall"]["starters"]) == 2
    assert body["draft_capital"] is None                      # redraft
    assert [s["roster_id"] for s in body["standings"]]          # provider standings present


def test_dynasty_league_gets_draft_capital(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _tracked(fmt="dynasty"))
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season")
    body = res.json()
    assert isinstance(body["draft_capital"], list)
    assert body["draft_capital"][0]["picks"]                    # per-year pick chips


def test_404_for_unknown_league(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    monkeypatch.setattr(app_mod, "_STORE", store)
    assert TestClient(create_app()).get("/api/leagues/sleeper:ghost:2026/season").status_code == 404


def test_502_on_provider_outage(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)

    class _Dead:
        def __init__(self, *a, **k): pass
        def get_json(self, *a, **k):
            raise RuntimeError("GET ... failed after 4 attempts")
        def close(self): pass
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _Dead)
    assert TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season").status_code == 502


def test_traded_picks_outage_omits_capital_but_still_renders_the_rest(
        monkeypatch, tmp_path):
    """A dynasty/keeper league whose traded-picks feed alone is down (a
    genuine outage, not the "no trade history" 404 shape) must still get a
    full 200 season payload -- draft_capital degrades to None instead of
    the whole endpoint 502ing, since roster/power-ranking/standings have
    nothing to do with the traded-picks feed."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="dynasty"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE,
        f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS,
        f"{V1}/players/nfl": _PLAYERS,
        "/projections/": _PROJ,
        "/matchups/": _MATCHUPS,
    }

    class _FlakyTradedPicks:
        def __init__(self, *a, **k): pass

        def get_json(self, url, *a, **k):
            if "/traded_picks" in url:
                raise RuntimeError("GET ... failed after 4 attempts")
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}

        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FlakyTradedPicks)

    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season")

    assert res.status_code == 200
    body = res.json()
    assert body["draft_capital"] is None
    assert body["your_roster"]["team_name"] == "You"
    assert len(body["your_roster"]["players"]) == 3
    assert [s["roster_id"] for s in body["standings"]]
    assert set(body["power_ranking"]["by_position"]) == {"QB", "RB", "WR", "TE"}


def test_cross_format_guard_same_roster_different_values(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _tracked(fmt="redraft"))
    redraft = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season").json()

    _seed(monkeypatch, tmp_path, _tracked(fmt="dynasty"))
    dynasty = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season").json()

    r_vals = {p["player_id"]: p["value"] for p in redraft["your_roster"]["players"]}
    d_vals = {p["player_id"]: p["value"] for p in dynasty["your_roster"]["players"]}
    assert r_vals != d_vals    # resolved_format actually threads through roster_value


def test_dynasty_league_fetches_player_history_and_uses_the_real_curve(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="dynasty"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    stats_2025 = {"p_rb": {"gp": 16, "rush_yd": 1200.0}}
    resp = {
        f"{V1}/state/nfl": _STATE,
        f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS,
        f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS,
        "/projections/": _PROJ,
        "/matchups/": _MATCHUPS,
        f"{V1}/stats/nfl/regular/2025": stats_2025,
    }

    calls: list[str] = []

    class _DynastyClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            calls.append(url)
            for key, val in resp.items():
                if key in url:
                    return val
            if "/stats/nfl/regular/" in url:
                return {}   # every other historical season -- no data, that's fine
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _DynastyClient)
    res_dynasty = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season")
    assert res_dynasty.status_code == 200

    # The value comparison below cannot by itself prove the history fetch
    # happened -- annuity_value returns exactly current_full when the curve
    # has no entry for this player's ages, which already differs from
    # redraft's max(0, current_full - banked) whether or not history was ever
    # fetched. So assert the wiring directly, mirroring
    # test_redraft_league_never_calls_the_stats_endpoint's recording pattern:
    # one call per finished season in range(2021, lg.season).
    history_calls = sorted(c for c in calls if "/stats/nfl/regular/" in c)
    assert history_calls == [f"{V1}/stats/nfl/regular/{season}"
                             for season in range(2021, 2026)], history_calls

    store2 = LeagueStore(tmp_path / "ffdo2.db")
    store2.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store2)
    res_redraft = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season")
    assert res_redraft.status_code == 200

    dynasty_val = next(p["value"] for p in res_dynasty.json()["your_roster"]["players"]
                       if p["player_id"] == "p_rb")
    redraft_val = next(p["value"] for p in res_redraft.json()["your_roster"]["players"]
                       if p["player_id"] == "p_rb")
    # p_rb has real banked actuals in _MATCHUPS (90.0), so redraft's
    # max(0, current_full - banked) is strictly below current_full, while
    # dynasty's annuity_value degrades gracefully toward current_full when
    # the real DYNASTY_AGE_CURVE has no entry at this exact age -- the two
    # must differ regardless of the curve's precise fitted values, since a
    # real multi-year adjustment coincidentally landing on exactly +90.0 is
    # not a real risk.
    assert dynasty_val != redraft_val


def test_redraft_league_never_calls_the_stats_endpoint(monkeypatch, tmp_path):
    """A redraft league must not fetch player history at all -- no wasted
    calls for the common case."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE,
        f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS,
        f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS,
        "/projections/": _PROJ,
        "/matchups/": _MATCHUPS,
    }
    calls: list[str] = []

    class _RecordingClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            calls.append(url)
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _RecordingClient)
    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season")
    assert res.status_code == 200
    assert not any("/stats/nfl/regular/" in c for c in calls), (
        f"redraft league must never fetch player history: {calls}")


def test_below_replacement_bench_player_never_shows_negative_value(monkeypatch, tmp_path):
    players = dict(_PLAYERS)
    proj = list(_PROJ)
    for name, rush_yd in (("A", 500), ("B", 400), ("C", 300), ("D", 200)):
        pid = f"p_rb_{name}"
        players[pid] = {"first_name": "Dummy", "last_name": name, "position": "RB",
                         "team": "ZZZ", "age": 25, "years_exp": 2, "active": True}
        proj.append({"player_id": pid,
                     "last_modified": int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
                     "stats": {"rush_yd": rush_yd}})
    rosters = [
        {**_ROSTERS[0], "players": _ROSTERS[0]["players"] + [f"p_rb_{n}" for n in "ABCD"]},
        _ROSTERS[1],
    ]
    _seed(monkeypatch, tmp_path, _tracked())
    # Override the client _seed just installed with one serving the extended
    # roster/player/projection fixtures built above.
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _recording_client({
        f"{V1}/league/L1/rosters": rosters,
        f"{V1}/players/nfl": players,
        "/projections/": proj,
    }))

    res = TestClient(create_app()).get("/api/leagues/sleeper:L1:2026/season")
    assert res.status_code == 200
    body = res.json()

    by_id = {p["player_id"]: p for p in body["your_roster"]["players"]}
    # Dummy "D" (200 rush yards -> 20 pts) is below this league's real RB
    # replacement level (30 pts, set by dummy "C") -- raw VOR is -10.
    assert by_id["p_rb_D"]["value"] == 0.0
    assert all(p["value"] >= 0 for p in body["your_roster"]["players"])
    assert body["your_roster"]["bench_value"] >= 0

    rb_full = next(r for r in body["power_ranking"]["by_position"]["RB"]["full"]
                   if r["roster_id"] == 1)
    assert rb_full["value"] >= 0
    assert rb_full["bench_value"] >= 0
