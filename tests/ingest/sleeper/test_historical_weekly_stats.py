from ffdo.ingest.sleeper import historical_weekly_stats


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    def get_json(self, url):
        assert "/stats/nfl/regular/2025/6" in url
        return self._payload


def test_fetch_parses_numeric_stats_only():
    client = _FakeClient({
        "p1": {"pts_half_ppr": 14.2, "rec_yd": 55, "player_active": True},
        "p2": "not a dict",
    })
    result = historical_weekly_stats.fetch(client, 2025, 6)
    assert result == {"p1": {"pts_half_ppr": 14.2, "rec_yd": 55.0}}


def test_fetch_returns_empty_for_a_bye_week_player_with_no_stats():
    client = _FakeClient({"p1": {}})
    result = historical_weekly_stats.fetch(client, 2025, 6)
    assert result == {"p1": {}}
