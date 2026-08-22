from datetime import datetime, timezone

import httpx
import pytest

from ffdo.ingest import connect
from ffdo.ingest.client import SleeperClient

LEAGUE_RAW = {
    "league_id": "L1", "season": "2026", "settings": {"num_teams": 12, "budget": 200},
    "total_rosters": 12,
    "roster_positions": ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                        "BN", "BN", "BN", "BN", "BN"],
    "scoring_settings": {"rec": 0.5, "pass_td": 4},
    "name": "Test League", "status": "pre_draft",
}
DRAFTS_RAW = [{"draft_id": "D1", "type": "auction", "status": "pre_draft"}]
DRAFT_META = {"draft_id": "D1", "type": "auction", "status": "pre_draft",
             "settings": {"teams": 12, "rounds": 13, "budget": 200}}
USER_RAW = {"user_id": "U1", "display_name": "tester", "username": "tester"}
ROSTERS_RAW = [{"roster_id": 3, "owner_id": "U1"},
              {"roster_id": 4, "owner_id": "U2"}]


def _client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def _happy_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("/league/L1/rosters"):
        return httpx.Response(200, json=ROSTERS_RAW)
    if url.endswith("/league/L1/drafts"):
        return httpx.Response(200, json=DRAFTS_RAW)
    if url.endswith("/league/L1"):
        return httpx.Response(200, json=LEAGUE_RAW)
    if url.endswith("/draft/D1"):
        return httpx.Response(200, json=DRAFT_META)
    if url.endswith("/user/tester"):
        return httpx.Response(200, json=USER_RAW)
    raise AssertionError(f"unexpected URL: {url}")


def test_resolve_returns_a_fully_populated_session():
    client = _client(_happy_handler)
    session = connect.resolve(
        client, "L1", "tester",
        now=lambda: datetime(2026, 8, 22, tzinfo=timezone.utc))

    assert session.username == "tester"
    assert session.user_id == "U1"
    assert session.league_id == "L1"
    assert session.draft_id == "D1"
    assert session.roster_id == 3
    assert session.league_name == "Test League"
    assert session.season == 2026
    assert session.num_teams == 12
    assert session.budget == 200
    assert session.roster_positions == (
        "QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
        "BN", "BN", "BN", "BN", "BN")
    assert session.scoring_settings == {"rec": 0.5, "pass_td": 4}
    assert session.draft_type == "auction"
    assert session.draft_status == "pre_draft"
    assert session.connected_at == "2026-08-22T00:00:00+00:00"


def test_resolve_falls_back_to_the_drafts_budget_when_league_settings_omit_it():
    """Mirrors the fallback already used in ffdo.api.app.get_board(): some
    leagues carry the auction budget on the draft object, not the league's
    own settings. LEAGUE_RAW normally carries settings.budget=200 (asserted
    by test_resolve_returns_a_fully_populated_session) -- this test removes
    it to isolate the fallback path specifically."""
    league_no_budget = {**LEAGUE_RAW, "settings": {"num_teams": 12}}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/league/L1/rosters"):
            return httpx.Response(200, json=ROSTERS_RAW)
        if url.endswith("/league/L1/drafts"):
            return httpx.Response(200, json=DRAFTS_RAW)
        if url.endswith("/league/L1"):
            return httpx.Response(200, json=league_no_budget)
        if url.endswith("/draft/D1"):
            return httpx.Response(200, json=DRAFT_META)
        if url.endswith("/user/tester"):
            return httpx.Response(200, json=USER_RAW)
        raise AssertionError(f"unexpected URL: {url}")

    session = connect.resolve(_client(handler), "L1", "tester")
    assert session.budget == 200  # from DRAFT_META.settings.budget


def test_resolve_raises_when_league_is_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(connect.ConnectError, match="League not found"):
        connect.resolve(_client(handler), "bad-league", "tester")


def test_resolve_raises_when_the_league_has_no_draft():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/league/L1/drafts"):
            return httpx.Response(200, json=[])
        if url.endswith("/league/L1"):
            return httpx.Response(200, json=LEAGUE_RAW)
        raise AssertionError(f"unexpected URL: {url}")

    with pytest.raises(connect.ConnectError, match="No draft found"):
        connect.resolve(_client(handler), "L1", "tester")


def test_resolve_raises_when_username_is_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/user/ghost"):
            return httpx.Response(404, json={"error": "not found"})
        if url.endswith("/league/L1/drafts"):
            return httpx.Response(200, json=DRAFTS_RAW)
        if url.endswith("/league/L1"):
            return httpx.Response(200, json=LEAGUE_RAW)
        if url.endswith("/draft/D1"):
            return httpx.Response(200, json=DRAFT_META)
        raise AssertionError(f"unexpected URL: {url}")

    with pytest.raises(connect.ConnectError, match="Username not found"):
        connect.resolve(_client(handler), "L1", "ghost")


def test_resolve_raises_when_the_user_has_no_roster_in_this_league():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/league/L1/rosters"):
            return httpx.Response(200, json=[{"roster_id": 9, "owner_id": "someone-else"}])
        if url.endswith("/league/L1/drafts"):
            return httpx.Response(200, json=DRAFTS_RAW)
        if url.endswith("/league/L1"):
            return httpx.Response(200, json=LEAGUE_RAW)
        if url.endswith("/draft/D1"):
            return httpx.Response(200, json=DRAFT_META)
        if url.endswith("/user/tester"):
            return httpx.Response(200, json=USER_RAW)
        raise AssertionError(f"unexpected URL: {url}")

    with pytest.raises(connect.ConnectError, match="not a member"):
        connect.resolve(_client(handler), "L1", "tester")
