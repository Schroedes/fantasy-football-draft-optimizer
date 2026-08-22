from ffdo.api.app import SKILL_POSITIONS, _TTLCache


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


def test_skill_positions_excludes_non_startable_positions():
    """FB/CB have no roster slot in this league and no replacement level,
    so vor.compute() falls back to raw points as VOR for them -- they must
    be filtered out before scoring, not merely relying on Sleeper's
    position[] query param (which the live API ignores)."""
    assert SKILL_POSITIONS == frozenset({"QB", "RB", "WR", "TE"})
    assert "FB" not in SKILL_POSITIONS
    assert "CB" not in SKILL_POSITIONS
