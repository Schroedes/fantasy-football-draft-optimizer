from fastapi.testclient import TestClient

from ffdo.api import app as app_mod
from ffdo.api.app import create_app
from ffdo.api.store import LeagueStore
from ffdo.api.trade_ledger import TradeLedger
from ffdo.ingest.client import V1

# Reuses the same _tracked/_ROSTERS/_USERS/_PLAYERS/_STATE/_PROJ/_MATCHUPS
# fixtures already defined in tests/api/test_season_endpoint.py -- read
# that file first to confirm they are still present under these exact
# names before importing them here.
from tests.api.test_season_endpoint import (
    _MATCHUPS, _PLAYERS, _PROJ, _ROSTERS, _STATE, _USERS, _tracked)


def test_trade_evaluate_returns_both_sides_value(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
    }

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeClient)
    client = TestClient(create_app())
    body = {"partner_roster_id": 2,
            "side_a": {"player_ids": ["p_rb"], "picks": []},
            "side_b": {"player_ids": [], "picks": []}}
    res = client.post("/api/leagues/sleeper:L1:2026/trade/evaluate", json=body)
    assert res.status_code == 200
    data = res.json()
    assert "side_a_value" in data and "side_b_value" in data and "differential" in data


def test_trade_builder_returns_every_teams_players_with_values(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
    }

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeClient)
    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/trade-builder")
    assert res.status_code == 200
    teams = res.json()["teams"]
    assert len(teams) == 2

    you = next(t for t in teams if t["is_you"])
    assert you["roster_id"] == 1
    player_ids = {p["player_id"] for p in you["players"]}
    assert player_ids == {"p_qb", "p_rb", "p_wr"}
    for p in you["players"]:
        assert "name" in p and "position" in p and "value" in p

    # Redraft has no future picks to trade.
    assert you["picks"] == []

    partner = next(t for t in teams if not t["is_you"])
    assert partner["roster_id"] == 2
    assert {p["player_id"] for p in partner["players"]} == {"p_rb2"}


def test_trade_builder_includes_future_picks_for_a_dynasty_league(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="dynasty"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
    }

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeClient)
    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/trade-builder")
    assert res.status_code == 200
    teams = res.json()["teams"]
    you = next(t for t in teams if t["is_you"])

    # An empty traded_picks feed still yields every roster's own untraded
    # picks -- 2 draft years x 4 rounds (raw_settings.draft_rounds) each,
    # per traded_picks.capital's implicit-ownership rule.
    assert len(you["picks"]) == 8
    for pick in you["picks"]:
        assert pick["current_owner_roster_id"] == 1
        assert "label" in pick and "value" in pick
        assert "season" in pick and "round" in pick and "projected_slot" in pick
        assert "original_roster_id" in pick


def test_trade_builder_is_sleeper_only():
    from ffdo.domain.models import TrackedLeague

    app_mod._STORE.upsert(_tracked(
        league_key="espn:E1:2026", provider="espn", provider_league_id="E1"))
    res = TestClient(create_app()).get("/api/leagues/espn:E1:2026/trade-builder")
    assert res.status_code == 400


def test_trades_endpoint_returns_empty_list_with_no_real_trades(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
    }

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            if "/transactions/" in url:
                return []
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeClient)
    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/trades")
    assert res.status_code == 200
    assert res.json()["trades"] == []


def test_trades_endpoint_detects_a_trade_in_the_current_in_progress_week(monkeypatch, tmp_path):
    """Regression: the ledger's week-scan bound must be the live current
    week (nfl.week), not the stats-final bound (nfl.week - 1) used for
    scoring -- a trade made during the current, still-in-progress week
    must still be detected, even though that week's points aren't final
    yet. _STATE (from test_season_endpoint) has week=10, so this fixture
    puts a real trade at exactly week 10 and confirms it's found.

    _TRADE_LEDGER is monkeypatched to a tmp_path-backed instance (same
    isolation pattern test_lineup_endpoint.py uses for _LINEUP_LEDGER) --
    it's a write-once ledger, and without this it would record against
    the real shared data/ffdo.db and leak across test runs."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr(app_mod, "_TRADE_LEDGER", TradeLedger(tmp_path / "ffdo.db"))

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
    }
    current_week_trade = [{
        "type": "trade", "status": "complete", "transaction_id": "t-current-week",
        "roster_ids": [1, 2], "adds": {}, "drops": {}, "draft_picks": [],
        "created": 1000,
    }]

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            if "/transactions/10" in url:
                return current_week_trade
            if "/transactions/" in url:
                return []
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeClient)
    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/trades")
    assert res.status_code == 200
    assert [t["transaction_id"] for t in res.json()["trades"]] == ["t-current-week"]
