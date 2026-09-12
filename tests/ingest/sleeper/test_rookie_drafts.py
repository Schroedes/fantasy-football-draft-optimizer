from ffdo.ingest.sleeper import rookie_drafts


class _FakeClient:
    def __init__(self, responses):
        self._responses = responses

    def get_json(self, url):
        for key, val in self._responses.items():
            if key in url:
                return val
        raise AssertionError(f"unexpected URL: {url}")


def test_league_seasons_walks_the_previous_league_id_chain():
    client = _FakeClient({
        "/league/L3": {"previous_league_id": "L2"},
        "/league/L2": {"previous_league_id": "L1"},
        "/league/L1": {"previous_league_id": None},
    })
    assert rookie_drafts.league_seasons(client, "L3") == ["L3", "L2", "L1"]


def test_league_seasons_stops_at_max_seasons():
    client = _FakeClient({
        "/league/L3": {"previous_league_id": "L2"},
        "/league/L2": {"previous_league_id": "L1"},
        "/league/L1": {"previous_league_id": "L0"},
    })
    assert rookie_drafts.league_seasons(client, "L3", max_seasons=2) == ["L3", "L2"]


def test_rookie_picks_only_reads_completed_linear_drafts():
    client = _FakeClient({
        "/league/L1/drafts": [
            {"draft_id": "d1", "type": "linear", "status": "complete",
             "season": "2025", "settings": {"rounds": 3}, "metadata": {}},
            {"draft_id": "d2", "type": "snake", "status": "complete",
             "season": "2024", "settings": {"rounds": 15}, "metadata": {}},
            {"draft_id": "d3", "type": "linear", "status": "in_progress",
             "season": "2026", "settings": {"rounds": 3}, "metadata": {}},
        ],
        "/draft/d1/picks": [
            {"pick_no": 1, "round": 1, "draft_slot": 1, "roster_id": 5,
             "picked_by": "u1", "player_id": "p1", "metadata": {}},
            {"pick_no": 2, "round": 1, "draft_slot": 2, "roster_id": 7,
             "picked_by": "u2", "player_id": "p2", "metadata": {}},
        ],
    })
    picks = rookie_drafts.rookie_picks(client, "L1")
    assert picks == [
        rookie_drafts.RookiePick(season=2025, round=1, pick_in_round=1, player_id="p1"),
        rookie_drafts.RookiePick(season=2025, round=1, pick_in_round=2, player_id="p2"),
    ]


def test_rookie_picks_returns_empty_for_a_league_with_no_rookie_drafts_yet():
    client = _FakeClient({
        "/league/L1/drafts": [
            {"draft_id": "d1", "type": "auction", "status": "complete",
             "season": "2024", "settings": {"rounds": 20}, "metadata": {}},
        ],
    })
    assert rookie_drafts.rookie_picks(client, "L1") == []
