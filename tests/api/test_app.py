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
        draft_status="pre_draft", connected_at="2026-08-22T00:00:00+00:00",
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


def test_ids_fall_back_to_env_vars_when_no_session_is_connected(monkeypatch, tmp_path):
    monkeypatch.delenv("FFDO_LEAGUE_ID", raising=False)
    monkeypatch.setattr(app_mod, "_SESSION_STORE", SessionStore(tmp_path / "session.json"))

    assert _league_id() == _DEFAULT_LEAGUE_ID


def test_connect_endpoint_returns_400_for_a_connect_error(monkeypatch, tmp_path):
    from ffdo.ingest import connect as connect_mod

    monkeypatch.setattr(app_mod, "_SESSION_STORE", SessionStore(tmp_path / "session.json"))

    def raise_connect_error(sleeper, league_id, username):
        raise connect_mod.ConnectError("League not found")

    monkeypatch.setattr("ffdo.ingest.connect.resolve", raise_connect_error)

    client = TestClient(create_app())
    res = client.post("/api/connect", json={"league_id": "bad", "username": "tester"})

    assert res.status_code == 400
    assert res.json()["detail"] == "League not found"


def test_connect_endpoint_rejects_a_blank_league_id_or_username(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "_SESSION_STORE", SessionStore(tmp_path / "session.json"))
    client = TestClient(create_app())

    res = client.post("/api/connect", json={"league_id": "  ", "username": "tester"})

    assert res.status_code == 400


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


def test_session_endpoint_returns_null_when_nothing_is_connected(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "_SESSION_STORE", SessionStore(tmp_path / "session.json"))
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


def test_readiness_endpoint_reports_pending_before_anything_is_connected(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "_SESSION_STORE", SessionStore(tmp_path / "session.json"))
    client = TestClient(create_app())

    res = client.get("/api/readiness")
    assert res.json() == {"league_draft": "pending", "players": "pending", "projections": "pending"}


def test_readiness_endpoint_reports_synced_after_connecting(monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "session.json")
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)
    monkeypatch.setattr("ffdo.ingest.connect.resolve",
                        lambda sleeper, league_id, username: _session())
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    client.post("/api/connect", json={"league_id": "L1", "username": "tester"})

    res = client.get("/api/readiness")
    body = res.json()
    assert body == {"league_draft": "synced", "players": "synced", "projections": "synced"}


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
