import httpx
import pytest

from ffdo.ingest import discover
from ffdo.ingest.client import SleeperClient
from ffdo.ingest.connect import ConnectError

_LEAGUES_RAW = [
    {"league_id": "L1", "name": "Redraft Home", "season": "2026",
     "total_rosters": 12, "status": "in_season", "settings": {"type": 0, "num_teams": 12}},
    {"league_id": "L2", "name": "The Dynasty", "season": "2026",
     "total_rosters": 10, "status": "pre_draft",
     "settings": {"type": 2, "num_teams": 10}},
]


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def test_list_leagues_maps_each_entry():
    def handler(request):
        assert request.url.path == "/v1/user/U1/leagues/nfl/2026"
        return httpx.Response(200, json=_LEAGUES_RAW)

    out = discover.list_leagues(_client(handler), "U1", 2026)

    assert [d.provider_league_id for d in out] == ["L1", "L2"]
    assert out[0].fmt == "redraft" and out[1].fmt == "dynasty"
    assert out[0].num_teams == 12
    assert out[0].draft_status == "in_season"
    assert all(d.provider == "sleeper" and d.season == 2026 for d in out)
    assert all(d.already_tracked is False for d in out)


def test_list_leagues_flags_already_tracked():
    def handler(request):
        return httpx.Response(200, json=_LEAGUES_RAW)

    out = discover.list_leagues(_client(handler), "U1", 2026,
                                tracked_keys=frozenset({"sleeper:L2:2026"}))
    by_id = {d.provider_league_id: d for d in out}
    assert by_id["L2"].already_tracked is True
    assert by_id["L1"].already_tracked is False


def test_list_leagues_returns_empty_for_no_leagues():
    out = discover.list_leagues(_client(lambda r: httpx.Response(200, json=[])), "U1", 2026)
    assert out == []


def test_resolve_user_id_raises_connect_error_on_404():
    def handler(request):
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(ConnectError, match="Username not found"):
        discover.resolve_user_id(_client(handler), "ghost")


def test_resolve_user_id_raises_connect_error_on_a_200_null_body():
    """Sleeper answers an unknown username with `200 null`, not a 404 -- so
    the status-error arm never fires and `user_mod.parse(None)` would raise a
    bare TypeError, surfacing on the discovery screen as a 500 instead of
    "Username not found"."""
    def handler(request):
        # A literal `null` body, which is what Sleeper actually sends --
        # httpx's `json=None` kwarg means "no json", not "the value null".
        return httpx.Response(200, content=b"null",
                              headers={"content-type": "application/json"})

    with pytest.raises(ConnectError, match="Username not found"):
        discover.resolve_user_id(_client(handler), "ghost")
