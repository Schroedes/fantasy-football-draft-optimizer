from ffdo.api.app import (
    _DEFAULT_DRAFT_ID, _DEFAULT_LEAGUE_ID, _TTLCache, _active_only,
    _draft_id, _league_id, _roster_id,
)
from ffdo.domain.models import PlayerProfile


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
