from ffdo.api import app as app_mod
from ffdo.api.app import (
    _DEFAULT_DRAFT_ID, _DEFAULT_LEAGUE_ID, _TTLCache, _active_only,
    _draft_id, _league_id, _roster_id, create_app,
)
from ffdo.api.session import SessionStore
from ffdo.domain.models import PlayerProfile, Session
from fastapi.testclient import TestClient


def _session(**overrides):
    base = dict(
        username="tester", user_id="U1", league_id="session-league",
        draft_id="session-draft", roster_id=5, league_name="Test League",
        season=2026, num_teams=12, budget=200,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                          "BN", "BN", "BN", "BN", "BN"),
        scoring_settings={"rec": 0.5}, draft_type="auction",
        draft_status="pre_draft", rounds=13, is_mock=False,
        connected_at="2026-08-22T00:00:00+00:00",
    )
    return Session(**{**base, **overrides})


class _FakeSleeperClient:
    """Stands in for ffdo.ingest.client.SleeperClient so tests never make a
    real network call. Returns an empty list for any projections URL (that
    parser requires a list) and an empty dict otherwise."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_json(self, url: str):
        return [] if "/projections/" in url else {}

    def close(self) -> None:
        pass


def test_has_value_is_false_before_the_first_load():
    cache = _TTLCache(ttl_seconds=10)
    assert cache.has_value() is False


def test_has_value_is_true_after_a_load_and_does_not_trigger_a_fetch():
    cache = _TTLCache(ttl_seconds=10)
    calls = []
    cache.get(lambda: calls.append(1) or "value")
    assert cache.has_value() is True
    assert cache.has_value() is True
    assert calls == [1], "has_value() must not call the loader"


def test_season_keyed_projections_caches_do_not_mix_seasons():
    """Mirrors `ffdo.api.app.create_app()`'s `_projections_cache_for()`
    pattern directly: `projections_cache` used to be a single shared
    `_TTLCache`, so connecting League A (season 2025) then League B (season
    2026) within the 1h TTL would silently serve 2025's cached payload for a
    2026 board. A dict of `_TTLCache` instances keyed by season, created
    lazily, is what fixes that -- proven here the same way the plain
    `_TTLCache` tests above prove TTL behavior: an injectable clock and a
    `calls` list, this time one pair per season."""
    caches: dict[int, _TTLCache] = {}

    def cache_for(season):
        return caches.setdefault(season, _TTLCache(ttl_seconds=3600))

    calls_2025 = []
    calls_2026 = []

    value_2025 = cache_for(2025).get(lambda: calls_2025.append(1) or "proj-2025")
    value_2026 = cache_for(2026).get(lambda: calls_2026.append(1) or "proj-2026")

    assert value_2025 == "proj-2025"
    assert value_2026 == "proj-2026"
    assert len(calls_2025) == 1
    assert len(calls_2026) == 1

    # Re-fetching season 2025 within the TTL must not re-run 2026's loader
    # (or vice versa) and must not return the other season's cached value --
    # this is exactly the cross-season contamination the single shared cache
    # used to cause.
    again_2025 = cache_for(2025).get(lambda: calls_2025.append(1) or "SHOULD-NOT-RUN")
    assert again_2025 == "proj-2025"
    assert len(calls_2025) == 1
    assert len(calls_2026) == 1


def test_league_id_prefers_the_connected_session_over_env_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("FFDO_LEAGUE_ID", "env-league")
    store = SessionStore(tmp_path / "session.json")
    store.save(_session())
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    assert _league_id() == "session-league"


def test_draft_id_prefers_the_connected_session_over_env_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("FFDO_DRAFT_ID", "env-draft")
    store = SessionStore(tmp_path / "session.json")
    store.save(_session())
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    assert _draft_id() == "session-draft"


def test_roster_id_prefers_the_connected_session_over_env_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("FFDO_ROSTER_ID", "999")
    store = SessionStore(tmp_path / "session.json")
    store.save(_session())
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    assert _roster_id() == 5


def test_ids_fall_back_to_env_vars_when_no_session_is_connected(monkeypatch):
    # No explicit store setup needed -- the autouse `_isolated_session_store`
    # fixture in conftest.py already points `_SESSION_STORE` at a fresh,
    # empty store, so there's nothing connected here by construction.
    monkeypatch.delenv("FFDO_LEAGUE_ID", raising=False)

    assert _league_id() == _DEFAULT_LEAGUE_ID


def test_connect_endpoint_returns_400_for_a_connect_error(monkeypatch):
    from ffdo.ingest import connect as connect_mod

    def raise_connect_error(sleeper, league_id, username):
        raise connect_mod.ConnectError("League not found")

    monkeypatch.setattr("ffdo.ingest.connect.resolve", raise_connect_error)

    client = TestClient(create_app())
    res = client.post("/api/connect", json={"league_id": "bad", "username": "tester"})

    assert res.status_code == 400
    assert res.json()["detail"] == "League not found"


def test_connect_endpoint_rejects_a_blank_league_id_or_username():
    client = TestClient(create_app())

    res = client.post("/api/connect", json={"league_id": "  ", "username": "tester"})

    assert res.status_code == 400


def test_connect_endpoint_rejects_both_league_id_and_draft_id_together():
    client = TestClient(create_app())

    res = client.post("/api/connect", json={
        "league_id": "L1", "draft_id": "D1", "username": "tester"})

    assert res.status_code == 400


def test_connect_endpoint_rejects_neither_league_id_nor_draft_id():
    client = TestClient(create_app())

    res = client.post("/api/connect", json={"username": "tester"})

    assert res.status_code == 400


def test_connect_endpoint_routes_a_draft_id_payload_to_resolve_mock(monkeypatch):
    fake_mock_session = _session(
        league_id="", draft_id="1397145756879605760", is_mock=True)
    captured = {}

    def fake_resolve_mock(sleeper, draft_id, username):
        captured["draft_id"] = draft_id
        return fake_mock_session

    monkeypatch.setattr("ffdo.ingest.connect.resolve_mock", fake_resolve_mock)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/connect", json={
        "draft_id": "https://sleeper.app/draft/nfl/1397145756879605760",
        "username": "schroedes"})

    assert res.status_code == 200
    assert res.json()["is_mock"] is True
    # The share URL's trailing numeric ID must reach resolve_mock() bare,
    # not the whole pasted URL.
    assert captured["draft_id"] == "1397145756879605760"


def test_connect_endpoint_accepts_a_bare_draft_id_without_a_url(monkeypatch):
    captured = {}

    def fake_resolve_mock(sleeper, draft_id, username):
        captured["draft_id"] = draft_id
        return _session(league_id="", is_mock=True)

    monkeypatch.setattr("ffdo.ingest.connect.resolve_mock", fake_resolve_mock)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    client.post("/api/connect", json={"draft_id": "1397145756879605760",
                                      "username": "schroedes"})

    assert captured["draft_id"] == "1397145756879605760"


def test_league_id_is_empty_string_for_a_connected_mock_session(monkeypatch, tmp_path):
    """get_board()'s mock-vs-real branch is driven entirely by whether
    _league_id() returns a falsy value -- this is the one fact that whole
    dispatch depends on, so it gets its own direct test."""
    store = SessionStore(tmp_path / "session.json")
    store.save(_session(league_id="", is_mock=True))
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    assert _league_id() == ""


def test_connect_endpoint_saves_the_session_and_returns_it(monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "session.json")
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    fake_session = _session(league_id="L9")
    monkeypatch.setattr("ffdo.ingest.connect.resolve",
                        lambda sleeper, league_id, username: fake_session)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/connect", json={"league_id": "L9", "username": "tester"})

    assert res.status_code == 200
    assert res.json()["league_id"] == "L9"
    assert store.get() == fake_session


def test_session_endpoint_returns_null_when_nothing_is_connected():
    client = TestClient(create_app())

    res = client.get("/api/session")
    assert res.json() is None


def test_session_endpoint_returns_the_connected_session(monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "session.json")
    store.save(_session())
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)
    client = TestClient(create_app())

    res = client.get("/api/session")
    assert res.json()["league_id"] == "session-league"


def test_readiness_endpoint_reports_pending_before_anything_is_connected():
    client = TestClient(create_app())

    res = client.get("/api/readiness")
    assert res.json() == {"league_draft": "pending", "players": "pending", "projections": "pending"}


def test_readiness_endpoint_reports_synced_after_connecting(monkeypatch):
    monkeypatch.setattr("ffdo.ingest.connect.resolve",
                        lambda sleeper, league_id, username: _session())
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    client.post("/api/connect", json={"league_id": "L1", "username": "tester"})

    res = client.get("/api/readiness")
    body = res.json()
    assert body == {"league_draft": "synced", "players": "synced", "projections": "synced"}


def test_readiness_reports_pending_for_projections_of_a_new_season_even_when_a_different_season_is_already_synced(monkeypatch):
    """The crux of the unkeyed-cache bug: connecting League A (season 2025)
    warms its projections cache. Switching to League B (season 2026) -- here
    simulated by writing directly to `_SESSION_STORE`, standing in for the
    instant after `/api/connect` returns but before the new season's
    background warm task has run -- must report `projections: pending` for
    2026, not spuriously `synced` off of 2025's stale cache entry. A
    misleading `synced` here would be actively wrong, not just wasted work."""
    monkeypatch.setattr("ffdo.ingest.connect.resolve",
                        lambda sleeper, league_id, username: _session(season=2025))
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    client.post("/api/connect", json={"league_id": "L1", "username": "tester"})
    assert client.get("/api/readiness").json()["projections"] == "synced"

    app_mod._SESSION_STORE.save(_session(season=2026, league_id="L2"))

    body = client.get("/api/readiness").json()
    assert body["projections"] == "pending"


def test_ttlcache_serves_cached_value_within_ttl_without_refetching():
    fake_now = [0.0]
    cache = _TTLCache(ttl_seconds=10)
    calls = []

    def loader():
        calls.append(len(calls))
        return f"value-{len(calls)}"

    cache._now = lambda: fake_now[0]

    first = cache.get(loader)
    assert first == "value-1"
    assert len(calls) == 1

    fake_now[0] = 5.0
    second = cache.get(loader)
    assert second == "value-1"
    assert len(calls) == 1, "loader must not be called again within the TTL"


def test_ttlcache_refetches_after_ttl_elapses():
    fake_now = [0.0]
    cache = _TTLCache(ttl_seconds=10)
    calls = []

    def loader():
        calls.append(len(calls))
        return f"value-{len(calls)}"

    cache._now = lambda: fake_now[0]

    assert cache.get(loader) == "value-1"
    assert len(calls) == 1

    fake_now[0] = 10.1
    second = cache.get(loader)
    assert second == "value-2"
    assert len(calls) == 2, "loader must be called again once the TTL elapses"


def test_ttlcache_default_clock_is_time_monotonic():
    """Sanity check that the injectable clock defaults to the real one."""
    import time

    cache = _TTLCache(ttl_seconds=10)
    assert cache._now is time.monotonic


def test_league_and_draft_ids_default_to_the_pinned_auction_league(monkeypatch):
    """With no env override, the app must keep pointing at the user's real
    2026 auction league/draft -- the auction path must keep working with
    zero config."""
    monkeypatch.delenv("FFDO_LEAGUE_ID", raising=False)
    monkeypatch.delenv("FFDO_DRAFT_ID", raising=False)
    assert _league_id() == _DEFAULT_LEAGUE_ID == "1315881559957458944"
    assert _draft_id() == _DEFAULT_DRAFT_ID == "1315881559965835264"


def test_league_and_draft_ids_are_read_fresh_from_env_on_each_call(monkeypatch):
    """The board is unreachable for snake leagues unless the league/draft id
    can be overridden. Reading `os.environ` fresh inside these functions
    (rather than freezing module-level constants at import time) is what
    makes an env var set after the process starts actually take effect --
    verified here by flipping the env var between two calls."""
    monkeypatch.delenv("FFDO_LEAGUE_ID", raising=False)
    monkeypatch.delenv("FFDO_DRAFT_ID", raising=False)
    assert _league_id() == _DEFAULT_LEAGUE_ID
    assert _draft_id() == _DEFAULT_DRAFT_ID

    monkeypatch.setenv("FFDO_LEAGUE_ID", "999888777")
    monkeypatch.setenv("FFDO_DRAFT_ID", "111222333")
    assert _league_id() == "999888777"
    assert _draft_id() == "111222333"
    assert _league_id() != _DEFAULT_LEAGUE_ID


def test_roster_id_is_none_when_unset(monkeypatch):
    monkeypatch.delenv("FFDO_ROSTER_ID", raising=False)
    assert _roster_id() is None


def test_roster_id_reads_the_env_override(monkeypatch):
    monkeypatch.setenv("FFDO_ROSTER_ID", "7")
    assert _roster_id() == 7


def test_roster_id_is_none_on_a_non_numeric_override(monkeypatch):
    monkeypatch.setenv("FFDO_ROSTER_ID", "not-a-number")
    assert _roster_id() is None


def _profile(pid, active):
    return PlayerProfile(player_id=pid, first_name="P", last_name=pid,
                         position="RB", team="X", age=30, years_exp=10,
                         injury_status=None, active=active)


def test_active_only_drops_inactive_players():
    """`PlayerProfile.active` was parsed but never used as a filter --
    a retired player (e.g. Cam Newton) could reach the valuation pool with
    a deeply negative VOR instead of not appearing at all."""
    points = {"active_p": 150.0, "retired_p": 5.0}
    profiles = {"active_p": _profile("active_p", active=True),
                "retired_p": _profile("retired_p", active=False)}
    out = _active_only(points, profiles)
    assert out == {"active_p": 150.0}
