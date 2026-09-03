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
