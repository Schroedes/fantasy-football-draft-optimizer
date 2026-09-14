from datetime import datetime, timezone

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


def test_trade_suggestions_is_sleeper_only():
    from ffdo.domain.models import TrackedLeague

    app_mod._STORE.upsert(_tracked(
        league_key="espn:E2:2026", provider="espn", provider_league_id="E2"))
    body = {"partner_roster_id": 2, "side_a": {"player_ids": [], "picks": []},
            "side_b": {"player_ids": [], "picks": []}}
    res = TestClient(create_app()).post("/api/leagues/espn:E2:2026/trade/suggestions", json=body)
    assert res.status_code == 400


def test_trade_targets_is_sleeper_only():
    from ffdo.domain.models import TrackedLeague

    app_mod._STORE.upsert(_tracked(
        league_key="espn:E3:2026", provider="espn", provider_league_id="E3"))
    res = TestClient(create_app()).get("/api/leagues/espn:E3:2026/trade-targets")
    assert res.status_code == 400


def test_trade_suggestions_returns_empty_list_when_no_team_has_surplus_depth(monkeypatch, tmp_path):
    # The shared _ROSTERS fixture rosters exactly 1 player per position per
    # team (see test_season_endpoint.py) -- no position anywhere clears the
    # >=2-rostered surplus gate, so suggest_for_team deterministically
    # returns []. This is a real, meaningful assertion (confirms the
    # >=2-rostered gate works end-to-end through the real API and
    # valuation pipeline), not a placeholder.
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
            "side_a": {"player_ids": [], "picks": []},
            "side_b": {"player_ids": [], "picks": []}}
    res = client.post("/api/leagues/sleeper:L1:2026/trade/suggestions", json=body)
    assert res.status_code == 200
    assert res.json() == {"suggestions": []}

    res2 = client.get("/api/leagues/sleeper:L1:2026/trade-targets")
    assert res2.status_code == 200
    assert res2.json() == {"suggestions": []}


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


def test_trade_evaluate_includes_needs_before_and_after_for_both_sides(monkeypatch, tmp_path):
    """Uses a fully LOCAL fixture (not the shared _ROSTERS/_PROJ/_PLAYERS
    from test_season_endpoint) because that shared fixture's minimal
    2-RB-total universe collapses replacement level onto the second RB
    itself, giving it VOR == 0.0 -- indistinguishable from "zero RBs" once
    power_ranking sums started VOR, so no rank change is actually
    observable there (confirmed by instrumenting roster_value() directly).

    Here the league has THREE RBs across two teams (n_rb1 starting for
    "you", n_rb2 starting for "partner", n_rb3 a clearly-weaker bench RB
    on partner's roster) against only 2 dedicated RB starting slots
    (roster_positions has no FLEX), so replacement_levels() lands on
    n_rb3 -- leaving both starters, n_rb1 and n_rb2, with real, distinct,
    positive VOR rather than one of them defining the baseline. This was
    verified empirically by temporarily instrumenting the endpoint to
    print each valued player's VOR for this exact fixture (see the task
    report for the observed numbers) before locking in the assertions
    below: n_rb1 and n_rb2 both score positive, non-zero, unequal VOR,
    so giving away your only RB for nothing in return (side_a=[n_rb1],
    side_b=[]) drops your post-trade starting RB value to 0 while
    partner's frozen real value stays positive -- a genuine, measured
    rank flip from 1 to 2, not a tie broken by roster_id."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft", roster_positions=("QB", "RB", "WR", "BN", "BN")))
    monkeypatch.setattr(app_mod, "_STORE", store)

    ts = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000)
    players = {
        "n_qb1": {"first_name": "N", "last_name": "QB1", "position": "QB", "team": "AAA",
                  "age": 27, "years_exp": 5, "active": True},
        "n_rb1": {"first_name": "N", "last_name": "RB1", "position": "RB", "team": "BBB",
                  "age": 24, "years_exp": 3, "active": True},
        "n_wr1": {"first_name": "N", "last_name": "WR1", "position": "WR", "team": "CCC",
                  "age": 26, "years_exp": 4, "active": True},
        "n_qb2": {"first_name": "N", "last_name": "QB2", "position": "QB", "team": "DDD",
                  "age": 28, "years_exp": 6, "active": True},
        "n_rb2": {"first_name": "N", "last_name": "RB2", "position": "RB", "team": "EEE",
                  "age": 25, "years_exp": 3, "active": True},
        "n_wr2": {"first_name": "N", "last_name": "WR2", "position": "WR", "team": "FFF",
                  "age": 27, "years_exp": 5, "active": True},
        "n_rb3": {"first_name": "N", "last_name": "RB3", "position": "RB", "team": "GGG",
                  "age": 30, "years_exp": 9, "active": True},
    }
    proj = [
        {"player_id": "n_qb1", "last_modified": ts, "stats": {"pass_yd": 4200.0, "pass_td": 30.0}},
        {"player_id": "n_rb1", "last_modified": ts,
         "stats": {"rush_yd": 1300.0, "rush_td": 12.0, "rec": 45.0, "rec_yd": 350.0}},
        {"player_id": "n_wr1", "last_modified": ts,
         "stats": {"rec": 90.0, "rec_yd": 1200.0, "rec_td": 8.0}},
        {"player_id": "n_qb2", "last_modified": ts, "stats": {"pass_yd": 3900.0, "pass_td": 26.0}},
        {"player_id": "n_rb2", "last_modified": ts,
         "stats": {"rush_yd": 900.0, "rush_td": 7.0, "rec": 25.0, "rec_yd": 200.0}},
        {"player_id": "n_wr2", "last_modified": ts,
         "stats": {"rec": 70.0, "rec_yd": 950.0, "rec_td": 6.0}},
        {"player_id": "n_rb3", "last_modified": ts,
         "stats": {"rush_yd": 150.0, "rush_td": 0.0, "rec": 3.0, "rec_yd": 20.0}},
    ]
    rosters = [
        {"roster_id": 1, "owner_id": "U1", "players": ["n_qb1", "n_rb1", "n_wr1"],
         "starters": ["n_qb1", "n_rb1", "n_wr1"],
         "settings": {"wins": 6, "losses": 3, "fpts": 1284, "fpts_against": 1244}},
        {"roster_id": 2, "owner_id": "U2", "players": ["n_qb2", "n_rb2", "n_wr2", "n_rb3"],
         "starters": ["n_qb2", "n_rb2", "n_wr2"],
         "settings": {"wins": 3, "losses": 6, "fpts": 1100, "fpts_against": 1250}},
    ]
    users = [{"user_id": "U1", "display_name": "You"}, {"user_id": "U2", "display_name": "Them"}]

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": rosters,
        f"{V1}/league/L1/users": users, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": players, "/projections/": proj, "/matchups/": [],
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
            "side_a": {"player_ids": ["n_rb1"], "picks": []},
            "side_b": {"player_ids": [], "picks": []}}
    res = client.post("/api/leagues/sleeper:L1:2026/trade/evaluate", json=body)
    assert res.status_code == 200
    data = res.json()

    assert set(data["needs_before"]) == {"you", "partner"}
    assert set(data["needs_after"]) == {"you", "partner"}
    for side in ("you", "partner"):
        for pos in ("QB", "RB", "WR", "TE"):
            assert pos in data["needs_before"][side]
            assert "rank" in data["needs_before"][side][pos]
            assert "severity" in data["needs_before"][side][pos]
            assert pos in data["needs_after"][side]

    assert data["needs_before"]["you"]["RB"]["rank"] == 1
    assert data["needs_after"]["you"]["RB"]["rank"] == 2


def test_trade_suggestions_returns_real_non_empty_suggestion_with_full_shape(monkeypatch, tmp_path):
    """The only other suggestions coverage
    (test_trade_suggestions_returns_empty_list_when_no_team_has_surplus_depth)
    exercises just the {"suggestions": []} branch -- this drives a REAL,
    non-empty suggestion through the real endpoint + real valuation
    pipeline (not a mock) to lock down the target_players/offer_players/
    offer_picks/why JSON shape Task 3/4's frontends will depend on.

    Uses a fully LOCAL fixture (same reasoning as
    test_trade_evaluate_includes_needs_before_and_after_for_both_sides
    above: the shared minimal _ROSTERS fixture cannot produce a surplus,
    since it rosters exactly 1 player per position per team). Built as a
    two-way-fit mirror, the same shape as Task 1's roster_needs unit-test
    fixtures: "you" (roster 1) has clear WR surplus (3 WRs, with y_wr1/
    y_wr2 clearly the stronger two and y_wr3 a weak bench arm) and a
    clear RB need (a single, weak starting RB); "partner" (roster 2) is
    the mirror image -- RB surplus (3 RBs, p_rb1/p_rb2 clearly the
    stronger two) and WR need (a single, weak starting WR). roster_positions
    has no FLEX (dedicated QB/RB/WR slots only), so surplus/need reads
    cleanly off roster_needs.position_needs's weakness+thinness blend in
    this 2-team league, matching suggest_for_team's own >=2-rostered
    surplus gate and its Fine-vs-Needy severity split.

    This was verified by actually running both endpoints against this
    exact fixture via TestClient before writing any assertion below (see
    the task report for the real, pipeline-computed JSON captured then):
    both endpoints return one suggestion, targeting partner's best RB
    (p_rb1) and offering "you"'s best WR (y_wr1), with a small positive
    net_value_gain -- confirming the >=2-rostered surplus/need gate AND
    the full non-empty response shape work end-to-end through the real
    valuation pipeline. Assertions below are deliberately structural
    (keys present, list shapes, net_value_gain > 0 -- an invariant of
    suggest_for_team's own filter, not a hand-derived pipeline number),
    never exact VOR/value literals, per this fixture's own burned-twice
    history of hand-deriving values against the real (non-raw-stat-sum)
    valuation pipeline and getting them wrong."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="redraft", roster_positions=("QB", "RB", "WR", "BN", "BN", "BN")))
    monkeypatch.setattr(app_mod, "_STORE", store)

    ts = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000)
    players = {
        "y_qb1": {"first_name": "Y", "last_name": "QB1", "position": "QB", "team": "AAA",
                  "age": 27, "years_exp": 5, "active": True},
        "y_wr1": {"first_name": "Y", "last_name": "WR1", "position": "WR", "team": "BBB",
                  "age": 25, "years_exp": 3, "active": True},
        "y_wr2": {"first_name": "Y", "last_name": "WR2", "position": "WR", "team": "CCC",
                  "age": 26, "years_exp": 4, "active": True},
        "y_wr3": {"first_name": "Y", "last_name": "WR3", "position": "WR", "team": "DDD",
                  "age": 30, "years_exp": 8, "active": True},
        "y_rb1": {"first_name": "Y", "last_name": "RB1", "position": "RB", "team": "EEE",
                  "age": 29, "years_exp": 7, "active": True},
        "p_qb1": {"first_name": "P", "last_name": "QB1", "position": "QB", "team": "FFF",
                  "age": 28, "years_exp": 6, "active": True},
        "p_rb1": {"first_name": "P", "last_name": "RB1", "position": "RB", "team": "GGG",
                  "age": 24, "years_exp": 2, "active": True},
        "p_rb2": {"first_name": "P", "last_name": "RB2", "position": "RB", "team": "HHH",
                  "age": 25, "years_exp": 3, "active": True},
        "p_rb3": {"first_name": "P", "last_name": "RB3", "position": "RB", "team": "III",
                  "age": 31, "years_exp": 9, "active": True},
        "p_wr1": {"first_name": "P", "last_name": "WR1", "position": "WR", "team": "JJJ",
                  "age": 30, "years_exp": 8, "active": True},
    }
    proj = [
        {"player_id": "y_qb1", "last_modified": ts, "stats": {"pass_yd": 4000.0, "pass_td": 28.0}},
        {"player_id": "y_wr1", "last_modified": ts,
         "stats": {"rec": 95.0, "rec_yd": 1300.0, "rec_td": 10.0}},
        {"player_id": "y_wr2", "last_modified": ts,
         "stats": {"rec": 85.0, "rec_yd": 1150.0, "rec_td": 8.0}},
        {"player_id": "y_wr3", "last_modified": ts,
         "stats": {"rec": 20.0, "rec_yd": 250.0, "rec_td": 1.0}},
        {"player_id": "y_rb1", "last_modified": ts,
         "stats": {"rush_yd": 300.0, "rush_td": 1.0, "rec": 10.0, "rec_yd": 60.0}},
        {"player_id": "p_qb1", "last_modified": ts, "stats": {"pass_yd": 3900.0, "pass_td": 27.0}},
        {"player_id": "p_rb1", "last_modified": ts,
         "stats": {"rush_yd": 1400.0, "rush_td": 13.0, "rec": 50.0, "rec_yd": 400.0}},
        {"player_id": "p_rb2", "last_modified": ts,
         "stats": {"rush_yd": 1200.0, "rush_td": 10.0, "rec": 40.0, "rec_yd": 300.0}},
        {"player_id": "p_rb3", "last_modified": ts,
         "stats": {"rush_yd": 150.0, "rush_td": 0.0, "rec": 5.0, "rec_yd": 30.0}},
        {"player_id": "p_wr1", "last_modified": ts,
         "stats": {"rec": 15.0, "rec_yd": 200.0, "rec_td": 1.0}},
    ]
    rosters = [
        {"roster_id": 1, "owner_id": "U1", "players": ["y_qb1", "y_wr1", "y_wr2", "y_wr3", "y_rb1"],
         "starters": ["y_qb1", "y_wr1", "y_rb1"],
         "settings": {"wins": 6, "losses": 3, "fpts": 1284, "fpts_against": 1244}},
        {"roster_id": 2, "owner_id": "U2", "players": ["p_qb1", "p_rb1", "p_rb2", "p_rb3", "p_wr1"],
         "starters": ["p_qb1", "p_rb1", "p_wr1"],
         "settings": {"wins": 3, "losses": 6, "fpts": 1100, "fpts_against": 1250}},
    ]
    users = [{"user_id": "U1", "display_name": "You"}, {"user_id": "U2", "display_name": "Them"}]

    resp = {
        f"{V1}/state/nfl": _STATE, f"{V1}/league/L1/rosters": rosters,
        f"{V1}/league/L1/users": users, f"{V1}/league/L1/traded_picks": [],
        f"{V1}/players/nfl": players, "/projections/": proj, "/matchups/": [],
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

    def _assert_suggestions_shape(suggestions):
        assert isinstance(suggestions, list)
        assert len(suggestions) >= 1
        for s in suggestions:
            assert isinstance(s["partner_roster_id"], int)
            assert isinstance(s["partner_team_name"], str) and s["partner_team_name"]

            for key in ("target_players", "offer_players"):
                players_list = s[key]
                assert isinstance(players_list, list) and len(players_list) >= 1
                for p in players_list:
                    assert set(("player_id", "name", "position", "value")) <= set(p)
                    assert isinstance(p["player_id"], str)
                    assert isinstance(p["name"], str)
                    assert isinstance(p["position"], str)
                    assert isinstance(p["value"], (int, float))

            assert isinstance(s["offer_picks"], list)
            for pick in s["offer_picks"]:
                assert set(("label", "season", "round", "original_roster_id")) <= set(pick)

            for key in ("target_value", "offer_value", "net_value_gain", "differential"):
                assert isinstance(s[key], (int, float))
            assert s["net_value_gain"] > 0

            assert isinstance(s["why"], str) and s["why"]

    body = {"partner_roster_id": 2,
            "side_a": {"player_ids": [], "picks": []},
            "side_b": {"player_ids": [], "picks": []}}
    res = client.post("/api/leagues/sleeper:L1:2026/trade/suggestions", json=body)
    assert res.status_code == 200
    post_suggestions = res.json()["suggestions"]
    _assert_suggestions_shape(post_suggestions)
    # This fixture's whole point is a real, concrete match: partner's best
    # RB for your best WR.
    assert post_suggestions[0]["target_players"][0]["player_id"] == "p_rb1"
    assert post_suggestions[0]["offer_players"][0]["player_id"] == "y_wr1"

    res2 = client.get("/api/leagues/sleeper:L1:2026/trade-targets")
    assert res2.status_code == 200
    _assert_suggestions_shape(res2.json()["suggestions"])
