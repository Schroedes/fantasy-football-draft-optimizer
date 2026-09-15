from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ffdo.api import app as app_mod
from ffdo.api.app import create_app
from ffdo.api.store import LeagueStore
from ffdo.ingest.client import V1

from tests.api.test_season_endpoint import (
    _MATCHUPS, _PLAYERS, _PROJ, _ROSTERS, _STATE, _USERS, _tracked)


def _base_resp(rosters=None, matchups_by_week=None):
    resp = {
        f"{V1}/state/nfl": _STATE,
        f"{V1}/league/L1/rosters": rosters if rosters is not None else _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
    }
    for week, rows in (matchups_by_week or {}).items():
        resp[f"{V1}/league/L1/matchups/{week}"] = rows
    return resp


class _FakeClient:
    def __init__(self, resp, *a, **k):
        self._resp = resp
    def get_json(self, url, *a, **k):
        # Prefer the most specific (longest) matching key rather than the
        # first insertion-order match -- the generic "/matchups/" fixture
        # key (shared with the season-endpoint tests, used there for
        # actuals' past-week matchups fetch) is itself a substring of the
        # week-specific "league/L1/matchups/{week}" URL, so a first-match
        # scan would always shadow the week-specific fixture.
        matches = [(key, val) for key, val in self._resp.items() if key in url]
        if matches:
            _, val = max(matches, key=lambda kv: len(kv[0]))
            return val
        if "/transactions/" in url:
            return []
        return [] if "/matchups/" in url or "/projections/" in url else {}
    def close(self): pass


def test_home_summary_is_sleeper_only():
    app_mod._STORE.upsert(_tracked(
        league_key="espn:E1:2026", provider="espn", provider_league_id="E1"))
    res = TestClient(create_app()).get("/api/leagues/espn:E1:2026/home-summary")
    assert res.status_code == 400


def test_home_summary_shape_and_empty_flags_with_minimal_shared_fixture(monkeypatch, tmp_path):
    # The shared _ROSTERS fixture rosters exactly 1 player per position per
    # team (see test_season_endpoint.py) -- no surplus anywhere, so waiver
    # and trade-target flags are deterministically absent, and there's no
    # week-10 matchups row in the fixture, so `matchup` is None. This
    # confirms the response shape end-to-end without needing to hand-derive
    # any real pipeline numbers.
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    resp = _base_resp()
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", lambda *a, **k: _FakeClient(resp))

    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/home-summary")
    assert res.status_code == 200
    data = res.json()
    assert data["league_key"] == "sleeper:L1:2026"
    assert data["provider"] == "sleeper"
    assert data["record"] == {"wins": 6, "losses": 3, "ties": 0}
    assert data["power_rank"]["value"] in (1, 2)
    assert data["power_rank"]["of"] == 2
    assert data["matchup"] is None
    assert isinstance(data["starters"], list)
    assert data["flags"] == {}


def test_home_summary_returns_degraded_response_when_you_have_no_roster(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft", roster_id=None))
    monkeypatch.setattr(app_mod, "_STORE", store)
    resp = _base_resp()
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", lambda *a, **k: _FakeClient(resp))

    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/home-summary")
    assert res.status_code == 200
    data = res.json()
    assert data["record"] is None
    assert data["power_rank"] is None
    assert data["matchup"] is None
    assert data["starters"] == []
    assert data["flags"] == {}


def test_home_summary_includes_a_real_matchup_with_opponent_and_scores(monkeypatch, tmp_path):
    # A local, hand-built fixture (following this initiative's established
    # pattern for a genuine, non-vacuous integration case) with a real
    # week-10 matchups row pairing roster 1 and roster 2 by matchup_id.
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    week10_matchups = [
        {"roster_id": 1, "matchup_id": 1, "players_points": {}},
        {"roster_id": 2, "matchup_id": 1, "players_points": {}},
    ]
    resp = _base_resp(matchups_by_week={10: week10_matchups})
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", lambda *a, **k: _FakeClient(resp))

    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/home-summary")
    assert res.status_code == 200
    data = res.json()
    assert data["matchup"] is not None
    assert data["matchup"]["opponent_name"] == "Them"
    assert isinstance(data["matchup"]["your_projected"], float)
    assert isinstance(data["matchup"]["opponent_projected"], float)


def test_home_summary_matchup_is_none_on_a_bye_week(monkeypatch, tmp_path):
    # Odd team count: a 3rd roster with its own unpaired matchup_id.
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    week10_matchups = [
        {"roster_id": 1, "matchup_id": 5, "players_points": {}},
    ]
    resp = _base_resp(matchups_by_week={10: week10_matchups})
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", lambda *a, **k: _FakeClient(resp))

    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/home-summary")
    assert res.status_code == 200
    assert res.json()["matchup"] is None
