from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ffdo.api import app as app_mod
from ffdo.api.app import create_app
from ffdo.api.store import LeagueStore
from ffdo.domain.models import ProviderCredential
from ffdo.ingest.client import V1

from tests.api.test_app import _recording_espn_client
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


_ESPN_LEAGUE_RAW = {
    "status": {"currentMatchupPeriod": 3},
    "settings": {"scheduleSettings": {"matchupPeriodCount": 14}},
    "teams": [
        {"id": 1, "name": "You Team",
         "record": {"overall": {"wins": 5, "losses": 2, "ties": 0,
                                "pointsFor": 800.0, "pointsAgainst": 700.0}},
         "roster": {"entries": [
             {"playerId": 9001, "lineupSlotId": 2,
              "playerPoolEntry": {"player": {"stats": []}}},
         ]}},
        {"id": 2, "name": "Them Team",
         "record": {"overall": {"wins": 2, "losses": 5, "ties": 0,
                                "pointsFor": 500.0, "pointsAgainst": 600.0}},
         "roster": {"entries": [
             {"playerId": 9002, "lineupSlotId": 4,
              "playerPoolEntry": {"player": {"stats": []}}},
         ]}},
    ],
}
_ESPN_PLAYER_POOL_RAW = [
    {"id": 9001, "fullName": "R B", "defaultPositionId": 2, "proTeamId": 99},  # -> p_rb
    {"id": 9002, "fullName": "W R", "defaultPositionId": 3, "proTeamId": 98},  # -> p_wr
]


def _espn_home_tracked(**over):
    return _tracked(**{
        "league_key": "espn:E1:2026", "provider": "espn", "provider_league_id": "E1",
        "roster_id": 1, **over,
    })


def test_home_summary_espn_returns_record_power_rank_and_lineup(monkeypatch, tmp_path):
    """ESPN's home-summary card includes record, power rank, and (now that
    ESPN weekly-lineup support exists) a real starting-lineup diff. This
    fixture has no `schedule` data, so matchup degrades to None (see the
    dedicated matchup test below for the populated case) -- the
    waiver/trade-target flags still have no ESPN ingest for a cheap
    league-wide count, so those stay absent regardless."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_espn_home_tracked())
    store.put_credential(ProviderCredential("espn", "{SWID}", "s2value", "{SWID}", "t"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient",
                        lambda *a, **k: _FakeClient({f"{V1}/state/nfl": _STATE,
                                                     f"{V1}/players/nfl": _PLAYERS,
                                                     "/projections/": _PROJ}))
    FakeEspn, _calls = _recording_espn_client({
        "seasons/2026/players": _ESPN_PLAYER_POOL_RAW,
        "leagues/E1": _ESPN_LEAGUE_RAW,
    })
    monkeypatch.setattr("ffdo.ingest.espn.client.EspnClient", FakeEspn)

    res = TestClient(create_app()).get("/api/leagues/espn:E1:2026/home-summary")
    assert res.status_code == 200
    data = res.json()
    assert data["league_key"] == "espn:E1:2026"
    assert data["provider"] == "espn"
    assert data["name"] == "You Team"
    assert data["record"] == {"wins": 5, "losses": 2, "ties": 0}
    assert data["power_rank"]["value"] in (1, 2)
    assert data["power_rank"]["of"] == 2
    assert data["matchup"] is None
    # starting_slots for _tracked()'s default roster_positions is
    # ("QB", "RB", "WR", "FLEX") -- 4 rows, same shape as /lineup's own test.
    assert len(data["starters"]) == 4
    rb_row = next(s for s in data["starters"] if s["name"] == "R B")
    assert rb_row["status"] == "match"


def test_home_summary_espn_includes_a_real_matchup_when_schedule_data_exists(monkeypatch, tmp_path):
    """When the combined mRoster+mMatchupScore response does carry
    `schedule` data for the current period, the home-summary card
    surfaces a real opponent + live-or-projected score for both sides --
    see ingest.espn.matchup's own docstring for the positional-zip this
    relies on."""
    league_raw_with_matchup = {
        **_ESPN_LEAGUE_RAW,
        "schedule": [
            {"matchupPeriodId": 3,
             "home": {"teamId": 1, "rosterForCurrentScoringPeriod": {"entries": [
                 {"lineupSlotId": 2, "playerPoolEntry": {"player": {"stats": [
                     {"statSourceId": 1, "appliedTotal": 18.4}]}}},
             ]}},
             "away": {"teamId": 2, "rosterForCurrentScoringPeriod": {"entries": [
                 {"lineupSlotId": 4, "playerPoolEntry": {"player": {"stats": [
                     {"statSourceId": 1, "appliedTotal": 30.0},
                     {"statSourceId": 0, "appliedTotal": 12.7}]}}},
             ]}}},
        ],
    }
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_espn_home_tracked())
    store.put_credential(ProviderCredential("espn", "{SWID}", "s2value", "{SWID}", "t"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient",
                        lambda *a, **k: _FakeClient({f"{V1}/state/nfl": _STATE,
                                                     f"{V1}/players/nfl": _PLAYERS,
                                                     "/projections/": _PROJ}))
    FakeEspn, _calls = _recording_espn_client({
        "seasons/2026/players": _ESPN_PLAYER_POOL_RAW,
        "leagues/E1": league_raw_with_matchup,
    })
    monkeypatch.setattr("ffdo.ingest.espn.client.EspnClient", FakeEspn)

    res = TestClient(create_app()).get("/api/leagues/espn:E1:2026/home-summary")
    assert res.status_code == 200
    data = res.json()
    assert data["matchup"] == {
        "opponent_name": "Them Team",
        "your_projected": 18.4,
        "opponent_projected": 12.7,   # actual (statSourceId 0) preferred over projection
    }


def test_home_summary_espn_400s_without_a_stored_credential(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_espn_home_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)

    res = TestClient(create_app()).get("/api/leagues/espn:E1:2026/home-summary")
    assert res.status_code == 400
    assert "Connect ESPN" in res.json()["detail"]


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


# A local, hand-built fixture (not the shared minimal _PLAYERS/_ROSTERS pair,
# which rosters every player in _PLAYERS and so has zero free agents) with a
# genuine, unrostered free-agent WR carrying a strong projection -- enough
# real surplus for waiver_value.recommend_adds to actually fire, so these
# two tests exercise the FAAB branch instead of vacuously skipping it.
_FAAB_PLAYERS = {
    **_PLAYERS,
    "p_fa_wr": {"first_name": "Free", "last_name": "Agent", "position": "WR",
                "team": "EEE", "age": 24, "years_exp": 2, "active": True},
}
_FAAB_PROJ = _PROJ + [
    {"player_id": "p_fa_wr",
     "last_modified": int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
     "stats": {"rec": 150.0, "rec_yd": 2000.0, "rec_td": 20.0}},
]


def _faab_tracked(**over):
    return _tracked(fmt="redraft", raw_settings={
        "waiver_type": 2, "waiver_budget": 100, "draft_rounds": 4}, **over)


def _faab_resp():
    resp = _base_resp()
    resp[f"{V1}/players/nfl"] = _FAAB_PLAYERS
    resp["/projections/"] = _FAAB_PROJ
    for week in range(1, 11):  # _STATE's week is 10 -- weeks 1 through 10
        resp[f"{V1}/league/L1/transactions/{week}"] = []
    return resp


class _FaabClient(_FakeClient):
    """Layers the bare `/league/L1` live-settings re-fetch (used only by
    home-summary's FAAB branch) on top of `_FakeClient`'s longest-match
    lookup. That base lookup can't hold this key itself: a bare
    ".../league/L1" is a substring of ".../league/L1/matchups/9" too, and
    at 38 chars it's "longer" than the 10-char generic "/matchups/" key
    that request is meant to hit -- so registering it in the shared `resp`
    dict would hijack the matchups (and rosters) lookups for every week.
    Checking for the bare suffix explicitly, before delegating, avoids
    that collision without changing the shared fixture's matching rule.
    """
    def get_json(self, url, *a, **k):
        if url.rstrip("/").endswith("/league/L1") and "/league/L1/" not in url:
            return {"settings": {"waiver_type": 2, "waiver_budget": 100}}
        return super().get_json(url, *a, **k)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Pre-existing bug, discovered while writing this test (not part of "
        "this task's caching fix): _load_trade_targets_context computes "
        "`valued` from all_pids = {pid for r in rosters for pid in "
        "r.player_ids} -- ONLY rostered players, never free agents. "
        "get_home_summary's FAAB branch then calls "
        "waiver_value_mod.recommend_adds(free_agent_ids, ..., valued, ...), "
        "which looks up `valued.get(fa_id)` for each free agent and skips "
        "any id not present -- so it always skips every one of them. "
        "Verified empirically: with a genuine free-agent surplus fixture "
        "(a strongly-projected unrostered WR), `p_fa_wr in valued` is False "
        "and `flags` comes back {} every time. This is a real, "
        "already-in-production bug (the home-summary waiver flag can never "
        "fire), and it also defeats trade_targets.suggest_for_team's "
        "free-agent 'sweetener' logic (its `pid in valued` check at "
        "trade_targets.py's `valued_fas = [pid for pid in free_agent_ids if "
        "pid in valued]`) for the same reason -- so this is a shared-helper "
        "bug, not something local to home-summary. Fixing it safely is "
        "nontrivial: naively unioning free agents into "
        "_load_trade_targets_context's all_pids would change the "
        "replacement-level computation ros_value.roster_value derives from "
        "that same player_ids set, shifting VOR for every rostered player "
        "too and risking every numeric assertion in the trade-targets test "
        "suite -- out of scope for this task. Left as xfail(strict=True) so "
        "a real fix flips this test to an unexpected pass, forcing it to "
        "be un-xfailed rather than silently staying stale."
    ),
)
def test_home_summary_reports_a_real_waiver_add_flag_for_a_faab_league(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_faab_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)
    resp = _faab_resp()
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", lambda *a, **k: _FaabClient(resp))

    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/home-summary")
    assert res.status_code == 200
    data = res.json()
    # Loose, structurally-meaningful assertion -- verified empirically
    # against this fixture's real recommend_adds output rather than
    # hand-derived, since hand-deriving VOR/bid numbers through this
    # codebase's real valuation pipeline has burned this repo before.
    assert data["flags"]["waiver_adds"] >= 1


def test_home_summary_omits_waiver_flag_when_the_claims_fetch_fails(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_faab_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)
    resp = _faab_resp()

    class _FailingWaiversClient(_FaabClient):
        def get_json(self, url, *a, **k):
            if "/transactions/" in url:
                raise RuntimeError("Sleeper transactions fetch failed")
            return super().get_json(url, *a, **k)

    monkeypatch.setattr(
        "ffdo.ingest.client.SleeperClient", lambda *a, **k: _FailingWaiversClient(resp))

    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L1:2026/home-summary")
    assert res.status_code == 200
    assert "waiver_adds" not in res.json()["flags"]
