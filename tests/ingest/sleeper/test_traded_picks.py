import httpx

from ffdo.ingest.client import SleeperClient
from ffdo.ingest.sleeper import traded_picks

# 3-team league; standings worst->best = [3, 2, 1] (roster 3 picks first)
STANDINGS = [3, 2, 1]
NAMES = {1: "Alpha", 2: "Bravo", 3: "Charlie"}


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def _capital(traded_raw, years=(2027, 2028), rounds=1):
    def handler(request):
        assert request.url.path.endswith("/traded_picks")
        return httpx.Response(200, json=traded_raw)

    return traded_picks.capital(
        _client(handler), "L1",
        num_teams=3, rounds=rounds, standings_order=STANDINGS,
        draft_years=years, team_names=NAMES)


def test_no_trades_every_roster_owns_its_own_picks():
    out = _capital([])
    # 3 rosters x 2 years x 1 round = 6 assets
    assert len(out) == 6
    a = {(p.season, p.round, p.original_roster_id): p for p in out}
    own = a[(2027, 1, 3)]
    assert own.current_owner_roster_id == 3
    assert own.original_roster_id == 3
    assert own.via_team_name is None
    assert own.projected_slot == 1                 # roster 3 is worst -> pick 1
    assert a[(2027, 1, 1)].projected_slot == 3     # roster 1 is best -> pick 3
    assert a[(2028, 1, 1)].projected_slot is None  # year+1 -> round only


def test_single_trade_moves_ownership_and_sets_via():
    # roster 1's 2027 R1 pick was traded to roster 3
    traded = [{"season": "2027", "round": 1, "roster_id": 1,
               "owner_id": 3, "previous_owner_id": 1}]
    out = _capital(traded)
    pick = next(p for p in out if p.season == 2027 and p.original_roster_id == 1)
    assert pick.current_owner_roster_id == 3
    assert pick.via_team_name == "Charlie"         # current owner's name
    assert pick.projected_slot == 3                # slot follows ORIGINAL roster (1 = best)


def test_chain_trade_lands_on_final_owner():
    # roster 1's pick: 1 -> 2, then 2 -> 3
    traded = [
        {"season": "2027", "round": 1, "roster_id": 1, "owner_id": 2, "previous_owner_id": 1},
        {"season": "2027", "round": 1, "roster_id": 1, "owner_id": 3, "previous_owner_id": 2},
    ]
    out = _capital(traded)
    pick = next(p for p in out if p.season == 2027 and p.original_roster_id == 1)
    assert pick.current_owner_roster_id == 3


def test_404_returns_implicit_ownership_not_an_error():
    def handler(request):
        return httpx.Response(404, json={"error": "not found"})

    out = traded_picks.capital(
        _client(handler), "L1", num_teams=3, rounds=1,
        standings_order=STANDINGS, draft_years=(2027,), team_names=NAMES)
    assert len(out) == 3
    assert all(p.current_owner_roster_id == p.original_roster_id for p in out)
