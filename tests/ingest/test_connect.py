from datetime import datetime, timezone

import httpx
import pytest

from ffdo.ingest import connect
from ffdo.ingest import mock_draft
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
    assert session.rounds == 13
    assert session.connected_at == "2026-08-22T00:00:00+00:00"
    assert session.is_mock is False


def test_resolve_reads_rounds_from_the_draft_metas_settings():
    """`Session.rounds` must come from the draft's actual round count
    (`draft_mod.parse(draft_meta, []).rounds`), not be derived from roster
    size -- these diverge for keeper/supplemental drafts. Bumping
    DRAFT_META's settings.rounds here (independent of LEAGUE_RAW's 13-slot
    roster_positions) proves the value is actually threaded through rather
    than coincidentally matching roster size."""
    draft_meta_different_rounds = {
        **DRAFT_META, "settings": {**DRAFT_META["settings"], "rounds": 16},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/league/L1/rosters"):
            return httpx.Response(200, json=ROSTERS_RAW)
        if url.endswith("/league/L1/drafts"):
            return httpx.Response(200, json=DRAFTS_RAW)
        if url.endswith("/league/L1"):
            return httpx.Response(200, json=LEAGUE_RAW)
        if url.endswith("/draft/D1"):
            return httpx.Response(200, json=draft_meta_different_rounds)
        if url.endswith("/user/tester"):
            return httpx.Response(200, json=USER_RAW)
        raise AssertionError(f"unexpected URL: {url}")

    session = connect.resolve(_client(handler), "L1", "tester")
    assert session.rounds == 16


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


MOCK_DRAFT_PRE_DRAFT = {
    "created": 1787468015451,
    "creators": ["461997611847512064"],
    "draft_id": "1397145756879605760",
    "draft_order": None,
    "last_message_id": "1397145756879605760",
    "last_message_time": 1787468015451,
    "last_picked": None,
    "league_id": None,
    "metadata": {"description": "", "name": "", "scoring_type": "ppr"},
    "season": "2026",
    "season_type": "regular",
    "settings": {
        "autostart": 0, "cpu_autopick": 1, "pick_timer": 120, "rounds": 15,
        "slots_def": 1, "slots_flex": 2, "slots_k": 1, "slots_qb": 1,
        "slots_rb": 2, "slots_te": 1, "slots_wr": 2, "teams": 10,
    },
    "slot_to_roster_id": {str(i): i for i in range(1, 11)},
    "sport": "nfl", "start_time": None, "status": "pre_draft", "type": "snake",
}
MOCK_DRAFT_MID_DRAFT = {
    **MOCK_DRAFT_PRE_DRAFT,
    "draft_order": {"461997611847512064": 1},
    "metadata": {"description": "", "name": "", "scoring_type": "half_ppr",
                 "show_team_names": "0"},
    "status": "drafting",
}
MOCK_USER_RAW = {"user_id": "461997611847512064", "display_name": "Schroedes",
                 "username": "schroedes"}


def _mock_client(handler):
    return SleeperClient(base_delay=0, transport=httpx.MockTransport(handler))


def test_resolve_mock_returns_a_fully_populated_session():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/draft/1397145756879605760"):
            return httpx.Response(200, json=MOCK_DRAFT_MID_DRAFT)
        if url.endswith("/user/schroedes"):
            return httpx.Response(200, json=MOCK_USER_RAW)
        raise AssertionError(f"unexpected URL: {url}")

    session = connect.resolve_mock(
        _mock_client(handler), "1397145756879605760", "schroedes",
        now=lambda: datetime(2026, 8, 22, tzinfo=timezone.utc))

    assert session.is_mock is True
    assert session.league_id == ""
    assert session.draft_id == "1397145756879605760"
    assert session.username == "schroedes"
    assert session.user_id == "461997611847512064"
    assert session.roster_id == 1  # joined slot 1
    assert session.season == 2026
    assert session.num_teams == 10
    assert session.budget is None  # snake mock
    assert session.scoring_settings["rec"] == 0.5  # half_ppr
    assert session.draft_type == "snake"
    assert session.draft_status == "drafting"
    assert session.rounds == 15
    assert session.connected_at == "2026-08-22T00:00:00+00:00"


def test_resolve_mock_allows_connecting_before_the_draft_starts():
    """roster_id must be None, not an error, when draft_order has no entry
    for this user yet -- connecting is allowed anytime."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/draft/1397145756879605760"):
            return httpx.Response(200, json=MOCK_DRAFT_PRE_DRAFT)
        if url.endswith("/user/schroedes"):
            return httpx.Response(200, json=MOCK_USER_RAW)
        raise AssertionError(f"unexpected URL: {url}")

    session = connect.resolve_mock(
        _mock_client(handler), "1397145756879605760", "schroedes")
    assert session.roster_id is None


def test_resolve_mock_raises_when_the_draft_is_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(connect.ConnectError, match="Mock draft not found"):
        connect.resolve_mock(_mock_client(handler), "bad-id", "schroedes")


def test_resolve_mock_raises_when_the_draft_fetch_returns_an_empty_response():
    """Sleeper answers some invalid/unknown draft IDs with `200 {}` instead
    of a 404. `is_mock_draft({})` would otherwise return True ({}.get(
    "league_id") is None, same as a real mock draft) and resolution would
    proceed to crash deep inside build_league_profile() on a bare KeyError
    for draft_raw["season"] -- an unhelpful 500 instead of the same clean
    "Mock draft not found" the 404 case already produces."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    with pytest.raises(connect.ConnectError, match="Mock draft not found"):
        connect.resolve_mock(_mock_client(handler), "bad-id", "schroedes")


def test_resolve_mock_rejects_a_real_league_draft():
    real_draft = {**MOCK_DRAFT_MID_DRAFT, "league_id": "1389375982783180800"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=real_draft)

    with pytest.raises(connect.ConnectError, match="real league draft"):
        connect.resolve_mock(_mock_client(handler), "D1", "schroedes")


def test_resolve_mock_raises_when_username_is_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/user/ghost"):
            return httpx.Response(404, json={"error": "not found"})
        if url.endswith("/draft/1397145756879605760"):
            return httpx.Response(200, json=MOCK_DRAFT_MID_DRAFT)
        raise AssertionError(f"unexpected URL: {url}")

    with pytest.raises(connect.ConnectError, match="Username not found"):
        connect.resolve_mock(_mock_client(handler), "1397145756879605760", "ghost")


def test_resolve_mock_raises_for_an_unsupported_scoring_preset():
    dynasty_draft = {
        **MOCK_DRAFT_MID_DRAFT,
        "metadata": {**MOCK_DRAFT_MID_DRAFT["metadata"], "scoring_type": "dynasty_2qb"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=dynasty_draft)

    with pytest.raises(connect.ConnectError, match="dynasty_2qb"):
        connect.resolve_mock(_mock_client(handler), "D1", "schroedes")
