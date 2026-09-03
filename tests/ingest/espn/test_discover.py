import httpx
import pytest

from ffdo.ingest.espn import discover
from ffdo.ingest.espn.connect import ConnectError

_FAN_RAW = {
    "preferences": [
        {"type": {"code": "fantasy"}, "metaData": {"entry": {
            "entryId": 7, "gameId": 1, "seasonId": 2026,
            "name": "Team Schroeder",
            "groups": [{"groupId": 1882997948, "groupName": "Dynasty Warehouse",
                        "groupSize": 12, "draftComplete": True}]}}},
        {"type": {"code": "fantasy"}, "metaData": {"entry": {
            "entryId": 3, "gameId": 1, "seasonId": 2025,
            "groups": [{"groupId": 999, "groupName": "Last Year", "groupSize": 10}]}}},
        {"type": {"code": "fantasy"}, "metaData": {"entry": {
            "entryId": 9, "gameId": 40, "seasonId": 2026,   # gameId 40 = basketball
            "groups": [{"groupId": 555, "groupName": "Hoops"}]}}},
    ]
}


def _transport(handler):
    return httpx.MockTransport(handler)


def test_list_leagues_filters_to_football_and_season_and_maps():
    def handler(request):
        assert "fan.api.espn.com" in str(request.url)
        return httpx.Response(200, json=_FAN_RAW)

    out = discover.list_leagues("s2", "{SWID}", 2026, transport=_transport(handler))

    assert len(out) == 1
    d = out[0]
    assert d.provider == "espn"
    assert d.provider_league_id == "1882997948"
    assert d.name == "Dynasty Warehouse"
    assert d.num_teams == 12
    assert d.draft_status == "complete"
    assert d.season == 2026


def test_list_leagues_flags_already_tracked():
    def handler(request):
        return httpx.Response(200, json=_FAN_RAW)

    out = discover.list_leagues("s2", "{SWID}", 2026,
                                tracked_keys=frozenset({"espn:1882997948:2026"}),
                                transport=_transport(handler))
    assert out[0].already_tracked is True


def test_list_leagues_raises_on_expired_cookies():
    def handler(request):
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(ConnectError, match="expired"):
        discover.list_leagues("s2", "{SWID}", 2026, transport=_transport(handler))


def test_list_leagues_returns_empty_on_an_unexpected_error():
    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    assert discover.list_leagues("s2", "{SWID}", 2026,
                                 transport=_transport(handler)) == []


def test_list_leagues_returns_empty_on_a_non_object_body():
    def handler(request):
        return httpx.Response(200, json=[])

    assert discover.list_leagues("s2", "{SWID}", 2026,
                                 transport=_transport(handler)) == []


def test_list_leagues_skips_a_malformed_entry_and_keeps_the_rest():
    raw = {
        "preferences": [
            {"type": {"code": "fantasy"}, "metaData": {"entry": {
                "entryId": 5, "gameId": 1, "seasonId": "not-a-year",
                "groups": [{"groupId": 111, "groupName": "Broken", "groupSize": "twelve"}]}}},
            {"type": {"code": "fantasy"}, "metaData": {"entry": {
                "entryId": 7, "gameId": 1, "seasonId": 2026,
                "groups": [{"groupId": 1882997948, "groupName": "Dynasty Warehouse",
                            "groupSize": 12, "draftComplete": True}]}}},
        ]
    }

    def handler(request):
        return httpx.Response(200, json=raw)

    out = discover.list_leagues("s2", "{SWID}", 2026, transport=_transport(handler))
    assert [d.provider_league_id for d in out] == ["1882997948"]
