from datetime import datetime, timezone

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ffdo.api import app as app_mod
from ffdo.api.app import _TTLCache, _active_only, _load_league, _uncached, create_app
from ffdo.api.store import LeagueStore
from ffdo.domain.models import DiscoveredLeague, PlayerProfile, ProviderCredential, TrackedLeague
from ffdo.ingest.client import PROJECTIONS, V1


def _tracked(**overrides):
    """A minimal but complete `TrackedLeague`, defaulting to the same
    `sleeper:L123:2025` auction league the board fixtures use."""
    base = dict(
        league_key="sleeper:L123:2025", provider="sleeper", provider_league_id="L123",
        season=2025, name="Test League", user_id="U1", roster_id=5,
        draft_id="D123", draft_type="auction", draft_status="drafting",
        num_teams=2, budget=200, rounds=3,
        roster_positions=("QB", "RB", "BN"), scoring_settings={"rec": 0.5},
        fmt="redraft", format_override=None, raw_settings={}, is_mock=False,
        tracked_at="2026-09-02T00:00:00+00:00",
        last_refreshed_at="2026-09-02T00:00:00+00:00",
    )
    return TrackedLeague(**{**base, **overrides})


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
    into) for tests that need to assert on an endpoint's actual HTTP call
    pattern. Production code always constructs `SleeperClient()` with no
    arguments, so `responses` and the shared `calls` list are captured by
    closure rather than passed to `__init__`.

    `responses` maps a URL (or a unique substring/prefix of one) to the
    canned get_json() return value. The first matching key, checked via `in`
    and tested in insertion order, wins -- so when one key is a substring of
    another (e.g. ".../draft/D1" vs ".../draft/D1/picks"), register the
    longer, more specific one first.
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


def _recording_espn_client(responses: dict[str, object]):
    """Same shape as `_recording_client` above, but for
    `ffdo.ingest.espn.client.EspnClient` -- which takes `(espn_s2, swid)`
    positionally in production and has a `get_json(url, extra_headers=None,
    max_attempts=4)` signature, wider than Sleeper's plain `get_json(url)`."""
    calls: list[str] = []

    class _RecordingEspnClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_json(self, url: str, extra_headers=None, max_attempts: int = 4):
            calls.append(url)
            for key, value in responses.items():
                if key in url:
                    return value
            return {}

        def close(self) -> None:
            pass

    return _RecordingEspnClient, calls


# ---------------------------------------------------------------------------
# _load_league


def test_load_league_returns_the_tracked_league(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)

    assert _load_league("sleeper:L123:2025").draft_id == "D123"


def test_load_league_404s_for_an_unknown_key(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "_STORE", LeagueStore(tmp_path / "ffdo.db"))

    with pytest.raises(HTTPException) as exc:
        _load_league("sleeper:nope:2025")

    assert exc.value.status_code == 404
    assert exc.value.detail == "League not tracked"


# ---------------------------------------------------------------------------
# POST /api/providers/connect


def test_providers_connect_sleeper_stores_credential_and_returns_discovered(monkeypatch):
    discovered = [DiscoveredLeague("sleeper", "L1", 2026, "Home", 12, "", "redraft",
                                   "in_season", False)]
    monkeypatch.setattr("ffdo.ingest.discover.resolve_user_id",
                        lambda sleeper, username: "U1")
    monkeypatch.setattr("ffdo.ingest.discover.list_leagues",
                        lambda sleeper, user_id, season, tracked_keys=frozenset(): discovered)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/providers/connect",
                      json={"provider": "sleeper", "username": "noah", "season": 2026})

    assert res.status_code == 200
    body = res.json()
    assert [d["provider_league_id"] for d in body["leagues"]] == ["L1"]
    # `fmt` is the dataclass field name; `format` is this app's JSON key.
    assert body["leagues"][0]["format"] == "redraft"
    assert "fmt" not in body["leagues"][0]
    assert app_mod._STORE.get_credential("sleeper").user_identifier == "noah"


def test_providers_connect_sleeper_rejects_a_blank_username(monkeypatch):
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)
    client = TestClient(create_app())
    res = client.post("/api/providers/connect",
                      json={"provider": "sleeper", "username": "  ", "season": 2026})
    assert res.status_code == 400


def test_providers_connect_maps_a_connect_error_to_400(monkeypatch):
    from ffdo.ingest import connect as connect_mod

    def raise_connect_error(sleeper, username):
        raise connect_mod.ConnectError("Username not found")

    monkeypatch.setattr("ffdo.ingest.discover.resolve_user_id", raise_connect_error)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/providers/connect",
                      json={"provider": "sleeper", "username": "ghost", "season": 2026})

    assert res.status_code == 400
    assert res.json()["detail"] == "Username not found"


def test_providers_connect_espn_strips_cookies_from_the_response(monkeypatch):
    """Credentials are stored, never echoed: nothing in the frontend reads
    them back, and handing a browser-side script a live copy of a session
    cookie is a needless credential leak."""
    monkeypatch.setattr(
        "ffdo.ingest.espn.discover.list_leagues",
        lambda espn_s2, swid, season, tracked_keys=frozenset(), transport=None: [])

    client = TestClient(create_app())
    res = client.post("/api/providers/connect", json={
        "provider": "espn", "season": 2026, "espn_s2": "s2secret", "swid": "{SWID}"})

    assert res.status_code == 200
    assert "s2secret" not in res.text
    assert "SWID" not in res.text
    # ...but it IS persisted, so tracking/board polls can use it.
    cred = app_mod._STORE.get_credential("espn")
    assert cred.espn_s2 == "s2secret"


def test_providers_connect_espn_requires_both_cookies():
    client = TestClient(create_app())
    res = client.post("/api/providers/connect",
                      json={"provider": "espn", "season": 2026, "espn_s2": "s2"})
    assert res.status_code == 400


def test_providers_connect_rejects_an_unknown_provider():
    client = TestClient(create_app())
    res = client.post("/api/providers/connect",
                      json={"provider": "yahoo", "season": 2026})
    assert res.status_code == 400


def test_providers_connect_rejects_a_missing_season():
    client = TestClient(create_app())
    res = client.post("/api/providers/connect",
                      json={"provider": "sleeper", "username": "noah"})
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/leagues/discovered


def test_discovered_uses_the_stored_credential(monkeypatch):
    app_mod._STORE.put_credential(
        ProviderCredential("sleeper", "noah", None, None, "t"))
    monkeypatch.setattr("ffdo.ingest.discover.resolve_user_id",
                        lambda sleeper, username: f"user-of-{username}")
    monkeypatch.setattr(
        "ffdo.ingest.discover.list_leagues",
        lambda sleeper, user_id, season, tracked_keys=frozenset(): [
            DiscoveredLeague("sleeper", user_id, season, "Home", 12, "", "keeper",
                             "in_season", False)])
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.get("/api/leagues/discovered",
                     params={"provider": "sleeper", "season": 2026})

    assert res.status_code == 200
    assert res.json()["leagues"][0]["provider_league_id"] == "user-of-noah"


def test_discovered_400s_without_a_stored_credential():
    client = TestClient(create_app())
    res = client.get("/api/leagues/discovered",
                     params={"provider": "sleeper", "season": 2026})
    assert res.status_code == 400


def test_discovered_is_not_captured_as_a_league_key():
    """Route-order regression guard: `/api/leagues/discovered` is declared
    before `/api/leagues/{league_key}`, so it must not 404 as an untracked
    league key."""
    client = TestClient(create_app())
    res = client.get("/api/leagues/discovered",
                     params={"provider": "sleeper", "season": 2026})
    assert res.status_code == 400  # "connect first", not 404 "League not tracked"


# ---------------------------------------------------------------------------
# POST /api/leagues/track


def test_track_endpoint_persists_a_sleeper_league(monkeypatch):
    app_mod._STORE.put_credential(
        ProviderCredential("sleeper", "noah", None, None, "t"))
    captured = {}

    def fake_track(sleeper, league_id, username):
        captured["league_id"] = league_id
        captured["username"] = username
        return _tracked(league_key="sleeper:L9:2026", provider_league_id="L9",
                        season=2026)

    monkeypatch.setattr("ffdo.ingest.connect.track", fake_track)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/leagues/track", json={
        "provider": "sleeper", "provider_league_id": "L9", "season": 2026})

    assert res.status_code == 200
    assert res.json()["leagues"][0]["league_key"] == "sleeper:L9:2026"
    assert app_mod._STORE.get("sleeper:L9:2026") is not None
    # The username comes from the stored credential, never from the request.
    assert captured == {"league_id": "L9", "username": "noah"}


def test_track_endpoint_400s_without_a_sleeper_credential():
    client = TestClient(create_app())
    res = client.post("/api/leagues/track", json={
        "provider": "sleeper", "provider_league_id": "L9", "season": 2026})
    assert res.status_code == 400


def test_track_endpoint_accepts_a_batch_of_leagues(monkeypatch):
    app_mod._STORE.put_credential(
        ProviderCredential("sleeper", "noah", None, None, "t"))
    monkeypatch.setattr(
        "ffdo.ingest.connect.track",
        lambda sleeper, league_id, username: _tracked(
            league_key=f"sleeper:{league_id}:2026", provider_league_id=league_id,
            season=2026))
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/leagues/track", json={"leagues": [
        {"provider": "sleeper", "provider_league_id": "L1", "season": 2026},
        {"provider": "sleeper", "provider_league_id": "L2", "season": 2026},
    ]})

    assert res.status_code == 200
    assert len(res.json()["leagues"]) == 2
    assert app_mod._STORE.get("sleeper:L1:2026") is not None
    assert app_mod._STORE.get("sleeper:L2:2026") is not None


def test_track_endpoint_extracts_a_draft_id_from_a_pasted_mock_share_url(monkeypatch):
    """Behavior preserved from the removed `POST /api/connect`: a user
    pastes the whole `https://sleeper.app/draft/nfl/<id>` share URL rather
    than digging the bare ID out of it."""
    app_mod._STORE.put_credential(
        ProviderCredential("sleeper", "noah", None, None, "t"))
    captured = {}

    def fake_track_mock(sleeper, draft_id, username):
        captured["draft_id"] = draft_id
        return _tracked(league_key="sleeper-mock:1397145756879605760:2026",
                        provider="sleeper-mock",
                        provider_league_id="1397145756879605760",
                        draft_id="1397145756879605760", season=2026, is_mock=True)

    monkeypatch.setattr("ffdo.ingest.connect.track_mock", fake_track_mock)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/leagues/track", json={
        "provider": "sleeper-mock",
        "provider_league_id": "https://sleeper.app/draft/nfl/1397145756879605760",
        "season": 2026})

    assert res.status_code == 200
    assert captured["draft_id"] == "1397145756879605760"


def test_track_endpoint_maps_a_connect_error_to_400(monkeypatch):
    from ffdo.ingest import connect as connect_mod

    app_mod._STORE.put_credential(
        ProviderCredential("sleeper", "noah", None, None, "t"))

    def raise_connect_error(sleeper, league_id, username):
        raise connect_mod.ConnectError("League not found")

    monkeypatch.setattr("ffdo.ingest.connect.track", raise_connect_error)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/leagues/track", json={
        "provider": "sleeper", "provider_league_id": "bad", "season": 2026})

    assert res.status_code == 400
    assert res.json()["detail"] == "League not found"


def _unreachable_client():
    """A provider client whose every `get_json` fails at the transport layer,
    the way an outage (or a dropped connection) actually presents. Signature is
    wide enough to stand in for both `SleeperClient.get_json(url)` and
    `EspnClient.get_json(url, extra_headers=None, max_attempts=4)`."""

    class _UnreachableClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_json(self, url: str, *args, **kwargs):
            raise httpx.ConnectError("boom")

        def close(self) -> None:
            pass

    return _UnreachableClient


def test_track_endpoint_502s_when_sleeper_is_unreachable(monkeypatch):
    """Spec §5.4: a provider outage is a 502, not a 500 -- a bare 500 tells the
    user their own input was wrong. `_sleeper_discover` already had this arm;
    track did not."""
    app_mod._STORE.put_credential(
        ProviderCredential("sleeper", "noah", None, None, "t"))
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _unreachable_client())

    client = TestClient(create_app())
    res = client.post("/api/leagues/track", json={
        "provider": "sleeper", "provider_league_id": "L9", "season": 2026})

    assert res.status_code == 502
    assert "try again" in res.json()["detail"]
    assert app_mod._STORE.list() == []


def test_track_endpoint_502s_when_sleeper_is_unreachable_for_a_mock(monkeypatch):
    app_mod._STORE.put_credential(
        ProviderCredential("sleeper", "noah", None, None, "t"))
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _unreachable_client())

    client = TestClient(create_app())
    res = client.post("/api/leagues/track", json={
        "provider": "sleeper-mock", "provider_league_id": "D9", "season": 2026})

    assert res.status_code == 502
    assert app_mod._STORE.list() == []


def test_track_endpoint_502s_when_espn_is_unreachable(monkeypatch):
    app_mod._STORE.put_credential(
        ProviderCredential("espn", "{SWID}", "s2val", "{SWID}", "t"))
    # Sleeper stays reachable (the ESPN branch needs its player profiles for
    # the crosswalk); only ESPN's own client fails, so this exercises the
    # ESPN arm rather than the Sleeper one.
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)
    monkeypatch.setattr("ffdo.ingest.espn.connect.EspnClient", _unreachable_client())

    client = TestClient(create_app())
    res = client.post("/api/leagues/track", json={
        "provider": "espn", "provider_league_id": "9", "season": 2026})

    assert res.status_code == 502
    assert "try again" in res.json()["detail"]
    assert app_mod._STORE.list() == []


def test_refresh_502s_when_sleeper_is_unreachable(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    store.put_credential(ProviderCredential("sleeper", "noah", None, None, "t"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _unreachable_client())

    client = TestClient(create_app())
    res = client.post("/api/leagues/sleeper:L123:2025/refresh")

    assert res.status_code == 502


def test_track_endpoint_persists_nothing_when_a_later_item_fails(monkeypatch):
    """All-or-nothing. Upserting inside the resolve loop left item 1 tracked
    while the caller got a 400 and no `leagues` body -- a half-written batch
    the discovery screen has no way to see or reconcile."""
    from ffdo.ingest import connect as connect_mod

    app_mod._STORE.put_credential(
        ProviderCredential("sleeper", "noah", None, None, "t"))

    def fake_track(sleeper, league_id, username):
        if league_id == "L2":
            raise connect_mod.ConnectError("League not found")
        return _tracked(league_key=f"sleeper:{league_id}:2026",
                        provider_league_id=league_id, season=2026)

    monkeypatch.setattr("ffdo.ingest.connect.track", fake_track)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/leagues/track", json={"leagues": [
        {"provider": "sleeper", "provider_league_id": "L1", "season": 2026},
        {"provider": "sleeper", "provider_league_id": "L2", "season": 2026},
    ]})

    assert res.status_code == 400
    assert res.json()["detail"] == "League not found"
    # L1 resolved cleanly but must NOT have been persisted.
    assert app_mod._STORE.list() == []


def test_track_endpoint_warms_the_caches_for_the_new_league(monkeypatch):
    """`_warm_caches` used to run as a background task off `POST /api/connect`,
    which no longer exists -- tracking is now the moment we know which season
    and provider the user is about to draft in, so the warm must ride along
    there or the draft room's first load pays for every fetch synchronously."""
    app_mod._STORE.put_credential(
        ProviderCredential("sleeper", "noah", None, None, "t"))
    monkeypatch.setattr(
        "ffdo.ingest.connect.track",
        lambda sleeper, league_id, username: _tracked(
            league_key="sleeper:L9:2026", provider_league_id="L9", season=2026))

    FakeClient, calls = _recording_client({})
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    client = TestClient(create_app())
    res = client.post("/api/leagues/track", json={
        "provider": "sleeper", "provider_league_id": "L9", "season": 2026})

    assert res.status_code == 200
    assert any("/players/nfl" in c for c in calls), (
        "the background warm must have populated the players cache")
    assert any("/projections/nfl/2026" in c for c in calls), (
        "the warm must use the tracked league's season, not a default")
    # The contrast against test_tracking_a_mock_never_warms_the_team_name_cache:
    # a REAL Sleeper league does warm the team-name cache, so that test is
    # proving the gate discriminates, not that the warm never runs at all.
    assert any("/league/L9/rosters" in c for c in calls), (
        "a real Sleeper league must warm the team-name cache")


def test_tracking_a_mock_never_warms_the_team_name_cache(monkeypatch):
    """A `sleeper-mock` league has no league behind it -- its
    `provider_league_id` IS a draft id -- so warming the team-name cache
    would call `/league/<draft_id>/rosters`, which does not exist and raises
    inside the background task. The warm gate is `provider == "sleeper"`
    precisely, not merely `!= "espn"`."""
    app_mod._STORE.put_credential(
        ProviderCredential("sleeper", "noah", None, None, "t"))
    monkeypatch.setattr(
        "ffdo.ingest.connect.track_mock",
        lambda sleeper, draft_id, username: _tracked(
            league_key="sleeper-mock:D999:2026", provider="sleeper-mock",
            provider_league_id="D999", draft_id="D999", season=2026,
            is_mock=True))

    FakeClient, calls = _recording_client({})
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    client = TestClient(create_app())
    res = client.post("/api/leagues/track", json={
        "provider": "sleeper-mock", "provider_league_id": "D999", "season": 2026})

    assert res.status_code == 200
    # The players/projections warm still happens -- only the team-name fetch
    # is skipped.
    assert any("/players/nfl" in c for c in calls)
    assert not any("/rosters" in c or "/users" in c for c in calls), (
        f"a mock draft has no /league/<id>/rosters to warm -- got {calls}")


# ---------------------------------------------------------------------------
# GET /api/leagues and GET /api/leagues/{league_key}


def test_get_leagues_lists_tracked_with_resolved_format(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(fmt="keeper", format_override="dynasty"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    client = TestClient(create_app())
    row = client.get("/api/leagues").json()[0]

    assert row["format"] == "keeper"
    assert row["resolved_format"] == "dynasty"
    assert row["needs_attention"] is False
    assert row["league_key"] == "sleeper:L123:2025"


def test_get_leagues_is_empty_when_nothing_is_tracked():
    client = TestClient(create_app())
    assert client.get("/api/leagues").json() == []


def test_get_league_returns_the_full_record(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)

    client = TestClient(create_app())
    body = client.get("/api/leagues/sleeper:L123:2025").json()

    assert body["draft_id"] == "D123"
    assert body["format"] == "redraft"
    assert body["resolved_format"] == "redraft"
    assert "fmt" not in body


def test_get_league_404s_for_an_unknown_key():
    client = TestClient(create_app())
    assert client.get("/api/leagues/sleeper:ghost:2025").status_code == 404


# ---------------------------------------------------------------------------
# PATCH / DELETE / refresh


def test_patch_sets_a_format_override(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)

    client = TestClient(create_app())
    res = client.patch("/api/leagues/sleeper:L123:2025",
                       json={"format_override": "dynasty"})

    assert res.status_code == 200
    assert res.json()["resolved_format"] == "dynasty"
    assert store.get("sleeper:L123:2025").format_override == "dynasty"


def test_patch_clears_a_format_override(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(format_override="dynasty"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    client = TestClient(create_app())
    res = client.patch("/api/leagues/sleeper:L123:2025",
                       json={"format_override": None})

    assert res.status_code == 200
    assert store.get("sleeper:L123:2025").format_override is None


def test_patch_rejects_a_bogus_format(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)

    client = TestClient(create_app())
    res = client.patch("/api/leagues/sleeper:L123:2025",
                       json={"format_override": "banana"})

    assert res.status_code == 422
    assert store.get("sleeper:L123:2025").format_override is None


def test_patch_with_an_empty_body_does_not_clear_the_override(monkeypatch, tmp_path):
    """`payload.get("format_override")` returns None for `{}` just as it does
    for an explicit `{"format_override": null}`. Without a presence check
    those two are indistinguishable, so a PATCH carrying an unrelated (or
    empty) body would silently wipe an override the user set deliberately."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(format_override="dynasty"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    client = TestClient(create_app())
    res = client.patch("/api/leagues/sleeper:L123:2025", json={})

    assert res.status_code == 422
    assert store.get("sleeper:L123:2025").format_override == "dynasty", (
        "an empty body must leave the existing override untouched")


def test_patch_with_an_unrelated_body_does_not_clear_the_override(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(format_override="keeper"))
    monkeypatch.setattr(app_mod, "_STORE", store)

    client = TestClient(create_app())
    res = client.patch("/api/leagues/sleeper:L123:2025", json={"name": "Renamed"})

    assert res.status_code == 422
    assert store.get("sleeper:L123:2025").format_override == "keeper"


def test_patch_404s_for_an_unknown_key():
    client = TestClient(create_app())
    res = client.patch("/api/leagues/sleeper:ghost:2025",
                       json={"format_override": "dynasty"})
    assert res.status_code == 404


def test_delete_untracks(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)

    client = TestClient(create_app())

    assert client.delete("/api/leagues/sleeper:L123:2025").status_code == 204
    assert store.get("sleeper:L123:2025") is None


def test_delete_404s_for_an_unknown_key():
    client = TestClient(create_app())
    assert client.delete("/api/leagues/sleeper:ghost:2025").status_code == 404


def test_refresh_re_resolves_and_upserts(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(draft_status="pre_draft", name="Old Name"))
    store.put_credential(ProviderCredential("sleeper", "noah", None, None, "t"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr(
        "ffdo.ingest.connect.track",
        lambda sleeper, league_id, username: _tracked(
            draft_status="drafting", name="New Name"))
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/leagues/sleeper:L123:2025/refresh")

    assert res.status_code == 200
    assert res.json()["name"] == "New Name"
    assert store.get("sleeper:L123:2025").draft_status == "drafting"


def test_refresh_preserves_a_format_override(monkeypatch, tmp_path):
    """`LeagueStore.upsert` deliberately keeps an existing override; the
    refresh endpoint must return the re-read row, not the freshly-resolved
    (override-free) one, or the UI would show the override snapping back."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(format_override="dynasty"))
    store.put_credential(ProviderCredential("sleeper", "noah", None, None, "t"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr("ffdo.ingest.connect.track",
                        lambda sleeper, league_id, username: _tracked())
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/leagues/sleeper:L123:2025/refresh")

    assert res.status_code == 200
    assert res.json()["resolved_format"] == "dynasty"


def test_refresh_404s_for_an_unknown_key():
    client = TestClient(create_app())
    assert client.post("/api/leagues/sleeper:ghost:2025/refresh").status_code == 404


def test_refresh_tells_an_unconnected_user_to_connect_not_to_reconnect(
        monkeypatch, tmp_path):
    """No stored ESPN credential at all is a user who never connected --
    telling them their cookies "look expired -- reconnect ESPN" sends them
    looking for a broken thing that was never there."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(league_key="espn:9:2026", provider="espn",
                          provider_league_id="9", season=2026))
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/leagues/espn:9:2026/refresh")

    assert res.status_code == 400
    assert res.json()["detail"] == "Connect ESPN before refreshing a league"


def test_refresh_reports_expired_cookies_when_a_credential_exists_but_is_hollow(
        monkeypatch, tmp_path):
    """The other half of the distinction: a credential row IS stored, but
    carries no usable cookies (as the legacy session.json migration can
    produce). That genuinely is a stale connection -- "reconnect" is right."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(league_key="espn:9:2026", provider="espn",
                          provider_league_id="9", season=2026))
    store.put_credential(ProviderCredential("espn", "{SWID}", None, None, "t"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/leagues/espn:9:2026/refresh")

    assert res.status_code == 400
    assert "expired" in res.json()["detail"]


def test_track_tells_an_unconnected_user_to_connect_espn(monkeypatch):
    """Mirrors the Sleeper branch's "Connect Sleeper before tracking a
    league" -- before this, the ESPN path fell through to the expired-cookies
    message for a user who had simply never connected."""
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    res = client.post("/api/leagues/track", json={
        "provider": "espn", "provider_league_id": "9", "season": 2026})

    assert res.status_code == 400
    assert res.json()["detail"] == "Connect ESPN before tracking a league"


# ---------------------------------------------------------------------------
# GET /api/leagues/{league_key}/readiness


def test_readiness_404s_for_an_unknown_league():
    client = TestClient(create_app())
    assert client.get("/api/leagues/sleeper:ghost:2025/readiness").status_code == 404


def test_readiness_reports_pending_before_anything_is_warmed(monkeypatch, tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    monkeypatch.setattr(app_mod, "_STORE", store)

    client = TestClient(create_app())
    res = client.get("/api/leagues/sleeper:L123:2025/readiness")

    assert res.json() == {"league_draft": "synced", "players": "pending",
                          "projections": "pending"}


def test_readiness_is_per_season_not_global(monkeypatch, tmp_path):
    """The crux of the season-keyed projections cache: tracking a 2026 league
    warms 2026's cache; a separately-tracked 2025 league must still report
    `projections: pending` rather than reading `synced` off 2026's entry."""
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())  # season 2025
    store.put_credential(ProviderCredential("sleeper", "noah", None, None, "t"))
    monkeypatch.setattr(app_mod, "_STORE", store)
    monkeypatch.setattr(
        "ffdo.ingest.connect.track",
        lambda sleeper, league_id, username: _tracked(
            league_key="sleeper:L9:2026", provider_league_id="L9", season=2026))
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    client = TestClient(create_app())
    client.post("/api/leagues/track", json={
        "provider": "sleeper", "provider_league_id": "L9", "season": 2026})

    assert client.get("/api/leagues/sleeper:L9:2026/readiness").json() == {
        "league_draft": "synced", "players": "synced", "projections": "synced"}
    assert client.get("/api/leagues/sleeper:L123:2025/readiness").json()[
        "projections"] == "pending"


# ---------------------------------------------------------------------------
# Removed endpoints


def test_the_old_single_league_endpoints_are_gone():
    """`POST /api/connect`, `GET /api/session`, and the unscoped board routes
    are replaced by their league-scoped equivalents. A stale frontend hitting
    them must fail loudly, not silently act on some implicit "current"
    league."""
    client = TestClient(create_app())
    # Unmatched paths fall through to the static mount at "/", which answers
    # 404 for a GET of a nonexistent file and 405 for a non-GET method --
    # either way, no API handler is reached.
    assert client.post("/api/connect", json={}).status_code in (404, 405)
    assert client.get("/api/session").status_code == 404
    assert client.get("/api/board").status_code == 404
    assert client.get("/api/readiness").status_code == 404


# ---------------------------------------------------------------------------
# _uncached / _TTLCache / _active_only -- pure helpers, no store involvement


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
    lazily, is what fixes that."""
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

    again_2025 = cache_for(2025).get(lambda: calls_2025.append(1) or "SHOULD-NOT-RUN")
    assert again_2025 == "proj-2025"
    assert len(calls_2025) == 1
    assert len(calls_2026) == 1


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


# ---------------------------------------------------------------------------
# GET /api/leagues/{league_key}/board and /board/live -- integration tests
# through TestClient + a real LeagueStore + a recording client at the network
# seam. Ported from the old unscoped `GET /api/board` / `/api/board/live`
# suite (git: f2f030e:tests/api/test_app.py), which Task 10's rewrite deleted
# wholesale. Each old `_session(...)` becomes a `_tracked(...)` row in
# `_STORE` (the autouse `_isolated_store` fixture gives every test a fresh
# tmp_path store) plus a GET of the league-scoped URL.

_PROJ_TS_2025 = int(datetime(2025, 8, 1, tzinfo=timezone.utc).timestamp() * 1000)

_BOARD_REAL_LEAGUE_RAW = {
    "league_id": "L123", "season": "2025",
    "settings": {"num_teams": 2, "budget": 200},
    "roster_positions": ["QB", "RB", "BN"],
    "scoring_settings": {"rush_yd": 0.1, "rush_td": 6.0},
    "name": "Board Test League", "status": "drafting",
}
_BOARD_DRAFT_RAW = {
    "draft_id": "D123", "type": "auction", "status": "drafting",
    "settings": {"teams": 2, "rounds": 3, "budget": 200},
}
_BOARD_PLAYERS_RAW = {
    "P1": {"first_name": "Test", "last_name": "Runner", "position": "RB",
           "team": "AAA", "age": 25, "years_exp": 3, "active": True},
}
_BOARD_PROJECTIONS_RAW = [
    {"player_id": "P1", "last_modified": _PROJ_TS_2025,
     "stats": {"rush_yd": 1000.0, "rush_td": 10.0}},
]

# Real captured /v1/draft/<id> shape (see MOCK_DRAFT_PRE_DRAFT /
# MOCK_DRAFT_MID_DRAFT in tests/ingest/test_mock_draft.py), an auction-type
# mock so the response carries a `budget` section with a `your_roster_id`
# to assert on.
_BOARD_MOCK_DRAFT_RAW = {
    "created": 1787468015451, "creators": ["U1"], "draft_id": "D999",
    "draft_order": None, "league_id": None,
    "metadata": {"description": "", "name": "", "scoring_type": "half_ppr"},
    "season": "2026", "season_type": "regular",
    "settings": {
        "autostart": 0, "cpu_autopick": 1, "pick_timer": 120, "rounds": 3,
        "slots_qb": 1, "slots_rb": 1, "slots_bn": 1, "teams": 2, "budget": 200,
    },
    "slot_to_roster_id": {"1": 1, "2": 2},
    "sport": "nfl", "start_time": None, "status": "drafting", "type": "auction",
}


def _mock_tracked(**overrides):
    base = dict(
        league_key="sleeper-mock:D999:2026", provider="sleeper-mock",
        provider_league_id="D999", draft_id="D999", season=2026,
        is_mock=True, user_id="U1", roster_id=None, draft_type="auction",
        roster_positions=("QB", "RB", "BN"),
    )
    return _tracked(**{**base, **overrides})


# -- happy paths ------------------------------------------------------------


def test_board_endpoint_scores_a_player_for_a_tracked_sleeper_league(monkeypatch):
    app_mod._STORE.upsert(_tracked())  # sleeper:L123:2025

    FakeClient, calls = _recording_client({
        f"{V1}/draft/D123/picks": [],
        f"{V1}/draft/D123": _BOARD_DRAFT_RAW,
        f"{V1}/league/L123/rosters": [],
        f"{V1}/league/L123/users": [],
        f"{V1}/league/L123": _BOARD_REAL_LEAGUE_RAW,
        f"{V1}/players/nfl": _BOARD_PLAYERS_RAW,
        f"{PROJECTIONS}/2025": _BOARD_PROJECTIONS_RAW,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    res = TestClient(create_app()).get("/api/leagues/sleeper:L123:2025/board")

    assert res.status_code == 200
    body = res.json()
    assert body["is_mock"] is False
    assert f"{V1}/league/L123" in calls
    assert "P1" in {p["player_id"] for p in body["players"]}, (
        "the one scoreable player in the fixture must reach the response -- "
        "proves players/projections still flow through the real-league branch")


def test_board_endpoint_404s_for_an_untracked_league():
    assert TestClient(create_app()).get(
        "/api/leagues/sleeper:ghost:2025/board").status_code == 404


def test_board_live_404s_for_an_untracked_league():
    assert TestClient(create_app()).get(
        "/api/leagues/sleeper:ghost:2025/board/live").status_code == 404


def test_board_live_returns_nomination_without_the_heavy_fetches(monkeypatch):
    """The point of this endpoint: answer using only the two Sleeper calls
    that carry nomination/bid (draft meta + picks), never touching
    /league/<id>, /players/nfl, or the projections feed."""
    app_mod._STORE.upsert(_tracked())

    draft_meta = {**_BOARD_DRAFT_RAW,
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

    res = TestClient(create_app()).get("/api/leagues/sleeper:L123:2025/board/live")

    assert res.status_code == 200
    assert res.json() == {"live_nomination": {"player_id": "P1", "bid": 42},
                          "picks_made": 1}
    assert not any("/league/" in c or "/players/" in c or "/projections/" in c
                   for c in calls), f"only draft meta + picks may be fetched: {calls}"


# -- (c) mock single-fetch -----------------------------------------------------


def test_board_mock_mode_reports_is_mock_true_and_fetches_the_draft_once(monkeypatch):
    """get_board()'s mock branch must fetch /draft/<id> exactly once and
    reuse that payload for both build_league_profile() and draft_meta."""
    app_mod._STORE.upsert(_mock_tracked())

    FakeClient, calls = _recording_client({
        f"{V1}/draft/D999/picks": [],
        f"{V1}/draft/D999": _BOARD_MOCK_DRAFT_RAW,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    res = TestClient(create_app()).get("/api/leagues/sleeper-mock:D999:2026/board")

    assert res.status_code == 200
    assert res.json()["is_mock"] is True
    draft_meta_calls = [c for c in calls if c.split("?", 1)[0] == f"{V1}/draft/D999"]
    assert len(draft_meta_calls) == 1, (
        f"expected exactly one fetch of /draft/D999 (single-fetch reuse for "
        f"both LeagueProfile and draft_meta), got {len(draft_meta_calls)}: {calls}")
    assert any(c.split("?", 1)[0] == f"{V1}/draft/D999/picks" for c in calls)


# -- (d) mock backfill + live roster_id --------------------------------------


def test_board_mock_mode_backfills_picks_and_resolves_roster_id_live(monkeypatch):
    """Two guards together:

    1. Live roster_id: the tracked league's roster_id is None, but THIS
       poll's draft object has draft_order[user_id] = 2. get_board() must
       re-resolve roster_id from that live draft_meta, never trust the
       stored None.
    2. backfill_roster_ids() runs BEFORE draft.parse() sees the picks:
       Sleeper never populates roster_id on a mock pick (arrives null even
       for the human's own). The pick below has roster_id: null,
       draft_slot: 2 -> roster_id 2 via slot_to_roster_id. Without backfill
       (or run after parsing) spent_by_roster() skips it and your_spent is 0.
    """
    app_mod._STORE.upsert(_mock_tracked(roster_id=None))

    draft_meta = {**_BOARD_MOCK_DRAFT_RAW, "draft_order": {"U1": 2}}
    picks_raw = [{
        "draft_id": "D999", "draft_slot": 2, "pick_no": 1, "picked_by": "U1",
        "player_id": "P1", "roster_id": None, "round": 1,
        "metadata": {"amount": "50"},
    }]
    FakeClient, _ = _recording_client({
        f"{V1}/draft/D999/picks": picks_raw,
        f"{V1}/draft/D999": draft_meta,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    res = TestClient(create_app()).get("/api/leagues/sleeper-mock:D999:2026/board")

    assert res.status_code == 200
    body = res.json()
    assert body["budget"]["your_roster_id"] == 2, (
        "must reflect the LIVE roster_id resolved from this poll's draft_order, "
        "not the stale stored roster_id (None)")
    assert body["budget"]["your_spent"] == 50, (
        "the pick's null roster_id must have been backfilled to 2 before "
        "draft.parse() ran, so spent_by_roster() counts its $50 toward roster 2")


# -- (e) mock scoring_type drift -> clean 400 -------------------------------


def test_board_mock_mode_returns_a_clean_400_when_scoring_type_becomes_unsupported(
        monkeypatch):
    """build_league_profile() runs on every 3s poll. A mock draft's
    scoring_type can drift to an unsupported value after the user has
    connected -- that must surface as a clean 400 with the bad value in
    `detail`, not a bare 500."""
    app_mod._STORE.upsert(_mock_tracked())

    drifted = {**_BOARD_MOCK_DRAFT_RAW,
               "metadata": {**_BOARD_MOCK_DRAFT_RAW["metadata"],
                            "scoring_type": "dynasty_2qb"}}
    FakeClient, _ = _recording_client({
        f"{V1}/draft/D999/picks": [],
        f"{V1}/draft/D999": drifted,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    res = TestClient(create_app()).get("/api/leagues/sleeper-mock:D999:2026/board")

    assert res.status_code == 400
    assert "dynasty_2qb" in res.json()["detail"]


def test_board_mock_mode_handles_a_snake_draft_without_crashing(monkeypatch):
    """The auction fixtures never exercise the snake mock path end to end
    through the real endpoint (a snake mock's budget is legitimately None)."""
    app_mod._STORE.upsert(_mock_tracked(draft_type="snake"))

    snake_draft = {**_BOARD_MOCK_DRAFT_RAW, "type": "snake"}
    FakeClient, _ = _recording_client({
        f"{V1}/draft/D999/picks": [],
        f"{V1}/draft/D999": snake_draft,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    res = TestClient(create_app()).get("/api/leagues/sleeper-mock:D999:2026/board")

    assert res.status_code == 200
    body = res.json()
    assert body["format"] == "snake"
    assert body["is_mock"] is True


# -- (b) DEF/K in the projections request -----------------------------------


def test_board_fetches_and_scores_def_and_k_when_the_league_rosters_them(monkeypatch):
    """Regression (commit 89c8f56): `_load_projections`'s query only asked
    Sleeper for QB/RB/WR/TE, so DEF/K never reached `proj` -- silently
    invisible on the board for leagues that roster them, no crash."""
    app_mod._STORE.upsert(_tracked(
        league_key="sleeper:L456:2025", provider_league_id="L456",
        draft_id="D456", roster_positions=("QB", "DEF", "K", "BN")))

    league_raw = {
        "league_id": "L456", "season": "2025",
        "settings": {"num_teams": 2, "budget": 200},
        "roster_positions": ["QB", "DEF", "K", "BN"],
        "scoring_settings": {"pass_yd": 0.04, "sack": 1.0, "int": 2.0,
                             "fgm": 3.0, "xpm": 1.0},
        "name": "DEF/K Board Test League", "status": "drafting",
    }
    draft_raw = {"draft_id": "D456", "type": "auction", "status": "drafting",
                 "settings": {"teams": 2, "rounds": 3, "budget": 200}}
    players_raw = {
        "DAL": {"first_name": "Dallas", "last_name": "Defense", "position": "DEF",
                "team": "DAL", "age": None, "years_exp": None, "active": True},
        "K1": {"first_name": "Test", "last_name": "Kicker", "position": "K",
               "team": "AAA", "age": 28, "years_exp": 5, "active": True},
    }
    projections_raw = [
        {"player_id": "DAL", "last_modified": _PROJ_TS_2025,
         "stats": {"sack": 3.0, "int": 2.0}},
        {"player_id": "K1", "last_modified": _PROJ_TS_2025,
         "stats": {"fgm": 2.0, "xpm": 3.0}},
    ]
    FakeClient, calls = _recording_client({
        f"{V1}/draft/D456/picks": [],
        f"{V1}/draft/D456": draft_raw,
        f"{V1}/league/L456/rosters": [],
        f"{V1}/league/L456/users": [],
        f"{V1}/league/L456": league_raw,
        f"{V1}/players/nfl": players_raw,
        f"{PROJECTIONS}/2025": projections_raw,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    res = TestClient(create_app()).get("/api/leagues/sleeper:L456:2025/board")

    assert res.status_code == 200
    projections_calls = [c for c in calls if f"{PROJECTIONS}/2025" in c]
    assert projections_calls, "must actually fetch the projections feed"
    assert "position[]=DEF" in projections_calls[0], (
        "the projections request must ask Sleeper for DEF explicitly")
    assert "position[]=K" in projections_calls[0], (
        "the projections request must ask Sleeper for K explicitly")

    player_ids = {p["player_id"] for p in res.json()["players"]}
    assert "DAL" in player_ids, "a rostered DEF with real projections must reach the board"
    assert "K1" in player_ids, "a rostered K with real projections must reach the board"


# -- (a) ESPN board never calls Sleeper's /draft/ endpoint ------------------


_ESPN_CRED = ProviderCredential("espn", "{SWID}", "s2value", "{SWID}", "t")

_ESPN_BOARD_LIVE_RAW = {
    "id": 1882997948,
    "settings": {"size": 2, "draftSettings": {"type": "SNAKE"}},
    "draftDetail": {"drafted": False, "inProgress": True, "picks": []},
}

_ESPN_BOARD_RAW = {
    "id": 1882997948, "seasonId": 2026,
    "settings": {
        "size": 2, "name": "ESPN Board Test",
        "draftSettings": {"type": "SNAKE"},
        "rosterSettings": {"lineupSlotCounts": {"0": 1, "2": 1, "4": 1, "20": 3}},
        "scoringSettings": {"scoringItems": [
            {"statId": 24, "points": 0.1}, {"statId": 25, "points": 6.0},
            {"statId": 53, "points": 1.0},
        ]},
    },
    "draftDetail": {"drafted": False, "inProgress": True, "picks": []},
}


def _espn_tracked():
    return _tracked(
        league_key="espn:1882997948:2026", provider="espn",
        provider_league_id="1882997948", season=2026, draft_id="1882997948",
        draft_type="snake", roster_positions=("QB", "RB", "WR", "BN", "BN", "BN"))


class _NoDraftFetchSleeperClient(_FakeSleeperClient):
    """Fails loudly if any Sleeper `/draft/` URL is fetched -- the exact
    live-production 500 (commit 328ace4): an ESPN league id sent to
    Sleeper's /draft/<id> 404s and 500s the endpoint on every poll."""

    calls: list[str] = []

    def get_json(self, url: str):
        _NoDraftFetchSleeperClient.calls.append(url)
        assert "/draft/" not in url, (
            f"must never fetch a Sleeper /draft/ URL for an ESPN league -- got {url}")
        return super().get_json(url)


def test_board_live_espn_never_calls_sleepers_draft_endpoint(monkeypatch):
    app_mod._STORE.upsert(_espn_tracked())
    app_mod._STORE.put_credential(_ESPN_CRED)

    FakeEspn, espn_calls = _recording_espn_client({
        "players?view=kona_player_info": [],
        "leagues/1882997948": _ESPN_BOARD_LIVE_RAW,
    })
    monkeypatch.setattr("ffdo.ingest.espn.client.EspnClient", FakeEspn)
    _NoDraftFetchSleeperClient.calls = []
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _NoDraftFetchSleeperClient)

    res = TestClient(create_app()).get("/api/leagues/espn:1882997948:2026/board/live")

    assert res.status_code == 200
    assert res.json() == {"live_nomination": None, "picks_made": 0}
    assert any("leagues/1882997948" in c for c in espn_calls), (
        "must actually fetch ESPN's own draft-detail endpoint")
    assert not any("/draft/" in c for c in _NoDraftFetchSleeperClient.calls)


def test_board_espn_never_calls_sleepers_draft_endpoint(monkeypatch):
    """Same guard for the heavier /board endpoint -- its ESPN branch must
    likewise never route an ESPN league id to Sleeper's /draft/<id>."""
    app_mod._STORE.upsert(_espn_tracked())
    app_mod._STORE.put_credential(_ESPN_CRED)

    FakeEspn, espn_calls = _recording_espn_client({
        "players?view=kona_player_info": [],
        "leagues/1882997948": _ESPN_BOARD_RAW,
    })
    monkeypatch.setattr("ffdo.ingest.espn.client.EspnClient", FakeEspn)
    _NoDraftFetchSleeperClient.calls = []
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _NoDraftFetchSleeperClient)

    res = TestClient(create_app()).get("/api/leagues/espn:1882997948:2026/board")

    assert res.status_code == 200
    assert res.json()["is_mock"] is False
    assert any("leagues/1882997948" in c for c in espn_calls)
    assert not any("/draft/" in c for c in _NoDraftFetchSleeperClient.calls)


def test_board_live_espn_reads_credentials_from_the_store(monkeypatch):
    """The ESPN branch reads espn_s2/swid from `_STORE.get_credential`, not
    from the request or a session object."""
    app_mod._STORE.upsert(_espn_tracked())
    app_mod._STORE.put_credential(_ESPN_CRED)

    captured_creds: list[tuple] = []

    FakeEspn, espn_calls = _recording_espn_client({
        "players?view=kona_player_info": [],
        "leagues/1882997948": _ESPN_BOARD_LIVE_RAW,
    })

    class _CredCapturingEspnClient(FakeEspn):
        def __init__(self, *args, **kwargs):
            captured_creds.append(args)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("ffdo.ingest.espn.client.EspnClient", _CredCapturingEspnClient)
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    res = TestClient(create_app()).get("/api/leagues/espn:1882997948:2026/board/live")

    assert res.status_code == 200
    assert res.json() == {"live_nomination": None, "picks_made": 0}
    assert ("s2value", "{SWID}") in captured_creds, (
        f"EspnClient must be constructed with the stored cookies -- got {captured_creds}")


def test_board_live_espn_400s_when_no_credential_is_stored(monkeypatch):
    app_mod._STORE.upsert(_espn_tracked())  # no put_credential
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", _FakeSleeperClient)

    res = TestClient(create_app()).get("/api/leagues/espn:1882997948:2026/board/live")
    assert res.status_code == 400
    assert "Connect ESPN" in res.json()["detail"]


# -- (f) touch_status through an endpoint ----------------------------------


def test_board_poll_updates_the_stored_draft_status(monkeypatch):
    """After a /board poll returns a draft state with status "complete",
    the stored TrackedLeague.draft_status for that league_key is "complete"
    (get_board() calls `_STORE.touch_status(league_key, state.status)`)."""
    app_mod._STORE.upsert(_tracked(draft_status="drafting"))

    complete_draft = {**_BOARD_DRAFT_RAW, "status": "complete"}
    FakeClient, _ = _recording_client({
        f"{V1}/draft/D123/picks": [],
        f"{V1}/draft/D123": complete_draft,
        f"{V1}/league/L123/rosters": [],
        f"{V1}/league/L123/users": [],
        f"{V1}/league/L123": {**_BOARD_REAL_LEAGUE_RAW, "status": "complete"},
        f"{V1}/players/nfl": _BOARD_PLAYERS_RAW,
        f"{PROJECTIONS}/2025": _BOARD_PROJECTIONS_RAW,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    TestClient(create_app()).get("/api/leagues/sleeper:L123:2025/board")

    assert app_mod._STORE.get("sleeper:L123:2025").draft_status == "complete"


def test_board_live_poll_updates_the_stored_draft_status(monkeypatch):
    """get_board_live() calls touch_status too -- every 1s poll is also the
    freshest signal about whether the draft is still running."""
    app_mod._STORE.upsert(_tracked(draft_status="drafting"))

    complete_draft = {**_BOARD_DRAFT_RAW, "status": "complete"}
    FakeClient, _ = _recording_client({
        f"{V1}/draft/D123/picks": [],
        f"{V1}/draft/D123": complete_draft,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    TestClient(create_app()).get("/api/leagues/sleeper:L123:2025/board/live")

    assert app_mod._STORE.get("sleeper:L123:2025").draft_status == "complete"


# -- (g) cross-league scoring regression guard ----------------------------


def test_two_leagues_with_different_scoring_produce_different_vor(monkeypatch):
    """The single most important property of the multi-league foundation:
    two tracked leagues with different scoring_settings must produce
    DIFFERENT vor for the same player's stat line -- no scoring bleed
    between leagues served by the same process."""
    app_mod._STORE.upsert(_tracked(
        league_key="sleeper:PPR:2025", provider_league_id="PPR", draft_id="DPPR",
        scoring_settings={"rush_yd": 0.1, "rec": 1.0}))
    app_mod._STORE.upsert(_tracked(
        league_key="sleeper:STD:2025", provider_league_id="STD", draft_id="DSTD",
        scoring_settings={"rush_yd": 0.1, "rec": 0.0}))

    players_raw = {
        "P1": {"first_name": "Top", "last_name": "Back", "position": "RB",
               "team": "AAA", "age": 25, "years_exp": 3, "active": True},
        "P2": {"first_name": "Mid", "last_name": "Back", "position": "RB",
               "team": "BBB", "age": 26, "years_exp": 4, "active": True},
        "P3": {"first_name": "Repl", "last_name": "Back", "position": "RB",
               "team": "CCC", "age": 27, "years_exp": 5, "active": True},
    }
    projections_raw = [
        {"player_id": "P1", "last_modified": _PROJ_TS_2025,
         "stats": {"rush_yd": 1000.0, "rec": 80.0}},
        {"player_id": "P2", "last_modified": _PROJ_TS_2025,
         "stats": {"rush_yd": 500.0, "rec": 40.0}},
        {"player_id": "P3", "last_modified": _PROJ_TS_2025,
         "stats": {"rush_yd": 100.0, "rec": 10.0}},
    ]

    def _league_raw(lid, scoring):
        return {"league_id": lid, "season": "2025",
                "settings": {"num_teams": 2, "budget": 200},
                "roster_positions": ["QB", "RB", "BN"],
                "scoring_settings": scoring, "name": lid, "status": "drafting"}

    def _draft_raw(did):
        return {"draft_id": did, "type": "auction", "status": "drafting",
                "settings": {"teams": 2, "rounds": 3, "budget": 200}}

    FakeClient, _ = _recording_client({
        f"{V1}/draft/DPPR/picks": [], f"{V1}/draft/DPPR": _draft_raw("DPPR"),
        f"{V1}/draft/DSTD/picks": [], f"{V1}/draft/DSTD": _draft_raw("DSTD"),
        f"{V1}/league/PPR/rosters": [], f"{V1}/league/PPR/users": [],
        f"{V1}/league/PPR": _league_raw("PPR", {"rush_yd": 0.1, "rec": 1.0}),
        f"{V1}/league/STD/rosters": [], f"{V1}/league/STD/users": [],
        f"{V1}/league/STD": _league_raw("STD", {"rush_yd": 0.1, "rec": 0.0}),
        f"{V1}/players/nfl": players_raw,
        f"{PROJECTIONS}/2025": projections_raw,
    })
    monkeypatch.setattr("ffdo.ingest.client.SleeperClient", FakeClient)

    client = TestClient(create_app())
    ppr = client.get("/api/leagues/sleeper:PPR:2025/board").json()
    std = client.get("/api/leagues/sleeper:STD:2025/board").json()

    def _vor(body, pid):
        return next(p["vor"] for p in body["players"] if p["player_id"] == pid)

    # Full-PPR: P1 = 100 + 80 = 180, replacement (P3) = 10 + 10 = 20 -> vor 160.
    # Standard: P1 = 100, replacement (P3) = 10 -> vor 90.
    assert _vor(ppr, "P1") == 160.0
    assert _vor(std, "P1") == 90.0
    assert _vor(ppr, "P1") != _vor(std, "P1"), (
        "same player, same stat line, different league scoring -> different VOR")
    assert _vor(ppr, "P2") != _vor(std, "P2")
