"""`GET /api/leagues/{league_key}/waivers` -- free-agent add/drop + FAAB bid
recommendations, threading Task 1's real FAAB ingest, Task 4's bid curve,
and Task 7's cap-aware recommend_adds together end to end with no network.
"""

import pytest
from fastapi.testclient import TestClient

from ffdo.api import app as app_mod
from ffdo.api.app import create_app
from ffdo.api.store import LeagueStore
from ffdo.api.waiver_ledger import WaiverLedger
from ffdo.engine import waiver_value
from ffdo.ingest.client import V1

from tests.api.test_season_endpoint import (
    _MATCHUPS, _PLAYERS, _PROJ, _ROSTERS, _STATE, _USERS, _tracked)


def test_waivers_endpoint_returns_recommendations_for_a_faab_league(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
        f"{V1}/league/L1": {"settings": {"waiver_type": 2, "waiver_budget": 100}},
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
    res = client.get("/api/leagues/sleeper:L1:2026/waivers")
    assert res.status_code == 200
    data = res.json()
    assert "remaining_budget" in data
    assert "recommendations" in data
    for rec in data["recommendations"]:
        assert "free_agent_name" in rec and rec["free_agent_name"]
        assert "free_agent_position" in rec
        assert "drop_player_id" in rec
        assert "drop_player_name" in rec
        assert "drop_player_position" in rec
        # Names must be resolved from player profiles, not raw Sleeper IDs.
        assert rec["free_agent_name"] != rec["free_agent_id"]


def test_waivers_endpoint_400s_for_a_non_faab_league(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
        f"{V1}/league/L1": {"settings": {"waiver_type": 1}},  # 1 = rolling priority
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
    res = client.get("/api/leagues/sleeper:L1:2026/waivers")
    assert res.status_code == 400


def test_waivers_endpoint_counts_a_claim_in_the_current_in_progress_week(monkeypatch, tmp_path):
    """Regression, proactive: sub-project #5's GET /trades shipped with a
    bug where the stats-final `through_week` (nfl.week - 1) was reused as
    the transaction-scan bound, silently missing anything that happened
    during the current, still-in-progress week. This must use nfl.week
    directly. _STATE's week is 10 and _tracked's default roster_id is 1
    (both confirmed directly against tests/api/test_season_endpoint.py's
    real fixtures, not assumed)."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))  # roster_id=1 by default
    monkeypatch.setattr(app_mod, "_STORE", store)

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
        f"{V1}/league/L1": {"settings": {"waiver_type": 2, "waiver_budget": 100}},
    }
    current_week_claim = [{
        "type": "waiver", "status": "complete", "transaction_id": "w-current",
        "roster_ids": [1], "adds": {"some_pid": 1},
        "settings": {"waiver_bid": 15}, "created": 1000,
    }]

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            if "/transactions/10" in url:  # _STATE's week == 10
                return current_week_claim
            if "/transactions/" in url:
                return []
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeClient)
    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/waivers")
    assert res.status_code == 200
    # roster 1's remaining budget must reflect the current-week $15 spend
    # (100 - 15 = 85) -- if the endpoint used the stats-final bound
    # instead of nfl.week, /transactions/10 would never be requested and
    # remaining_budget would incorrectly still show the full 100.
    assert res.json()["remaining_budget"] == pytest.approx(85.0)


def test_get_waivers_excludes_reserve_and_taxi_players_from_drop_candidates(
        monkeypatch, tmp_path):
    """IR ('reserve') and Taxi Squad slots are restricted -- get_waivers must
    never offer a player parked in either slot as a drop candidate, even
    though Sleeper's own `players` array (RosterEntry.player_ids) still
    includes them."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))  # roster_id=1 by default
    monkeypatch.setattr(app_mod, "_STORE", store)

    rosters_with_reserve_and_taxi = [
        {**_ROSTERS[0], "reserve": ["p_rb"], "taxi": ["p_wr"]},
        _ROSTERS[1],
    ]
    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": rosters_with_reserve_and_taxi,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
        f"{V1}/league/L1": {"settings": {"waiver_type": 2, "waiver_budget": 100}},
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

    captured = {}
    real_recommend_adds = waiver_value.recommend_adds

    def _spy(free_agent_ids, your_roster_ids, *args, **kwargs):
        captured["your_roster_ids"] = list(your_roster_ids)
        return real_recommend_adds(free_agent_ids, your_roster_ids, *args, **kwargs)

    monkeypatch.setattr(waiver_value, "recommend_adds", _spy)

    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/waivers")
    assert res.status_code == 200
    assert "your_roster_ids" in captured
    assert "p_rb" not in captured["your_roster_ids"]   # in "reserve" (IR)
    assert "p_wr" not in captured["your_roster_ids"]   # in "taxi"
    assert "p_qb" in captured["your_roster_ids"]        # untouched, still droppable


def test_get_waivers_records_your_own_claims_into_the_ledger(monkeypatch, tmp_path):
    from ffdo.api.waiver_ledger import WaiverLedger

    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft"))  # roster_id=1 by default
    monkeypatch.setattr(app_mod, "_STORE", store)
    ledger = WaiverLedger(tmp_path / "ledger.db")
    monkeypatch.setattr(app_mod, "_WAIVER_LEDGER", ledger)

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": _ROSTERS,
        f"{V1}/league/L1/users": _USERS, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": _PLAYERS, "/projections/": _PROJ, "/matchups/": _MATCHUPS,
        f"{V1}/league/L1": {"settings": {"waiver_type": 2, "waiver_budget": 100}},
    }
    your_claim = [{
        "type": "waiver", "status": "complete", "transaction_id": "w-mine",
        "roster_ids": [1], "adds": {"some_pid": 1},
        "settings": {"waiver_bid": 15}, "created": 1000,
    }]

    class _FakeClient:
        def __init__(self, *a, **k): pass
        def get_json(self, url, *a, **k):
            if "/transactions/" in url:
                return your_claim
            for key, val in resp.items():
                if key in url:
                    return val
            return [] if "/matchups/" in url or "/projections/" in url else {}
        def close(self): pass

    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeClient)
    client = TestClient(create_app())

    res = client.get("/api/leagues/sleeper:L1:2026/waivers")
    assert res.status_code == 200

    entry = ledger.get("sleeper:L1:2026", "w-mine")
    assert entry is not None
    assert entry.roster_id == 1
    assert entry.add_player_id == "some_pid"
    assert entry.actual_bid == 15
    assert entry.won is True

    # A second poll must be idempotent -- record_if_absent, not overwrite.
    res2 = client.get("/api/leagues/sleeper:L1:2026/waivers")
    assert res2.status_code == 200
    entry2 = ledger.get("sleeper:L1:2026", "w-mine")
    assert entry2.recorded_at == entry.recorded_at
