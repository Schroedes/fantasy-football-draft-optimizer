import json as _json

import pytest

from ffdo.api.store import LeagueStore
from ffdo.domain.models import ProviderCredential, TrackedLeague


def _tracked(**overrides):
    base = dict(
        league_key="sleeper:L1:2026", provider="sleeper", provider_league_id="L1",
        season=2026, name="Test League", user_id="U1", roster_id=3,
        draft_id="D1", draft_type="auction", draft_status="pre_draft",
        num_teams=12, budget=200, rounds=13,
        roster_positions=("QB", "RB", "BN"), scoring_settings={"rec": 0.5},
        fmt="redraft", format_override=None, raw_settings={"type": 0}, is_mock=False,
        tracked_at="2026-09-02T00:00:00+00:00",
        last_refreshed_at="2026-09-02T00:00:00+00:00",
    )
    return TrackedLeague(**{**base, **overrides})


def test_upsert_then_get_round_trips_every_field(tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    lg = _tracked()
    store.upsert(lg)
    got = store.get("sleeper:L1:2026")
    assert got == lg


def test_get_returns_none_for_an_unknown_key(tmp_path):
    assert LeagueStore(tmp_path / "ffdo.db").get("nope:x:2026") is None


def test_list_is_ordered_by_tracked_at(tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(league_key="sleeper:B:2026", provider_league_id="B",
                          tracked_at="2026-09-02T02:00:00+00:00"))
    store.upsert(_tracked(league_key="sleeper:A:2026", provider_league_id="A",
                          tracked_at="2026-09-02T01:00:00+00:00"))
    assert [l.provider_league_id for l in store.list()] == ["A", "B"]


def test_upsert_preserves_an_existing_format_override_and_tracked_at(tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(tracked_at="2026-09-02T01:00:00+00:00"))
    store.set_format_override("sleeper:L1:2026", "dynasty")
    # A later refresh re-upserts with fresh provider data but no override:
    store.upsert(_tracked(name="Renamed", tracked_at="2026-09-09T09:00:00+00:00"))
    got = store.get("sleeper:L1:2026")
    assert got.name == "Renamed"
    assert got.format_override == "dynasty"
    assert got.tracked_at == "2026-09-02T01:00:00+00:00"


def test_delete_removes_the_row(tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked())
    store.delete("sleeper:L1:2026")
    assert store.get("sleeper:L1:2026") is None


def test_touch_status_updates_status_and_refreshed_at(tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.upsert(_tracked(draft_status="drafting"))
    store.touch_status("sleeper:L1:2026", "complete")
    got = store.get("sleeper:L1:2026")
    assert got.draft_status == "complete"
    assert got.last_refreshed_at != "2026-09-02T00:00:00+00:00"


def test_touch_status_is_a_noop_for_an_unknown_key(tmp_path):
    LeagueStore(tmp_path / "ffdo.db").touch_status("nope:x:2026", "complete")  # no raise


def test_credentials_round_trip_and_replace_on_provider(tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db")
    store.put_credential(ProviderCredential("espn", "{SWID}", "s2a", "{SWID}", "t1"))
    store.put_credential(ProviderCredential("espn", "{SWID}", "s2b", "{SWID}", "t2"))
    got = store.get_credential("espn")
    assert got.espn_s2 == "s2b"
    assert store.get_credential("sleeper") is None


def test_a_corrupt_db_file_reads_as_empty_not_an_exception(tmp_path):
    p = tmp_path / "ffdo.db"
    p.write_text("this is not sqlite", encoding="utf-8")
    store = LeagueStore(p)
    assert store.list() == []
    assert store.get("sleeper:L1:2026") is None


_LEGACY_SESSION = {
    "username": "noahdschroeder", "user_id": "U1", "league_id": "1315881559957458944",
    "draft_id": "1315881559965835264", "roster_id": 7, "league_name": "P-Vegas Ballers",
    "season": 2026, "num_teams": 12, "budget": 200,
    "roster_positions": ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                         "BN", "BN", "BN", "BN", "BN"],
    "scoring_settings": {"rec": 0.5}, "draft_type": "auction",
    "draft_status": "pre_draft", "rounds": 13,
    "connected_at": "2026-08-24T00:00:00+00:00", "is_mock": False,
    "provider": "sleeper", "espn_s2": None, "swid": None,
}


def test_migration_imports_a_legacy_session_once(tmp_path):
    legacy = tmp_path / "session.json"
    legacy.write_text(_json.dumps(_LEGACY_SESSION), encoding="utf-8")

    store = LeagueStore(tmp_path / "ffdo.db", legacy_session_path=legacy)
    leagues = store.list()

    assert len(leagues) == 1
    lg = leagues[0]
    assert lg.league_key == "sleeper:1315881559957458944:2026"
    assert lg.name == "P-Vegas Ballers"
    assert lg.roster_id == 7
    assert lg.draft_type == "auction"
    assert lg.fmt == "redraft"
    assert not legacy.exists()
    assert (tmp_path / "session.json.migrated").exists()


def test_migration_is_idempotent_and_skipped_when_leagues_exist(tmp_path):
    legacy = tmp_path / "session.json"
    legacy.write_text(_json.dumps(_LEGACY_SESSION), encoding="utf-8")
    db = tmp_path / "ffdo.db"

    LeagueStore(db, legacy_session_path=legacy).list()
    # Second construction: legacy file is gone, nothing re-imported, no crash.
    store2 = LeagueStore(db, legacy_session_path=legacy)
    assert len(store2.list()) == 1


def test_migration_does_not_crash_when_a_migrated_file_already_exists(tmp_path):
    """`Path.rename` raises FileExistsError on Windows when the target is
    already there -- so a user who migrated once, then restored a
    `session.json` (or pointed a fresh store at the same data dir), crashed
    the app on startup. `os.replace` overwrites instead."""
    legacy = tmp_path / "session.json"
    legacy.write_text(_json.dumps(_LEGACY_SESSION), encoding="utf-8")
    LeagueStore(tmp_path / "ffdo.db", legacy_session_path=legacy).list()
    assert (tmp_path / "session.json.migrated").exists()

    # Same legacy path again, but a fresh DB so the empty-table guard does not
    # short-circuit before the rename -- this is the call that used to raise.
    legacy.write_text(_json.dumps(_LEGACY_SESSION), encoding="utf-8")
    store2 = LeagueStore(tmp_path / "ffdo2.db", legacy_session_path=legacy)

    assert len(store2.list()) == 1
    assert not legacy.exists()
    assert (tmp_path / "session.json.migrated").exists()


def test_migration_carries_espn_credentials(tmp_path):
    legacy = tmp_path / "session.json"
    espn_session = {**_LEGACY_SESSION, "provider": "espn",
                    "league_id": "1882997948", "draft_id": "1882997948",
                    "draft_type": "snake", "espn_s2": "s2val", "swid": "{SWID}",
                    "username": ""}
    legacy.write_text(_json.dumps(espn_session), encoding="utf-8")

    store = LeagueStore(tmp_path / "ffdo.db", legacy_session_path=legacy)
    cred = store.get_credential("espn")
    assert cred is not None and cred.espn_s2 == "s2val" and cred.swid == "{SWID}"
    assert store.list()[0].league_key == "espn:1882997948:2026"


def test_no_migration_when_the_legacy_file_is_absent(tmp_path):
    store = LeagueStore(tmp_path / "ffdo.db", legacy_session_path=tmp_path / "nope.json")
    assert store.list() == []


def test_migration_carries_a_sleeper_username_credential(tmp_path):
    legacy = tmp_path / "session.json"
    legacy.write_text(_json.dumps(_LEGACY_SESSION), encoding="utf-8")

    store = LeagueStore(tmp_path / "ffdo.db", legacy_session_path=legacy)
    cred = store.get_credential("sleeper")
    assert cred is not None
    assert cred.user_identifier == "noahdschroeder"
    assert cred.espn_s2 is None and cred.swid is None


@pytest.mark.parametrize("payload", ["[]", "{}", '{"season": "not-a-number"}'])
def test_migration_never_raises_on_a_malformed_legacy_file(tmp_path, payload):
    legacy = tmp_path / "session.json"
    legacy.write_text(payload, encoding="utf-8")

    store = LeagueStore(tmp_path / "ffdo.db", legacy_session_path=legacy)
    assert store.list() == []
    # Un-migratable file is left in place, un-renamed, for the user to inspect.
    assert legacy.exists()
    assert not (tmp_path / "session.json.migrated").exists()
