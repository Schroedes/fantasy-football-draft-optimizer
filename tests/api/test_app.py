from datetime import datetime, timezone

from ffdo.api import app as app_mod
from ffdo.api.app import (
    _DEFAULT_DRAFT_ID, _DEFAULT_LEAGUE_ID, _TTLCache, _active_only,
    _draft_id, _league_id, _roster_id, _uncached, create_app,
)
from ffdo.api.session import SessionStore
from ffdo.domain.models import PlayerProfile, Session
from ffdo.ingest.client import PROJECTIONS, V1
from fastapi.testclient import TestClient


def _session(**overrides):
    base = dict(
        username="tester", user_id="U1", league_id="session-league",
        draft_id="session-draft", roster_id=5, league_name="Test League",
        season=2026, num_teams=12, budget=200,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                          "BN", "BN", "BN", "BN", "BN"),
        scoring_settings={"rec": 0.5}, draft_type="auction",
        draft_status="pre_draft", rounds=13, is_mock=False,
        connected_at="2026-08-22T00:00:00+00:00",
    )
    return Session(**{**base, **overrides})


class _FakeSleeperClient:
    """Stands in for ffdo.ingest.client.SleeperClient so tests never make a
    real network call. Returns an empty list for any projections URL (that
    parser requires a list) and an empty dict otherwise."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_json(self, url: str):
        return [] if "/projections/" in url else {}

    def close(self) -> None:
        pass


def _recording_client(responses: dict[str, object]):
    """Builds a fake SleeperClient class (plus the list it records calls
    into) for tests that need to assert on get_board()'s actual HTTP call
    pattern -- e.g. proving /draft/<id> is fetched exactly once in mock
    mode, or that a specific pick payload flows all the way through to the
    response. Production code always constructs `SleeperClient()` with no
    arguments, so `responses` and the shared `calls` list are captured by
    closure rather than passed to `__init__`.

    `responses` maps a URL (or a unique substring/prefix of one, e.g. the
    projections URL before its query string) to the canned get_json()
    return value. The first matching key, checked via `in` and tested in
    insertion order, wins -- so when one key is a substring of another
    (e.g. ".../draft/D1" vs ".../draft/D1/picks"), register the longer,
    more specific one first. Unmatched URLs fall back to the same default
    `_FakeSleeperClient` above uses: [] for a projections URL, {} otherwise.
    """
    calls: list[str] = []

    class _RecordingClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_json(self, url: str):
            calls.append(url)
            for key, value in responses.items():
                if key in url:
                    return value
            return [] if "/projections/" in url else {}

        def close(self) -> None:
            pass

    return _RecordingClient, calls


def test_uncached_appends_a_query_param_to_a_plain_url():
    url = _uncached("https://api.sleeper.app/v1/draft/D1")
    assert url.startswith("https://api.sleeper.app/v1/draft/D1?_=")


def test_uncached_appends_with_an_ampersand_when_the_url_already_has_a_query():
    url = _uncached("https://api.sleeper.app/v1/players/nfl?season=2026")
    assert url.startswith("https://api.sleeper.app/v1/players/nfl?season=2026&_=")


def test_uncached_returns_a_different_value_on_each_call():
    """The whole point: a repeated identical URL must produce a distinct
    query string each time, so Sleeper's CDN (observed serving /draft/<id>
    with `cache-control: s-maxage=30` and a steadily climbing `Age` header)
    sees each poll as a fresh cache key rather than replaying the same
    up-to-30s-stale snapshot."""
    a = _uncached("https://api.sleeper.app/v1/draft/D1")
    b = _uncached("https://api.sleeper.app/v1/draft/D1")
    assert a != b


def test_has_value_is_false_before_the_first_load():
    cache = _TTLCache(ttl_seconds=10)
    assert cache.has_value() is False


def test_has_value_is_true_after_a_load_and_does_not_trigger_a_fetch():
    cache = _TTLCache(ttl_seconds=10)
    calls = []
    cache.get(lambda: calls.append(1) or "value")
    assert cache.has_value() is True
    assert cache.has_value() is True
    assert calls == [1], "has_value() must not call the loader"


def test_season_keyed_projections_caches_do_not_mix_seasons():
    """Mirrors `ffdo.api.app.create_app()`'s `_projections_cache_for()`
    pattern directly: `projections_cache` used to be a single shared
    `_TTLCache`, so connecting League A (season 2025) then League B (season
    2026) within the 1h TTL would silently serve 2025's cached payload for a
    2026 board. A dict of `_TTLCache` instances keyed by season, created
    lazily, is what fixes that -- proven here the same way the plain
    `_TTLCache` tests above prove TTL behavior: an injectable clock and a
    `calls` list, this time one pair per season."""
    caches: dict[int, _TTLCache] = {}

    def cache_for(season):
        return caches.setdefault(season, _TTLCache(ttl_seconds=3600))

    calls_2025 = []
    calls_2026 = []

    value_2025 = cache_for(2025).get(lambda: calls_2025.append(1) or "proj-2025")
    value_2026 = cache_for(2026).get(lambda: calls_2026.append(1) or "proj-2026")

    assert value_2025 == "proj-2025"
    assert value_2026 == "proj-2026"
    assert len(calls_2025) == 1
    assert len(calls_2026) == 1

    # Re-fetching season 2025 within the TTL must not re-run 2026's loader
    # (or vice versa) and must not return the other season's cached value --
    # this is exactly the cross-season contamination the single shared cache
    # used to cause.
    again_2025 = cache_for(2025).get(lambda: calls_2025.append(1) or "SHOULD-NOT-RUN")
    assert again_2025 == "proj-2025"
    assert len(calls_2025) == 1
    assert len(calls_2026) == 1


def test_league_id_prefers_the_connected_session_over_env_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("FFDO_LEAGUE_ID", "env-league")
    store = SessionStore(tmp_path / "session.json")
    store.save(_session())
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    assert _league_id() == "session-league"


def test_draft_id_prefers_the_connected_session_over_env_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("FFDO_DRAFT_ID", "env-draft")
    store = SessionStore(tmp_path / "session.json")
    store.save(_session())
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    assert _draft_id() == "session-draft"


def test_roster_id_prefers_the_connected_session_over_env_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("FFDO_ROSTER_ID", "999")
    store = SessionStore(tmp_path / "session.json")
    store.save(_session())
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    assert _roster_id() == 5


def test_ids_fall_back_to_env_vars_when_no_session_is_connected(monkeypatch):
    # No explicit store setup needed -- the autouse `_isolated_session_store`
    # fixture in conftest.py already points `_SESSION_STORE` at a fresh,
    # empty store, so there's nothing connected here by construction.
    monkeypatch.delenv("FFDO_LEAGUE_ID", raising=False)

    assert _league_id() == _DEFAULT_LEAGUE_ID


def test_connect_endpoint_returns_400_for_a_connect_error(monkeypatch):
    from ffdo.ingest import connect as connect_mod

    def raise_connect_error(sleeper, league_id, username):
        raise connect_mod.ConnectError("League not found")

    monkeypatch.setattr("ffdo.ingest.connect.resolve", raise_connect_error)

    client = TestClient(create_app())
    res = client.post("/api/connect", json={"league_id": "bad", "username": "tester"})

    assert res.status_code == 400
    assert res.json()["detail"] == "League not found"


def test_connect_endpoint_rejects_a_blank_league_id_or_username():
    client = TestClient(create_app())

    res = client.post("/api/connect", json={"league_id": "  ", "username": "tester"})

    assert res.status_code == 400


def test_connect_endpoint_rejects_both_league_id_and_draft_id_together():
    client = TestClient(create_app())

    res = client.post("/api/connect", json={
        "league_id": "L1", "draft_id": "D1", "username": "tester"})

    assert res.status_code == 400


def test_connect_endpoint_rejects_neither_league_id_nor_draft_id():
    client = TestClient(create_app())

    res = client.post("/api/connect", json={"username": "tester"})

    assert res.status_code == 400


def test_connect_endpoint_routes_a_draft_id_payload_to_resolve_mock(monkeypatch):
    fake_mock_session = _session(
        league_id="", draft_id="1397145756879605760", is_mock=True)
    captured = {}

    def fake_resolve_mock(sleeper, draft_id, username):
        captured["draft_id"] = draft_id
        return fake_mock_session

    monkeypatch.setattr("ffdo.ingest.connect.resolve_mock", fake_resolve_mock)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/connect", json={
        "draft_id": "https://sleeper.app/draft/nfl/1397145756879605760",
        "username": "schroedes"})

    assert res.status_code == 200
    assert res.json()["is_mock"] is True
    # The share URL's trailing numeric ID must reach resolve_mock() bare,
    # not the whole pasted URL.
    assert captured["draft_id"] == "1397145756879605760"


def test_connect_endpoint_accepts_a_bare_draft_id_without_a_url(monkeypatch):
    captured = {}

    def fake_resolve_mock(sleeper, draft_id, username):
        captured["draft_id"] = draft_id
        return _session(league_id="", is_mock=True)

    monkeypatch.setattr("ffdo.ingest.connect.resolve_mock", fake_resolve_mock)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    client.post("/api/connect", json={"draft_id": "1397145756879605760",
                                      "username": "schroedes"})

    assert captured["draft_id"] == "1397145756879605760"


def test_league_id_is_empty_string_for_a_connected_mock_session(monkeypatch, tmp_path):
    """get_board()'s mock-vs-real branch is driven entirely by whether
    _league_id() returns a falsy value -- this is the one fact that whole
    dispatch depends on, so it gets its own direct test."""
    store = SessionStore(tmp_path / "session.json")
    store.save(_session(league_id="", is_mock=True))
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    assert _league_id() == ""


# ---------------------------------------------------------------------------
# GET /api/board -- real-league and mock-draft branches, through TestClient.
#
# Everything above this point that touches `get_board()`'s internals
# (_league_id/_draft_id/_roster_id) or the mock-draft translation layer
# (ffdo.ingest.mock_draft, tested in tests/ingest/test_mock_draft.py) is a
# unit test of a piece the endpoint is BUILT from. None of it actually calls
# `GET /api/board`, so the endpoint's own wiring -- single-fetch reuse of
# /draft/<id> in mock mode, running backfill_roster_ids() before
# draft.parse() sees the picks, and re-resolving roster_id live rather than
# trusting the persisted session -- was previously proven only by reading
# app.py, not by a test. The fixtures below are adapted from the real
# captured Sleeper payloads in tests/ingest/test_mock_draft.py /
# tests/ingest/test_connect.py (MOCK_DRAFT_PRE_DRAFT / MOCK_DRAFT_MID_DRAFT),
# switched to an auction-type mock so the response carries a `budget`
# section with a `your_roster_id` to assert on.

_BOARD_REAL_LEAGUE_RAW = {
    "league_id": "L123",
    "season": "2025",
    "settings": {"num_teams": 2, "budget": 200},
    "roster_positions": ["QB", "RB", "BN"],
    "scoring_settings": {"rush_yd": 0.1, "rush_td": 6.0},
    "name": "Board Test League",
    "status": "drafting",
}

_BOARD_REAL_LEAGUE_DRAFT_RAW = {
    "draft_id": "D123",
    "type": "auction",
    "status": "drafting",
    "settings": {"teams": 2, "rounds": 3, "budget": 200},
}

_BOARD_PLAYERS_RAW = {
    "P1": {"first_name": "Test", "last_name": "Runner", "position": "RB",
           "team": "AAA", "age": 25, "years_exp": 3, "active": True},
}

_BOARD_PROJECTIONS_RAW = [
    {"player_id": "P1",
     "last_modified": int(datetime(2025, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
     "stats": {"rush_yd": 1000.0, "rush_td": 10.0}},
]

# Real captured /v1/draft/<id> shape (see MOCK_DRAFT_PRE_DRAFT /
# MOCK_DRAFT_MID_DRAFT in tests/ingest/test_mock_draft.py), adapted to an
# auction-type mock with a budget so build_auction_board's `budget` section
# (with `your_roster_id` / `your_spent`) is exercised end to end.
_BOARD_MOCK_DRAFT_RAW = {
    "created": 1787468015451,
    "creators": ["U1"],
    "draft_id": "D999",
    "draft_order": None,
    "last_message_id": "D999",
    "last_message_time": 1787468015451,
    "last_picked": None,
    "league_id": None,
    "metadata": {"description": "", "name": "", "scoring_type": "half_ppr"},
    "season": "2026",
    "season_type": "regular",
    "settings": {
        "autostart": 0, "cpu_autopick": 1, "pick_timer": 120, "rounds": 3,
        "slots_qb": 1, "slots_rb": 1, "slots_bn": 1, "teams": 2, "budget": 200,
    },
    "slot_to_roster_id": {"1": 1, "2": 2},
    "sport": "nfl", "start_time": None, "status": "drafting", "type": "auction",
}


def test_get_board_live_returns_nomination_without_the_heavy_fetches(
        monkeypatch, tmp_path):
    """The whole point of this endpoint: it must answer using only the two
    Sleeper calls that actually carry nomination/bid (draft meta + picks),
    never touching /league/<id>, /players/nfl, or the projections feed --
    those are what made /api/board too slow to poll at the once-a-second
    cadence auction bidding needs (see board.js's `refreshLive`)."""
    store = SessionStore(tmp_path / "session.json")
    store.save(_session(league_id="L123", draft_id="D123", is_mock=False))
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    draft_meta = {**_BOARD_REAL_LEAGUE_DRAFT_RAW,
                 "metadata": {"nominated_player_id": "P1", "highest_offer": "42"}}
    picks_raw = [{
        "draft_id": "D123", "draft_slot": 1, "pick_no": 1, "picked_by": "U1",
        "player_id": "P2", "roster_id": 1, "round": 1, "metadata": {"amount": "10"},
    }]
    FakeClient, calls = _recording_client({
        f"{V1}/draft/D123/picks": picks_raw,
        f"{V1}/draft/D123": draft_meta,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    client = TestClient(create_app())
    res = client.get("/api/board/live")

    assert res.status_code == 200
    body = res.json()
    assert body == {"live_nomination": {"player_id": "P1", "bid": 42}, "picks_made": 1}
    assert not any("/league/" in c or "/players/" in c or "/projections/" in c
                  for c in calls), f"must not fetch league/players/projections -- got {calls}"


def test_get_board_real_league_mode_reports_is_mock_false_and_scores_a_player(
        monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "session.json")
    store.save(_session(league_id="L123", draft_id="D123", is_mock=False))
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    FakeClient, calls = _recording_client({
        f"{V1}/draft/D123/picks": [],
        f"{V1}/draft/D123": _BOARD_REAL_LEAGUE_DRAFT_RAW,
        # Registered before the shorter /league/L123 key below: PR #4's
        # team-name fetch hits /league/L123/rosters and /league/L123/users,
        # both of which contain "/league/L123" as a substring -- without
        # these more-specific entries winning first, they'd incorrectly
        # match the plain league fixture and crash teams_mod.parse().
        f"{V1}/league/L123/rosters": [],
        f"{V1}/league/L123/users": [],
        f"{V1}/league/L123": _BOARD_REAL_LEAGUE_RAW,
        f"{V1}/players/nfl": _BOARD_PLAYERS_RAW,
        f"{PROJECTIONS}/2025": _BOARD_PROJECTIONS_RAW,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    client = TestClient(create_app())
    res = client.get("/api/board")

    assert res.status_code == 200
    body = res.json()
    assert body["is_mock"] is False
    assert f"{V1}/league/L123" in calls
    player_ids = {p["player_id"] for p in body["players"]}
    assert "P1" in player_ids, (
        "the one scoreable player in the fixture must reach the response "
        "-- proves players/projections still flow through the real-league "
        "branch unchanged")


def test_get_board_mock_mode_reports_is_mock_true_and_fetches_the_draft_once(
        monkeypatch, tmp_path):
    """Direct proof of single-fetch reuse: get_board()'s mock branch must
    fetch /draft/<id> exactly once and reuse that same payload for both
    build_league_profile() and draft_meta, rather than fetching it twice."""
    store = SessionStore(tmp_path / "session.json")
    store.save(_session(league_id="", draft_id="D999", is_mock=True, user_id="U1"))
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    FakeClient, calls = _recording_client({
        f"{V1}/draft/D999/picks": [],
        f"{V1}/draft/D999": _BOARD_MOCK_DRAFT_RAW,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    client = TestClient(create_app())
    res = client.get("/api/board")

    assert res.status_code == 200
    assert res.json()["is_mock"] is True

    # Cache-busted with a `?_=...` suffix (see `_uncached` in app.py), so
    # compare on the path alone rather than exact URL equality.
    draft_meta_calls = [c for c in calls if c.split("?", 1)[0] == f"{V1}/draft/D999"]
    assert len(draft_meta_calls) == 1, (
        f"expected exactly one fetch of /draft/D999 (single-fetch reuse "
        f"for both LeagueProfile and draft_meta), got {len(draft_meta_calls)}: "
        f"{calls}")
    assert any(c.split("?", 1)[0] == f"{V1}/draft/D999/picks" for c in calls)


def test_get_board_mock_mode_backfills_picks_and_resolves_roster_id_live(
        monkeypatch, tmp_path):
    """The core regression guard this task exists for. Two things are
    proven together here:

    1. Live roster_id resolution: the persisted session's roster_id is
       None (as it would be if the user connected before joining a draft
       slot), but THIS poll's draft object has draft_order[user_id] = 2.
       get_board() must re-resolve roster_id from that live draft_meta via
       mock_draft.resolve_roster_id() -- never trust the stale persisted
       None (which is what calling `_roster_id()` in mock mode would do).

    2. backfill_roster_ids() running BEFORE draft.parse() sees the picks:
       Sleeper never populates roster_id on a mock-draft pick (it arrives
       null even for the connecting human's own pick). The one pick below
       has roster_id: null and draft_slot: 2, which maps to roster_id 2 via
       slot_to_roster_id. If backfill did not run, or ran after parsing,
       DraftState.spent_by_roster() would skip this pick entirely (it
       explicitly skips a null roster_id) and `your_spent` would be 0
       instead of the pick's $50 amount.
    """
    store = SessionStore(tmp_path / "session.json")
    store.save(_session(league_id="", draft_id="D999", is_mock=True,
                        user_id="U1", roster_id=None))
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    draft_meta = {**_BOARD_MOCK_DRAFT_RAW, "draft_order": {"U1": 2}}
    picks_raw = [{
        "draft_id": "D999", "draft_slot": 2, "pick_no": 1,
        "picked_by": "U1", "player_id": "P1", "roster_id": None,
        "round": 1, "metadata": {"amount": "50"},
    }]
    FakeClient, calls = _recording_client({
        f"{V1}/draft/D999/picks": picks_raw,
        f"{V1}/draft/D999": draft_meta,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    client = TestClient(create_app())
    res = client.get("/api/board")

    assert res.status_code == 200
    body = res.json()
    assert body["is_mock"] is True
    assert body["budget"]["your_roster_id"] == 2, (
        "must reflect the LIVE roster_id resolved from this poll's "
        "draft_order, not the stale persisted session.roster_id (None)")
    assert body["budget"]["your_spent"] == 50, (
        "the pick's null roster_id must have been backfilled to 2 (via "
        "draft_slot 2 -> slot_to_roster_id) before draft.parse() ran, so "
        "spent_by_roster() counts its $50 amount toward roster 2")


def test_get_board_mock_mode_returns_a_clean_400_when_scoring_type_becomes_unsupported(
        monkeypatch, tmp_path):
    """Cross-cutting gap from the final whole-branch review: resolve_mock()
    converts mock_draft.MockDraftError into a clean ConnectError -> 400 at
    /api/connect, but get_board() calls the same build_league_profile() on
    every 3s poll with no equivalent handling. A mock draft's scoring_type
    can drift to an unsupported value AFTER a user has already connected
    (documented in the spec: a real mock draft's scoring_type changed
    between two live polls of the same draft), so this must surface as a
    clean 400 with an actionable detail, not a bare 500."""
    store = SessionStore(tmp_path / "session.json")
    store.save(_session(league_id="", draft_id="D999", is_mock=True, user_id="U1"))
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    drifted_draft = {
        **_BOARD_MOCK_DRAFT_RAW,
        "metadata": {**_BOARD_MOCK_DRAFT_RAW["metadata"], "scoring_type": "dynasty_2qb"},
    }
    FakeClient, calls = _recording_client({
        f"{V1}/draft/D999/picks": [],
        f"{V1}/draft/D999": drifted_draft,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    client = TestClient(create_app())
    res = client.get("/api/board")

    assert res.status_code == 400
    assert "dynasty_2qb" in res.json()["detail"]


def test_get_board_mock_mode_handles_a_snake_draft_without_crashing(
        monkeypatch, tmp_path):
    """The Task 5 fix-round tests for get_board()'s mock branch only ever
    used an auction-type mock draft, so the `if lg.budget is None: lg =
    replace(lg, budget=state.budget)` fallback (which behaves differently
    for snake vs auction -- a snake mock's budget is legitimately None and
    should stay None) was never exercised end to end through the real
    endpoint for the mock path. This is a minimal smoke test, not a
    duplicate of the auction test's full backfill/roster-id assertions."""
    store = SessionStore(tmp_path / "session.json")
    store.save(_session(league_id="", draft_id="D999", is_mock=True, user_id="U1"))
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    snake_draft = {**_BOARD_MOCK_DRAFT_RAW, "type": "snake"}
    FakeClient, calls = _recording_client({
        f"{V1}/draft/D999/picks": [],
        f"{V1}/draft/D999": snake_draft,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    client = TestClient(create_app())
    res = client.get("/api/board")

    assert res.status_code == 200
    body = res.json()
    assert body["format"] == "snake"
    assert body["is_mock"] is True


def test_connect_endpoint_saves_the_session_and_returns_it(monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "session.json")
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)

    fake_session = _session(league_id="L9")
    monkeypatch.setattr("ffdo.ingest.connect.resolve",
                        lambda sleeper, league_id, username: fake_session)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/connect", json={"league_id": "L9", "username": "tester"})

    assert res.status_code == 200
    assert res.json()["league_id"] == "L9"
    assert store.get() == fake_session


def test_session_endpoint_returns_null_when_nothing_is_connected():
    client = TestClient(create_app())

    res = client.get("/api/session")
    assert res.json() is None


def test_session_endpoint_returns_the_connected_session(monkeypatch, tmp_path):
    store = SessionStore(tmp_path / "session.json")
    store.save(_session())
    monkeypatch.setattr(app_mod, "_SESSION_STORE", store)
    client = TestClient(create_app())

    res = client.get("/api/session")
    assert res.json()["league_id"] == "session-league"


def test_readiness_endpoint_reports_pending_before_anything_is_connected():
    client = TestClient(create_app())

    res = client.get("/api/readiness")
    assert res.json() == {"league_draft": "pending", "players": "pending", "projections": "pending"}


def test_readiness_endpoint_reports_synced_after_connecting(monkeypatch):
    monkeypatch.setattr("ffdo.ingest.connect.resolve",
                        lambda sleeper, league_id, username: _session())
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    client.post("/api/connect", json={"league_id": "L1", "username": "tester"})

    res = client.get("/api/readiness")
    body = res.json()
    assert body == {"league_draft": "synced", "players": "synced", "projections": "synced"}


def test_readiness_reports_pending_for_projections_of_a_new_season_even_when_a_different_season_is_already_synced(monkeypatch):
    """The crux of the unkeyed-cache bug: connecting League A (season 2025)
    warms its projections cache. Switching to League B (season 2026) -- here
    simulated by writing directly to `_SESSION_STORE`, standing in for the
    instant after `/api/connect` returns but before the new season's
    background warm task has run -- must report `projections: pending` for
    2026, not spuriously `synced` off of 2025's stale cache entry. A
    misleading `synced` here would be actively wrong, not just wasted work."""
    monkeypatch.setattr("ffdo.ingest.connect.resolve",
                        lambda sleeper, league_id, username: _session(season=2025))
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    client.post("/api/connect", json={"league_id": "L1", "username": "tester"})
    assert client.get("/api/readiness").json()["projections"] == "synced"

    app_mod._SESSION_STORE.save(_session(season=2026, league_id="L2"))

    body = client.get("/api/readiness").json()
    assert body["projections"] == "pending"


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


def test_league_and_draft_ids_default_to_the_pinned_auction_league(monkeypatch):
    """With no env override, the app must keep pointing at the user's real
    2026 auction league/draft -- the auction path must keep working with
    zero config."""
    monkeypatch.delenv("FFDO_LEAGUE_ID", raising=False)
    monkeypatch.delenv("FFDO_DRAFT_ID", raising=False)
    assert _league_id() == _DEFAULT_LEAGUE_ID == "1315881559957458944"
    assert _draft_id() == _DEFAULT_DRAFT_ID == "1315881559965835264"


def test_league_and_draft_ids_are_read_fresh_from_env_on_each_call(monkeypatch):
    """The board is unreachable for snake leagues unless the league/draft id
    can be overridden. Reading `os.environ` fresh inside these functions
    (rather than freezing module-level constants at import time) is what
    makes an env var set after the process starts actually take effect --
    verified here by flipping the env var between two calls."""
    monkeypatch.delenv("FFDO_LEAGUE_ID", raising=False)
    monkeypatch.delenv("FFDO_DRAFT_ID", raising=False)
    assert _league_id() == _DEFAULT_LEAGUE_ID
    assert _draft_id() == _DEFAULT_DRAFT_ID

    monkeypatch.setenv("FFDO_LEAGUE_ID", "999888777")
    monkeypatch.setenv("FFDO_DRAFT_ID", "111222333")
    assert _league_id() == "999888777"
    assert _draft_id() == "111222333"
    assert _league_id() != _DEFAULT_LEAGUE_ID


def test_roster_id_is_none_when_unset(monkeypatch):
    monkeypatch.delenv("FFDO_ROSTER_ID", raising=False)
    assert _roster_id() is None


def test_roster_id_reads_the_env_override(monkeypatch):
    monkeypatch.setenv("FFDO_ROSTER_ID", "7")
    assert _roster_id() == 7


def test_roster_id_is_none_on_a_non_numeric_override(monkeypatch):
    monkeypatch.setenv("FFDO_ROSTER_ID", "not-a-number")
    assert _roster_id() is None


def _profile(pid, active):
    return PlayerProfile(player_id=pid, first_name="P", last_name=pid,
                         position="RB", team="X", age=30, years_exp=10,
                         injury_status=None, active=active)


def test_active_only_drops_inactive_players():
    """`PlayerProfile.active` was parsed but never used as a filter --
    a retired player (e.g. Cam Newton) could reach the valuation pool with
    a deeply negative VOR instead of not appearing at all."""
    points = {"active_p": 150.0, "retired_p": 5.0}
    profiles = {"active_p": _profile("active_p", active=True),
                "retired_p": _profile("retired_p", active=False)}
    out = _active_only(points, profiles)
    assert out == {"active_p": 150.0}
