from datetime import datetime, timezone

import httpx
import pytest

from ffdo.ingest import snapshot
from ffdo.ingest.espn import connect

ESPN_SNAPSHOT_DIR = snapshot.DEFAULT_SNAPSHOT_DIR.parent / "2026-08-23-espn-league"
YOUR_SWID = "{00000004-0000-0000-0000-000000000000}"


def _combined_league_response():
    """The real API returns one merged object when multiple `view=` params
    are requested together; these fixtures were captured as three separate
    single-view requests during design validation, so recombine them here
    to match connect.py's actual combined-view request shape.

    Each view populates a DIFFERENT slice of the same top-level object --
    mSettings' `settings` is the full object (includes `size`,
    `rosterSettings`, `scoringSettings`, ...), while mDraftDetail's
    `settings` is a slimmer one (only `draftSettings`). A naive
    `{**a, **b, **c}` spread would let whichever dict is merged last win on
    every shared key, silently replacing the full `settings` with the slim
    one and losing `size` (confirmed live while implementing this task's
    real counterpart, Task 7 -- the exact same bug, caught there first).
    Take `settings_raw` as the base and layer in only the fields the other
    two views uniquely contribute."""
    settings_raw = snapshot.load("mSettings", snapshot_dir=ESPN_SNAPSHOT_DIR)
    team_raw = snapshot.load("mTeam", snapshot_dir=ESPN_SNAPSHOT_DIR)
    draft_raw = snapshot.load("mDraftDetail", snapshot_dir=ESPN_SNAPSHOT_DIR)
    return {
        **settings_raw,
        "teams": team_raw["teams"],
        "members": team_raw["members"],
        "draftDetail": draft_raw["draftDetail"],
    }


def _dst_pool():
    return snapshot.load("espnPlayersDst", snapshot_dir=ESPN_SNAPSHOT_DIR)


def _handler(league_response=None):
    league_response = league_response or _combined_league_response()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "players?view=kona_player_info" in url:
            return httpx.Response(200, json=_dst_pool())
        if "leagues/1882997948" in url:
            return httpx.Response(200, json=league_response)
        raise AssertionError(f"unexpected URL: {url}")
    return handler


def test_track_returns_a_fully_populated_tracked_league_for_the_real_pre_draft_league():
    lg = connect.track(
        "1882997948", 2026, "s2value", YOUR_SWID,
        profiles={}, espn_id_index={},
        transport=httpx.MockTransport(_handler()),
        now=lambda: datetime(2026, 8, 23, tzinfo=timezone.utc))

    assert lg.provider == "espn"
    assert lg.league_key == "espn:1882997948:2026"
    assert lg.provider_league_id == "1882997948"
    assert lg.draft_id == "1882997948"
    assert lg.roster_id == 7
    assert lg.name == "Pigskin Pricing Experts"
    assert lg.num_teams == 10
    assert lg.draft_type == "snake"
    assert lg.draft_status == "pre_draft"
    assert lg.rounds == 15
    assert lg.fmt in ("redraft", "keeper")
    assert lg.format_override is None
    assert lg.is_mock is False
    assert lg.tracked_at == "2026-08-23T00:00:00+00:00"
    assert lg.last_refreshed_at == "2026-08-23T00:00:00+00:00"
    assert not hasattr(lg, "espn_s2")
    assert not hasattr(lg, "swid")


def test_track_normalizes_a_swid_pasted_without_braces():
    bare_swid = YOUR_SWID.strip("{}")
    lg = connect.track(
        "1882997948", 2026, "s2value", bare_swid,
        profiles={}, espn_id_index={},
        transport=httpx.MockTransport(_handler()))
    assert lg.roster_id == 7
    assert lg.user_id == YOUR_SWID


def test_track_raises_when_the_league_is_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(connect.ConnectError, match="League not found"):
        connect.track("bad-league", 2026, "s2", YOUR_SWID, {}, {},
                      transport=httpx.MockTransport(handler))


def test_track_raises_when_the_draft_type_is_not_snake():
    raw = _combined_league_response()
    auction_raw = {
        **raw,
        "settings": {**raw["settings"], "draftSettings":
                    {**raw["settings"]["draftSettings"], "type": "AUCTION"}},
    }
    with pytest.raises(connect.ConnectError, match="auction"):
        connect.track("1882997948", 2026, "s2", YOUR_SWID, {}, {},
                      transport=httpx.MockTransport(_handler(auction_raw)))


def test_track_raises_when_the_swid_matches_no_team():
    unknown_swid = "{ffffffff-0000-0000-0000-000000000000}"
    with pytest.raises(connect.ConnectError, match="not a member"):
        connect.track("1882997948", 2026, "s2", unknown_swid, {}, {},
                      transport=httpx.MockTransport(_handler()))


def test_track_raises_a_cookie_expired_message_on_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(connect.ConnectError, match="cookies look expired"):
        connect.track("1882997948", 2026, "s2", YOUR_SWID, {}, {},
                      transport=httpx.MockTransport(handler))


def test_track_raises_when_the_player_pool_fetch_fails(monkeypatch):
    monkeypatch.setattr("ffdo.ingest.espn.client.time.sleep", lambda *_a, **_kw: None)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "players?view=kona_player_info" in url:
            return httpx.Response(500, json={"error": "server error"})
        if "leagues/1882997948" in url:
            return httpx.Response(200, json=_combined_league_response())
        raise AssertionError(f"unexpected URL: {url}")

    with pytest.raises(connect.ConnectError, match="player pool"):
        connect.track("1882997948", 2026, "s2", YOUR_SWID, {}, {},
                      transport=httpx.MockTransport(handler))


def test_normalize_swid_adds_missing_braces():
    assert connect.normalize_swid("ABCD-1234") == "{ABCD-1234}"
    assert connect.normalize_swid("{ABCD-1234}") == "{ABCD-1234}"
