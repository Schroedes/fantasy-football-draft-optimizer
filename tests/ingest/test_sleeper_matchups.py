from unittest.mock import MagicMock

from ffdo.ingest.sleeper import matchups


def _client(rows):
    client = MagicMock()
    client.get_json.return_value = rows
    return client


def test_fetch_pairs_rosters_sharing_a_matchup_id():
    rows = [
        {"roster_id": 1, "matchup_id": 10, "players_points": {"p1": 12.5, "p2": 8.0}},
        {"roster_id": 2, "matchup_id": 10, "players_points": {"p3": 9.5}},
        {"roster_id": 3, "matchup_id": 11, "players_points": {"p4": 3.0}},
        {"roster_id": 4, "matchup_id": 11, "players_points": {}},
    ]
    result = matchups.fetch(_client(rows), "L1", 3)
    assert result.pairing == {1: 2, 2: 1, 3: 4, 4: 3}
    assert result.live_points == {"p1": 12.5, "p2": 8.0, "p3": 9.5, "p4": 3.0}


def test_fetch_leaves_an_unpaired_roster_out_of_pairing():
    # Odd team count: matchup_id 12 has only one roster in it (a bye).
    rows = [
        {"roster_id": 1, "matchup_id": 10, "players_points": {"p1": 5.0}},
        {"roster_id": 2, "matchup_id": 10, "players_points": {"p2": 6.0}},
        {"roster_id": 5, "matchup_id": 12, "players_points": {"p5": 1.0}},
    ]
    result = matchups.fetch(_client(rows), "L1", 3)
    assert result.pairing == {1: 2, 2: 1}
    assert 5 not in result.pairing
    assert result.live_points == {"p1": 5.0, "p2": 6.0, "p5": 1.0}


def test_fetch_returns_empty_on_a_404_not_yet_generated_week():
    import httpx
    client = MagicMock()
    resp = httpx.Response(404, request=httpx.Request("GET", "http://x"))
    client.get_json.side_effect = httpx.HTTPStatusError("404", request=resp.request, response=resp)
    result = matchups.fetch(client, "L1", 1)
    assert result.pairing == {}
    assert result.live_points == {}


def test_fetch_handles_a_missing_or_empty_rows_list():
    result = matchups.fetch(_client(None), "L1", 1)
    assert result.pairing == {}
    assert result.live_points == {}
