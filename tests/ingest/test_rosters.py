import httpx

from ffdo.ingest import rosters
from ffdo.ingest.client import SleeperClient

_ROSTERS_RAW = [
    {"roster_id": 1, "owner_id": "u1",
     "players": ["100", "200", "300"], "starters": ["100", "200", "0"],
     "settings": {"wins": 6, "losses": 3, "ties": 0,
                  "fpts": 1284, "fpts_decimal": 60,
                  "fpts_against": 1244, "fpts_against_decimal": 0}},
    {"roster_id": 2, "owner_id": "u2",
     "players": None, "starters": None,
     "settings": {"wins": 3, "losses": 6, "ties": 0, "fpts": 1100, "fpts_against": 1200}},
]
_USERS_RAW = [
    {"user_id": "u1", "display_name": "user1", "metadata": {"team_name": "The Foobars"}},
    {"user_id": "u2", "display_name": "CoolTeam", "metadata": {}},
]


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def _handler(request):
    url = str(request.url)
    if url.endswith("/league/L1/rosters"):
        return httpx.Response(200, json=_ROSTERS_RAW)
    if url.endswith("/league/L1/users"):
        return httpx.Response(200, json=_USERS_RAW)
    raise AssertionError(url)


def test_fetch_maps_players_starters_and_record():
    out = rosters.fetch(_client(_handler), "L1")
    assert [r.roster_id for r in out] == [1, 2]
    r1 = out[0]
    assert r1.team_name == "The Foobars"
    assert r1.player_ids == ("100", "200", "300")
    assert r1.starter_ids == ("100", "200")          # "0" (empty slot) dropped
    assert r1.wins == 6 and r1.losses == 3
    assert r1.points_for == 1284.60                   # fpts + fpts_decimal/100
    assert r1.points_against == 1244.0


def test_fetch_handles_null_players_and_missing_decimals():
    out = rosters.fetch(_client(_handler), "L1")
    r2 = out[1]
    assert r2.player_ids == ()
    assert r2.starter_ids == ()
    assert r2.team_name == "CoolTeam"                 # falls back to display_name
    assert r2.points_for == 1100.0


def test_raw_starters_preserves_positional_alignment_including_empty_slots():
    rosters_raw = [
        {"roster_id": 1, "owner_id": "U1", "players": ["a", "b", "c"],
         "starters": ["a", "0", "c", "b"],
         "settings": {"wins": 0, "losses": 0}},
        {"roster_id": 2, "owner_id": "U2", "players": ["d"],
         "starters": ["d"], "settings": {"wins": 0, "losses": 0}},
    ]

    def handler(request):
        if request.url.path.endswith("/rosters"):
            return httpx.Response(200, json=rosters_raw)
        return httpx.Response(200, json=[])

    out = rosters.raw_starters(_client(handler), "L1", roster_id=1)
    assert out == ("a", None, "c", "b")


def test_raw_starters_returns_empty_tuple_for_an_unknown_roster_id():
    def handler(request):
        return httpx.Response(200, json=[
            {"roster_id": 1, "owner_id": "U1", "players": [], "starters": [],
             "settings": {}},
        ])

    assert rosters.raw_starters(_client(handler), "L1", roster_id=99) == ()
